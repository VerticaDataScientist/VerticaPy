"""
(c)  Copyright  [2018-2023]  OpenText  or one of its
affiliates.  Licensed  under  the   Apache  License,
Version 2.0 (the  "License"); You  may  not use this
file except in compliance with the License.

You may obtain a copy of the License at:
http://www.apache.org/licenses/LICENSE-2.0

Unless  required  by applicable  law or  agreed to in
writing, software  distributed  under the  License is
distributed on an  "AS IS" BASIS,  WITHOUT WARRANTIES
OR CONDITIONS OF ANY KIND, either express or implied.
See the  License for the specific  language governing
permissions and limitations under the License.
"""
import itertools, warnings
from collections.abc import Iterable
from typing import Literal, Optional, Union
from vertica_python.errors import QueryError

from matplotlib.axes import Axes
import matplotlib.pyplot as plt

from verticapy._config.colors import get_colors
import verticapy._config.config as conf
from verticapy._typing import (
    ArrayLike,
    PythonNumber,
    PythonScalar,
    SQLColumns,
    SQLRelation,
)
from verticapy._utils._gen import gen_name, gen_tmp_name
from verticapy._utils._sql._collect import save_verticapy_logs
from verticapy._utils._sql._format import clean_query, quote_ident, schema_relation
from verticapy._utils._sql._sys import _executeSQL
from verticapy.errors import ParameterError

from verticapy.core.tablesample.base import TableSample
from verticapy.core.vdataframe.base import vDataFrame

from verticapy.plotting.base import PlottingBase
import verticapy.plotting._matplotlib as vpy_plt

import verticapy.machine_learning.metrics as mt
from verticapy.machine_learning.vertica.base import (
    MulticlassClassifier,
    Regressor,
    Tree,
    VerticaModel,
)
from verticapy.machine_learning.vertica.tree import DecisionTreeRegressor

from verticapy.sql.drop import drop


"""
Algorithms used for regression.
"""


class KNeighborsRegressor(Regressor):
    """
    [Beta Version]
    Creates a  KNeighborsRegressor object using the 
    k-nearest neighbors algorithm. This object uses 
    pure SQL to compute all the distances and final 
    score.

    \u26A0 Warning : This   algorithm   uses  a   CROSS  JOIN 
                     during   computation  and  is  therefore 
                     computationally  expensive at  O(n * n), 
                     where n is the total number of elements. 
                     Since  KNeighborsRegressor  uses  the p-
                     distance,  it  is  highly  sensitive  to 
                     unnormalized data.

    Parameters
    ----------
    n_neighbors: int, optional
        Number of neighbors to consider when computing 
        the score.
    p: int, optional
        The p corresponding to the one of the p-distances 
        (distance metric used during the model computation).
    """

    # Properties.

    @property
    def _is_native(self) -> Literal[False]:
        return False

    @property
    def _vertica_fit_sql(self) -> Literal[""]:
        return ""

    @property
    def _vertica_predict_sql(self) -> Literal[""]:
        return ""

    @property
    def _model_category(self) -> Literal["SUPERVISED"]:
        return "SUPERVISED"

    @property
    def _model_subcategory(self) -> Literal["REGRESSOR"]:
        return "REGRESSOR"

    @property
    def _model_type(self) -> Literal["KNeighborsRegressor"]:
        return "KNeighborsRegressor"

    @property
    def _attributes(self) -> list[str]:
        return ["n_neighbors_", "p_"]

    # System & Special Methods.

    @save_verticapy_logs
    def __init__(self, name: str, n_neighbors: int = 5, p: int = 2) -> None:
        self.model_name = name
        self.parameters = {"n_neighbors": n_neighbors, "p": p}
        return None

    def drop(self) -> bool:
        """
        KNN models are not stored in the Vertica DB.
        """
        return False

    # Attributes Methods.

    def _compute_attributes(self) -> None:
        self.p_ = self.parameters["p"]
        self.n_neighbors_ = self.parameters["n_neighbors"]
        return None

    # I/O Methods.

    def deploySQL(
        self, X: SQLColumns = [], test_relation: str = "", key_columns: SQLColumns = [],
    ) -> str:
        """
        Returns the SQL code needed to deploy the model. 

        Parameters
        ----------
        X: SQLColumns
            List of the predictors.
        test_relation: str, optional
            Relation to use to do the predictions.
        key_columns: SQLColumns, optional
            A  list  of columns  to  include in  the  results, 
            but to exclude from computation of the prediction.

        Returns
        -------
        str
            the SQL code needed to deploy the model.
        """
        if isinstance(X, str):
            X = [X]
        if isinstance(key_columns, str):
            key_columns = [key_columns]
        X = [quote_ident(elem) for elem in X] if (X) else self.X
        if not (test_relation):
            test_relation = self.test_relation
        if not (key_columns) and key_columns != None:
            key_columns = [self.y]
        p = self.parameters["p"]
        X_str = ", ".join([f"x.{x}" for x in X])
        if key_columns:
            key_columns_str = ", " + ", ".join(
                ["x." + quote_ident(x) for x in key_columns]
            )
        else:
            key_columns_str = ""
        sql = [f"POWER(ABS(x.{X[i]} - y.{self.X[i]}), {p})" for i in range(len(self.X))]
        sql = f"""
            SELECT 
                {X_str}{key_columns_str}, 
                ROW_NUMBER() OVER(PARTITION BY {X_str}, row_id 
                                  ORDER BY POWER({' + '.join(sql)}, 1 / {p})) 
                                  AS ordered_distance, 
                y.{self.y} AS predict_neighbors, 
                row_id 
            FROM
                (SELECT 
                    *, 
                    ROW_NUMBER() OVER() AS row_id 
                 FROM {test_relation} 
                 WHERE {" AND ".join([f"{x} IS NOT NULL" for x in X])}) x 
                 CROSS JOIN 
                 (SELECT 
                    * 
                 FROM {self.input_relation} 
                 WHERE {" AND ".join([f"{x} IS NOT NULL" for x in self.X])}) y"""
        if key_columns:
            key_columns_str = ", " + ", ".join([quote_ident(x) for x in key_columns])
        n_neighbors = self.parameters["n_neighbors"]
        sql = f"""
            (SELECT 
                {", ".join(X)}{key_columns_str}, 
                AVG(predict_neighbors) AS predict_neighbors 
             FROM ({sql}) z 
             WHERE ordered_distance <= {n_neighbors} 
             GROUP BY {", ".join(X)}{key_columns_str}, row_id) knr_table"""
        return clean_query(sql)

    # Prediction / Transformation Methods.

    def _predict(
        self,
        vdf: SQLRelation,
        X: SQLColumns = [],
        name: str = "",
        inplace: bool = True,
        **kwargs,
    ) -> vDataFrame:
        """
        Predicts using the input relation.
        """
        if isinstance(X, str):
            X = [X]
        if isinstance(vdf, str):
            vdf = vDataFrame(vdf)
        X = [quote_ident(elem) for elem in X] if (X) else self.X
        key_columns = vdf.get_columns(exclude_columns=X)
        if "key_columns" in kwargs:
            key_columns_arg = None
        else:
            key_columns_arg = key_columns
        if not (name):
            name = f"{self._model_type}_" + "".join(
                ch for ch in self.model_name if ch.isalnum()
            )
        if key_columns:
            key_columns_str = ", " + ", ".join(key_columns)
        else:
            key_columns_str = ""
        table = self.deploySQL(
            X=X, test_relation=vdf._genSQL(), key_columns=key_columns_arg
        )
        sql = f"""
            SELECT 
                {", ".join(X)}{key_columns_str}, 
                predict_neighbors AS {name} 
             FROM {table}"""
        if inplace:
            vdf.__init__(sql)
            return vdf
        else:
            return vDataFrame(sql)

    # Plotting Methods.

    def _get_plot_args(self, method: Optional[str] = None) -> list:
        """
        Returns the args used by plotting methods.
        """
        if method == "contour":
            args = [self.X, self]
        else:
            raise NotImplementedError
        return args


"""
Algorithms used for classification.
"""


class KNeighborsClassifier(MulticlassClassifier):
    """
    [Beta Version]
    Creates a KNeighborsClassifier object using the 
    k-nearest neighbors algorithm. This object uses 
    pure SQL to compute all the distances and final 
    score.

    \u26A0 Warning : This   algorithm   uses  a   CROSS  JOIN 
                     during   computation  and  is  therefore 
                     computationally  expensive at  O(n * n), 
                     where n is the total number of elements. 
                     Since  KNeighborsClassifier uses  the p-
                     distance,  it  is  highly  sensitive  to 
                     unnormalized data.

    Parameters
    ----------
    n_neighbors: int, optional
        Number  of neighbors to consider when computing  the 
        score.
    p: int, optional
        The  p corresponding  to the one of the  p-distances 
        (distance metric used during the model computation).
	"""

    # Properties.

    @property
    def _is_native(self) -> Literal[False]:
        return False

    @property
    def _vertica_fit_sql(self) -> Literal[""]:
        return ""

    @property
    def _vertica_predict_sql(self) -> Literal[""]:
        return ""

    @property
    def _model_category(self) -> Literal["SUPERVISED"]:
        return "SUPERVISED"

    @property
    def _model_subcategory(self) -> Literal["CLASSIFIER"]:
        return "CLASSIFIER"

    @property
    def _model_type(self) -> Literal["KNeighborsClassifier"]:
        return "KNeighborsClassifier"

    @property
    def _attributes(self) -> list[str]:
        return ["classes_", "n_neighbors_", "p_"]

    # System & Special Methods.

    @save_verticapy_logs
    def __init__(self, name: str, n_neighbors: int = 5, p: int = 2) -> None:
        self.model_name = name
        self.parameters = {"n_neighbors": n_neighbors, "p": p}
        return None

    def drop(self) -> bool:
        """
        KNN models are not stored in the Vertica DB.
        """
        return False

    # Attributes Methods.

    def _compute_attributes(self) -> None:
        """
        Computes the model's attributes.
        """
        self.classes_ = self._get_classes()
        self.p_ = self.parameters["p"]
        self.n_neighbors_ = self.parameters["n_neighbors"]
        return None

    # I/O Methods.

    def deploySQL(
        self,
        X: SQLColumns = [],
        test_relation: str = "",
        predict: bool = False,
        key_columns: SQLColumns = [],
    ) -> str:
        """
        Returns the SQL code needed to deploy the model. 

        Parameters
        ----------
        X: SQLColumns
            List of the predictors.
        test_relation: str, optional
            Relation to use to do the predictions.
        predict: bool, optional
            If set to True, returns the prediction instead 
            of the probability.
        key_columns: SQLColumns, optional
            A  list of columns to include in the  results, 
            but  to   exclude  from   computation  of  the 
            prediction.

        Returns
        -------
        SQLExpression
            the SQL code needed to deploy the model.
        """
        if isinstance(X, str):
            X = [X]
        if isinstance(key_columns, str):
            key_columns = [key_columns]
        X = [quote_ident(x) for x in X] if (X) else self.X
        if not (test_relation):
            test_relation = self.test_relation
        if not (key_columns) and key_columns != None:
            key_columns = [self.y]
        p = self.parameters["p"]
        n_neighbors = self.parameters["n_neighbors"]
        X_str = ", ".join([f"x.{x}" for x in X])
        if key_columns:
            key_columns_str = ", " + ", ".join(
                ["x." + quote_ident(x) for x in key_columns]
            )
        else:
            key_columns_str = ""
        sql = [f"POWER(ABS(x.{X[i]} - y.{self.X[i]}), {p})" for i in range(len(self.X))]
        sql = f"""
            SELECT 
                {X_str}{key_columns_str}, 
                ROW_NUMBER() OVER(PARTITION BY 
                                  {X_str}, row_id 
                                  ORDER BY POWER({' + '.join(sql)}, 1 / {p})) 
                                  AS ordered_distance, 
                y.{self.y} AS predict_neighbors, 
                row_id 
            FROM 
                (SELECT 
                    *, 
                    ROW_NUMBER() OVER() AS row_id 
                 FROM {test_relation} 
                 WHERE {" AND ".join([f"{x} IS NOT NULL" for x in X])}) x 
                 CROSS JOIN 
                (SELECT * FROM {self.input_relation} 
                 WHERE {" AND ".join([f"{x} IS NOT NULL" for x in self.X])}) y"""

        if key_columns:
            key_columns_str = ", " + ", ".join([quote_ident(x) for x in key_columns])

        sql = f"""
            (SELECT 
                row_id, 
                {", ".join(X)}{key_columns_str}, 
                predict_neighbors, 
                COUNT(*) / {n_neighbors} AS proba_predict 
             FROM ({sql}) z 
             WHERE ordered_distance <= {n_neighbors} 
             GROUP BY {", ".join(X)}{key_columns_str}, 
                      row_id, 
                      predict_neighbors) kneighbors_table"""
        if predict:
            sql = f"""
                (SELECT 
                    {", ".join(X)}{key_columns_str}, 
                    predict_neighbors 
                 FROM 
                    (SELECT 
                        {", ".join(X)}{key_columns_str}, 
                        predict_neighbors, 
                        ROW_NUMBER() OVER (PARTITION BY {", ".join(X)} 
                                           ORDER BY proba_predict DESC) 
                                           AS order_prediction 
                     FROM {sql}) VERTICAPY_SUBTABLE 
                     WHERE order_prediction = 1) predict_neighbors_table"""
        return clean_query(sql)

    # Prediction / Transformation Methods.

    def _get_final_relation(self, pos_label: PythonScalar = None,) -> str:
        """
        Returns the final relation used to do the predictions.
        """
        return f"""
            (SELECT 
                * 
            FROM {self.deploySQL()} 
            WHERE predict_neighbors = '{pos_label}') 
            final_centroids_relation"""

    def _get_y_proba(self, pos_label: PythonScalar = None,) -> str:
        """
        Returns the input which represents the model's probabilities.
        """
        return "proba_predict"

    def _get_y_score(
        self, pos_label: PythonScalar = None, cutoff: PythonNumber = 0.5,
    ) -> str:
        """
        Returns the input which represents the model's scoring.
        """
        return f"(CASE WHEN proba_predict > {cutoff} THEN 1 ELSE 0 END)"

    def _compute_accuracy(self) -> float:
        """
        Computes the model accuracy.
        """
        return mt.accuracy_score(
            self.y, "predict_neighbors", self.deploySQL(predict=True)
        )

    def _confusion_matrix(
        self, pos_label: PythonScalar = None, cutoff: PythonNumber = -1,
    ) -> TableSample:
        """
        Computes the model confusion matrix.
        """
        if pos_label == None:
            input_relation = f"""
                (SELECT 
                    *, 
                    ROW_NUMBER() OVER(PARTITION BY {", ".join(self.X)}, row_id 
                                      ORDER BY proba_predict DESC) AS pos 
                 FROM {self.deploySQL()}) neighbors_table WHERE pos = 1"""
            return mt.multilabel_confusion_matrix(
                self.y, "predict_neighbors", input_relation, self.classes_
            )
        else:
            pos_label = self._check_pos_label(pos_label=pos_label)
            input_relation = (
                self.deploySQL() + f" WHERE predict_neighbors = '{pos_label}'"
            )
            y_score = f"(CASE WHEN proba_predict > {cutoff} THEN 1 ELSE 0 END)"
            y_true = f"DECODE({self.y}, '{pos_label}', 1, 0)"
            return mt.confusion_matrix(y_true, y_score, input_relation)

    # Model Evaluation Methods.

    def _predict(
        self,
        vdf: SQLRelation,
        X: SQLColumns = [],
        name: str = "",
        cutoff: PythonNumber = 0.5,
        inplace: bool = True,
        **kwargs,
    ) -> vDataFrame:
        """
        Predicts using the input relation.
        """
        if isinstance(X, str):
            X = [X]
        assert 0 <= cutoff <= 1, ParameterError(
            "Incorrect parameter 'cutoff'.\nThe cutoff "
            "must be between 0 and 1, inclusive."
        )
        if isinstance(vdf, str):
            vdf = vDataFrame(vdf)
        X = [quote_ident(elem) for elem in X] if (X) else self.X
        key_columns = vdf.get_columns(exclude_columns=X)
        if "key_columns" in kwargs:
            key_columns_arg = None
        else:
            key_columns_arg = key_columns
        if key_columns:
            key_columns_str = ", " + ", ".join(key_columns)
        else:
            key_columns_str = ""
        if not (name):
            name = gen_name([self._model_type, self.model_name])

        if self._is_binary_classifier:
            table = self.deploySQL(
                X=X, test_relation=vdf._genSQL(), key_columns=key_columns_arg
            )
            sql = f"""
                (SELECT 
                    {", ".join(X)}{key_columns_str}, 
                    (CASE 
                        WHEN proba_predict > {cutoff} 
                            THEN '{self.classes_[1]}' 
                        ELSE '{self.classes_[0]}' 
                     END) AS {name} 
                 FROM {table} 
                 WHERE predict_neighbors = '{self.classes_[1]}') VERTICAPY_SUBTABLE"""
        else:
            table = self.deploySQL(
                X=X,
                test_relation=vdf._genSQL(),
                key_columns=key_columns_arg,
                predict=True,
            )
            sql = f"""
                SELECT 
                    {", ".join(X)}{key_columns_str}, 
                    predict_neighbors AS {name} 
                 FROM {table}"""
        if inplace:
            vdf.__init__(sql)
            return vdf
        else:
            return vDataFrame(sql)

    def _predict_proba(
        self,
        vdf: SQLRelation,
        X: SQLColumns = [],
        name: str = "",
        pos_label: PythonScalar = None,
        inplace: bool = True,
        **kwargs,
    ) -> vDataFrame:
        """
        Returns the model's probabilities using the 
        input relation.
        """
        # Inititalization
        if isinstance(X, str):
            X = [X]
        assert pos_label is None or pos_label in self.classes_, ParameterError(
            (
                "Incorrect parameter 'pos_label'.\nThe class label "
                f"must be in [{'|'.join([str(c) for c in self.classes_])}]. "
                f"Found '{pos_label}'."
            )
        )
        if isinstance(vdf, str):
            vdf = vDataFrame(vdf)
        X = [quote_ident(x) for x in X] if (X) else self.X
        key_columns = vdf.get_columns(exclude_columns=X)
        if not (name):
            name = gen_name([self._model_type, self.model_name])
        if "key_columns" in kwargs:
            key_columns_arg = None
        else:
            key_columns_arg = key_columns

        # Generating the probabilities
        if pos_label == None:
            predict = [
                f"""ZEROIFNULL(AVG(DECODE(predict_neighbors, 
                                          '{c}', 
                                          proba_predict, 
                                          NULL))) AS {gen_name([name, c])}"""
                for c in self.classes_
            ]
        else:
            predict = [
                f"""ZEROIFNULL(AVG(DECODE(predict_neighbors, 
                                          '{pos_label}', 
                                          proba_predict, 
                                          NULL))) AS {name}"""
            ]
        if key_columns:
            key_columns_str = ", " + ", ".join(key_columns)
        else:
            key_columns_str = ""
        table = self.deploySQL(
            X=X, test_relation=vdf._genSQL(), key_columns=key_columns_arg
        )
        sql = f"""
            SELECT 
                {", ".join(X)}{key_columns_str}, 
                {", ".join(predict)} 
             FROM {table} 
             GROUP BY {", ".join(X + key_columns)}"""

        # Result
        if inplace:
            vdf.__init__(sql)
            return vdf
        else:
            return vDataFrame(sql)

    # Plotting Methods.

    def _get_plot_args(
        self, pos_label: PythonScalar = None, method: Optional[str] = None
    ) -> list:
        """
        Returns the args used by plotting methods.
        """
        pos_label = self._check_pos_label(pos_label)
        if method == "contour":
            args = [self.X, self]
        else:
            input_relation = (
                self.deploySQL() + f" WHERE predict_neighbors = '{pos_label}'"
            )
            args = [self.y, "proba_predict", input_relation, pos_label]
        return args

    def _get_plot_kwargs(
        self,
        pos_label: PythonScalar = None,
        nbins: int = 30,
        ax: Optional[Axes] = None,
        method: Optional[str] = None,
    ) -> dict:
        """
        Returns the kwargs used by plotting methods.
        """
        pos_label = self._check_pos_label(pos_label)
        res = {"nbins": nbins, "ax": ax}
        if method == "contour":
            res["cbar_title"] = self.y
            res["pos_label"] = pos_label
        elif method == "cutoff":
            res["cutoff_curve"] = True
        return res


"""
Algorithms used for density analysis.
"""


class KernelDensity(Regressor, Tree):
    """
    [Beta Version]
    Creates a KernelDensity object. 
    This object uses pure SQL to compute the final score.

    Parameters
    ----------
    name: str
        Name of the model. This is not a built-in model, so 
        this name will be used  to build the final table.
    bandwidth: PythonNumber, optional
        The bandwidth of the kernel.
    kernel: str, optional
        The kernel used during the learning phase.
            gaussian  : Gaussian Kernel.
            logistic  : Logistic Kernel.
            sigmoid   : Sigmoid Kernel.
            silverman : Silverman Kernel.
    p: int, optional
        The  p corresponding to  the  one of the  p-distances 
        (distance metric used  during the model computation).
    max_leaf_nodes: PythonNumber, optional
        The maximum number of leaf nodes,  an integer between 
        1 and 1e9, inclusive.
    max_depth: int, optional
        The maximum tree depth,  an integer between 1 and 100, 
        inclusive.
    min_samples_leaf: int, optional
        The  minimum number of  samples each branch must  have 
        after splitting a node,  an integer between 1 and 1e6, 
        inclusive. A split that causes fewer remaining samples 
        is discarded.
    nbins: int, optional 
        The  number  of  bins to use to discretize  the  input 
        features.
    xlim: list, optional
        List of tuples use to compute the kernel window.
    """

    # Properties.

    @property
    def _is_native(self) -> Literal[False]:
        return False

    @property
    def _is_using_native(self) -> Literal[True]:
        return True

    @property
    def _vertica_fit_sql(self) -> Literal["RF_REGRESSOR"]:
        return "RF_REGRESSOR"

    @property
    def _vertica_predict_sql(self) -> Literal["PREDICT_RF_REGRESSOR"]:
        return "PREDICT_RF_REGRESSOR"

    @property
    def _model_category(self) -> Literal["UNSUPERVISED"]:
        return "UNSUPERVISED"

    @property
    def _model_subcategory(self) -> Literal["PREPROCESSING"]:
        return "PREPROCESSING"

    @property
    def _model_type(self) -> Literal["KernelDensity"]:
        return "KernelDensity"

    # System & Special Methods.

    @save_verticapy_logs
    def __init__(
        self,
        name: str,
        bandwidth: PythonNumber = 1.0,
        kernel: Literal["gaussian", "logistic", "sigmoid", "silverman"] = "gaussian",
        p: int = 2,
        max_leaf_nodes: PythonNumber = 1e9,
        max_depth: int = 5,
        min_samples_leaf: int = 1,
        nbins: int = 5,
        xlim: list = [],
        **kwargs,
    ) -> None:
        self.model_name = name
        self.parameters = {
            "nbins": nbins,
            "p": p,
            "bandwidth": bandwidth,
            "kernel": str(kernel).lower(),
            "max_leaf_nodes": int(max_leaf_nodes),
            "max_depth": int(max_depth),
            "min_samples_leaf": int(min_samples_leaf),
            "xlim": xlim,
        }
        if "store" not in kwargs or kwargs["store"]:
            self._verticapy_store = True
        else:
            self._verticapy_store = False
        return None

    def drop(self) -> bool:
        """
        Drops the model from the Vertica database.
        """
        try:
            if hasattr(self, "map"):
                _executeSQL(
                    query=f"SELECT KDE FROM {self.map} LIMIT 0;",
                    title="Looking if the KDE table exists.",
                )
                drop(self.map, method="table")
        except QueryError:
            return False
        return drop(self.model_name, method="model")

    # Attributes Methods.

    def _density_kde(
        self, vdf: vDataFrame, columns: SQLColumns, kernel: str, x, p: int, h=None
    ) -> str:
        """
        Returns the result of the KDE.
        """
        for col in columns:
            if not (vdf[col].isnum()):
                raise TypeError(
                    f"Cannot compute KDE for non-numerical columns. {col} is not numerical."
                )
        if kernel == "gaussian":
            fkernel = "EXP(-1 / 2 * POWER({0}, 2)) / SQRT(2 * PI())"

        elif kernel == "logistic":
            fkernel = "1 / (2 + EXP({0}) + EXP(-{0}))"

        elif kernel == "sigmoid":
            fkernel = "2 / (PI() * (EXP({0}) + EXP(-{0})))"

        elif kernel == "silverman":
            fkernel = (
                "EXP(-1 / SQRT(2) * ABS({0})) / 2 * SIN(ABS({0}) / SQRT(2) + PI() / 4)"
            )

        else:
            raise ParameterError(
                "The parameter 'kernel' must be in [gaussian|logistic|sigmoid|silverman]."
            )
        if isinstance(x, (tuple)):
            return self._density_kde(vdf, columns, kernel, [x], p, h)[0]
        elif isinstance(x, (list)):
            N = vdf.shape()[0]
            L = []
            for xj in x:
                distance = []
                for i in range(len(columns)):
                    distance += [f"POWER({columns[i]} - {xj[i]}, {p})"]
                distance = " + ".join(distance)
                distance = f"POWER({distance}, {1.0 / p})"
                fkernel_tmp = fkernel.format(f"{distance} / {h}")
                L += [f"SUM({fkernel_tmp}) / ({h} * {N})"]
            query = f"""
                SELECT 
                    /*+LABEL('learn.neighbors.KernelDensity.fit')*/ 
                    {", ".join(L)} 
                FROM {vdf._genSQL()}"""
            result = _executeSQL(
                query=query, title="Computing the KDE", method="fetchrow"
            )
            return list(result)
        else:
            return 0

    def _density_compute(
        self,
        vdf: vDataFrame,
        columns: SQLColumns,
        h=None,
        kernel: str = "gaussian",
        nbins: int = 5,
        p: int = 2,
    ) -> list:
        """
        Returns the result of the KDE for all the data points.
        """
        columns = vdf._format_colnames(columns)
        x_vars = []
        y = []
        for idx, column in enumerate(columns):
            if self.parameters["xlim"]:
                try:
                    x_min, x_max = self.parameters["xlim"][idx]
                    N = vdf[column].count()
                except:
                    warning_message = (
                        f"Wrong xlim for the vDataColumn {column}.\n"
                        "The max and the min will be used instead."
                    )
                    warnings.warn(warning_message, Warning)
                    x_min, x_max, N = vdf.agg(
                        func=["min", "max", "count"], columns=[column]
                    ).transpose()[column]
            else:
                x_min, x_max, N = vdf.agg(
                    func=["min", "max", "count"], columns=[column]
                ).transpose()[column]
            x_vars += [
                [(x_max - x_min) * i / nbins + x_min for i in range(0, nbins + 1)]
            ]
        x = list(itertools.product(*x_vars))
        try:
            y = self._density_kde(vdf, columns, kernel, x, p, h)
        except:
            for xi in x:
                K = self._density_kde(vdf, columns, kernel, xi, p, h)
                y += [K]
        return [x, y]

    # Model Fitting Method.

    def fit(self, input_relation: SQLRelation, X: SQLColumns = []) -> None:
        """
        Trains the model.

        Parameters
        ----------
        input_relation: SQLRelation
            Training relation.
        X: list, optional
            List of the predictors.
        """
        if isinstance(X, str):
            X = [X]
        if conf.get_option("overwrite_model"):
            self.drop()
        else:
            self._is_already_stored(raise_error=True)
        if isinstance(input_relation, vDataFrame):
            if not (X):
                X = input_relation.numcol()
            vdf = input_relation
            input_relation = input_relation._genSQL()
        else:
            try:
                vdf = vDataFrame(input_relation)
            except:
                vdf = vDataFrame(input_relation)
            if not (X):
                X = vdf.numcol()
        X = vdf._format_colnames(X)
        x, y = self._density_compute(
            vdf,
            X,
            self.parameters["bandwidth"],
            self.parameters["kernel"],
            self.parameters["nbins"],
            self.parameters["p"],
        )
        table_name = self.model_name.replace('"', "") + "_KernelDensity_Map"
        if self._verticapy_store:
            _executeSQL(
                query=f"""
                    CREATE TABLE {table_name} AS    
                        SELECT 
                            /*+LABEL('learn.neighbors.KernelDensity.fit')*/
                            {", ".join(X)}, 0.0::float AS KDE 
                        FROM {vdf._genSQL()} 
                        LIMIT 0""",
                print_time_sql=False,
            )
            r, idx = 0, 0
            while r < len(y):
                values = []
                m = min(r + 100, len(y))
                for i in range(r, m):
                    values += ["SELECT " + str(x[i] + (y[i],))[1:-1]]
                _executeSQL(
                    query=f"""
                    INSERT /*+LABEL('learn.neighbors.KernelDensity.fit')*/ 
                    INTO {table_name}
                    ({", ".join(X)}, KDE) {" UNION ".join(values)}""",
                    title=f"Computing the KDE [Step {idx}].",
                )
                _executeSQL("COMMIT;", print_time_sql=False)
                r += 100
                idx += 1
            self.X, self.input_relation = X, input_relation
            self.map = table_name
            self.y = "KDE"
            model = DecisionTreeRegressor(
                name=self.model_name,
                max_features=len(self.X),
                max_leaf_nodes=self.parameters["max_leaf_nodes"],
                max_depth=self.parameters["max_depth"],
                min_samples_leaf=self.parameters["min_samples_leaf"],
                nbins=1000,
            )
            model.fit(self.map, self.X, "KDE")
        else:
            self.X, self.input_relation = X, input_relation
            self.verticapy_x = x
            self.verticapy_y = y
        return None

    # Plotting Methods.

    def plot(self, ax: Optional[Axes] = None, **style_kwds) -> Axes:
        """
        Draws the Model.

        Parameters
        ----------
        ax: Axes, optional
            The axes to plot on.
        **style_kwds
            Any optional parameter to pass to the 
            Matplotlib functions.

        Returns
        -------
        Axes
            Axes.
        """
        if len(self.X) == 1:
            if self._verticapy_store:
                query = f"""
                    SELECT 
                        /*+LABEL('learn.neighbors.KernelDensity.plot')*/ 
                        {self.X[0]}, KDE 
                    FROM {self.map} ORDER BY 1"""
                result = _executeSQL(query, method="fetchall", print_time_sql=False)
                x, y = [v[0] for v in result], [v[1] for v in result]
            else:
                x, y = [v[0] for v in self.verticapy_x], self.verticapy_y
            if not (ax):
                fig, ax = plt.subplots()
                if conf._get_import_success("jupyter"):
                    fig.set_size_inches(7, 5)
                ax.grid()
                ax.set_axisbelow(True)
            param = {
                "color": get_colors()[0],
            }
            ax.plot(x, y, **PlottingBase.updated_dict(param, style_kwds))
            ax.fill_between(
                x,
                y,
                facecolor=PlottingBase.updated_dict(param, style_kwds)["color"],
                alpha=0.7,
            )
            ax.set_xlim(min(x), max(x))
            ax.set_ylim(bottom=0)
            ax.set_ylabel("density")
            return ax
        elif len(self.X) == 2:
            n = self.parameters["nbins"]
            if self._verticapy_store:
                query = f"""
                    SELECT 
                        /*+LABEL('learn.neighbors.KernelDensity.plot')*/ 
                        {self.X[0]}, 
                        {self.X[1]}, 
                        KDE 
                    FROM {self.map} 
                    ORDER BY 1, 2"""
                result = _executeSQL(query, method="fetchall", print_time_sql=False)
                x, y, z = (
                    [v[0] for v in result],
                    [v[1] for v in result],
                    [v[2] for v in result],
                )
            else:
                x, y, z = (
                    [v[0] for v in self.verticapy_x],
                    [v[1] for v in self.verticapy_x],
                    self.verticapy_y,
                )
            result, idx = [], 0
            while idx < (n + 1) * (n + 1):
                result += [[z[idx + i] for i in range(n + 1)]]
                idx += n + 1
            if not (ax):
                fig, ax = plt.subplots()
                if conf._get_import_success("jupyter"):
                    fig.set_size_inches(8, 6)
            else:
                fig = plt
            param = {
                "cmap": "Reds",
                "origin": "lower",
                "interpolation": "bilinear",
            }
            extent = [min(x), max(x), min(y), max(y)]
            extent = [float(v) for v in extent]
            im = ax.imshow(
                result, extent=extent, **PlottingBase.updated_dict(param, style_kwds)
            )
            fig.colorbar(im, ax=ax)
            ax.set_ylabel(self.X[1])
            ax.set_xlabel(self.X[0])
            return ax
        else:
            raise AttributeError("KDE Plots are only available in 1D or 2D.")


"""
Algorithms used for anomaly detection.
"""


class LocalOutlierFactor(VerticaModel):
    """
    [Beta Version]
    Creates a LocalOutlierFactor object by using the 
    Local Outlier Factor algorithm as defined by Markus 
    M. Breunig, Hans-Peter Kriegel, Raymond T. Ng and Jörg 
    Sander. This object is using pure SQL to compute all 
    the distances and final score.

    \u26A0 Warning : This   algorithm   uses  a   CROSS  JOIN 
                     during   computation  and  is  therefore 
                     computationally  expensive at  O(n * n), 
                     where n is the total number of elements. 
                     Since  LocalOutlierFactor   uses  the p-
                     distance,  it  is  highly  sensitive  to 
                     unnormalized data. 
                     A  table  will be created at the  end of 
                     the learning phase.

    Parameters
    ----------
    name: str
    	Name  of the  model.  This is not a  built-in 
        model, so this name will be used to build the 
        final table.
    n_neighbors: int, optional
    	Number of neighbors to consider when computing 
        the score.
    p: int, optional
    	The p of the p-distances (distance metric used 
        during the model computation).
	"""

    # Properties.

    @property
    def _is_native(self) -> Literal[False]:
        return False

    @property
    def _vertica_fit_sql(self) -> Literal[""]:
        return ""

    @property
    def _vertica_predict_sql(self) -> Literal[""]:
        return ""

    @property
    def _model_category(self) -> Literal["UNSUPERVISED"]:
        return "UNSUPERVISED"

    @property
    def _model_subcategory(self) -> Literal["ANOMALY_DETECTION"]:
        return "ANOMALY_DETECTION"

    @property
    def _model_type(self) -> Literal["LocalOutlierFactor"]:
        return "LocalOutlierFactor"

    @property
    def _attributes(self) -> list[str]:
        return ["n_neighbors_", "p_", "n_errors_", "cnt_"]

    # System & Special Methods.

    @save_verticapy_logs
    def __init__(self, name: str, n_neighbors: int = 20, p: int = 2) -> None:
        self.model_name = name
        self.parameters = {"n_neighbors": n_neighbors, "p": p}
        return None

    def drop(self) -> bool:
        """
        Drops the model from the Vertica database.
        """
        try:
            _executeSQL(
                query=f"SELECT lof_score FROM {self.model_name} LIMIT 0;",
                title="Looking if the LOF table exists.",
            )
            return drop(self.model_name, method="table")
        except QueryError:
            return False

    # Attributes Methods.

    def _compute_attributes(self) -> None:
        """
        Computes the model's attributes.
        """
        self.p_ = self.parameters["p"]
        self.n_neighbors_ = self.parameters["n_neighbors"]
        self.cnt_ = _executeSQL(
            query=f"SELECT /*+LABEL('learn.VerticaModel.plot')*/ COUNT(*) FROM {self.model_name}",
            method="fetchfirstelem",
            print_time_sql=False,
        )
        return None

    # Model Fitting Method.

    def fit(
        self,
        input_relation: SQLRelation,
        X: SQLColumns = [],
        key_columns: SQLColumns = [],
        index: str = "",
    ) -> None:
        """
    	Trains the model.

    	Parameters
    	----------
    	input_relation: SQLRelation
    		Training relation.
    	X: SQLColumns, optional
    		List of the predictors.
    	key_columns: SQLColumns, optional
    		Columns  not  used   during  the   algorithm 
            computation  but   which  will  be  used  to 
            create the final relation.
    	index: str, optional
    		Index  used to identify each row  separately. 
            It is highly  recommanded to have one already 
            in the main table to avoid creating temporary 
            tables.
		"""
        if isinstance(X, str):
            X = [X]
        if isinstance(key_columns, str):
            key_columns = [key_columns]
        if conf.get_option("overwrite_model"):
            self.drop()
        else:
            self._is_already_stored(raise_error=True)
        self.key_columns = [quote_ident(column) for column in key_columns]
        if isinstance(input_relation, vDataFrame):
            self.input_relation = input_relation._genSQL()
            if not (X):
                X = input_relation.numcol()
        else:
            self.input_relation = input_relation
            if not (X):
                X = vDataFrame(input_relation).numcol()
        X = [quote_ident(column) for column in X]
        self.X = X
        n_neighbors = self.parameters["n_neighbors"]
        p = self.parameters["p"]
        schema, relation = schema_relation(input_relation)
        tmp_main_table_name = gen_tmp_name(name="main")
        tmp_distance_table_name = gen_tmp_name(name="distance")
        tmp_lrd_table_name = gen_tmp_name(name="lrd")
        tmp_lof_table_name = gen_tmp_name(name="lof")
        try:
            if not (index):
                index = "id"
                main_table = tmp_main_table_name
                schema = "v_temp_schema"
                drop(f"v_temp_schema.{tmp_main_table_name}", method="table")
                _executeSQL(
                    query=f"""
                        CREATE LOCAL TEMPORARY TABLE {main_table} 
                        ON COMMIT PRESERVE ROWS AS 
                            SELECT 
                                /*+LABEL('learn.neighbors.LocalOutlierFactor.fit')*/ 
                                ROW_NUMBER() OVER() AS id, 
                                {', '.join(X + key_columns)} 
                            FROM {self.input_relation} 
                            WHERE {' AND '.join([f"{x} IS NOT NULL" for x in X])}""",
                    print_time_sql=False,
                )
            else:
                main_table = self.input_relation
            sql = [f"POWER(ABS(x.{X[i]} - y.{X[i]}), {p})" for i in range(len(X))]
            distance = f"POWER({' + '.join(sql)}, 1 / {p})"
            drop(f"v_temp_schema.{tmp_distance_table_name}", method="table")
            _executeSQL(
                query=f"""
                    CREATE LOCAL TEMPORARY TABLE {tmp_distance_table_name} 
                    ON COMMIT PRESERVE ROWS AS 
                        SELECT 
                            /*+LABEL('learn.neighbors.LocalOutlierFactor.fit')*/ 
                            node_id, 
                            nn_id, 
                            distance, 
                            knn 
                        FROM 
                            (SELECT 
                                x.{index} AS node_id, 
                                y.{index} AS nn_id, 
                                {distance} AS distance, 
                                ROW_NUMBER() OVER(PARTITION BY x.{index} 
                                                  ORDER BY {distance}) AS knn 
                             FROM {schema}.{main_table} AS x 
                             CROSS JOIN 
                             {schema}.{main_table} AS y) distance_table 
                        WHERE knn <= {n_neighbors + 1}""",
                title="Computing the LOF [Step 0].",
            )
            drop(f"v_temp_schema.{tmp_lrd_table_name}", method="table")
            _executeSQL(
                query=f"""
                    CREATE LOCAL TEMPORARY TABLE {tmp_lrd_table_name} 
                    ON COMMIT PRESERVE ROWS AS 
                        SELECT 
                            /*+LABEL('learn.neighbors.LocalOutlierFactor.fit')*/ 
                            distance_table.node_id, 
                            {n_neighbors} / SUM(
                                    CASE 
                                        WHEN distance_table.distance 
                                             > kdistance_table.distance 
                                        THEN distance_table.distance 
                                        ELSE kdistance_table.distance 
                                     END) AS lrd 
                        FROM 
                            (v_temp_schema.{tmp_distance_table_name} AS distance_table 
                             LEFT JOIN 
                             (SELECT 
                                 node_id, 
                                 nn_id, 
                                 distance AS distance 
                              FROM v_temp_schema.{tmp_distance_table_name} 
                              WHERE knn = {n_neighbors + 1}) AS kdistance_table
                             ON distance_table.nn_id = kdistance_table.node_id) x 
                        GROUP BY 1""",
                title="Computing the LOF [Step 1].",
            )
            drop(f"v_temp_schema.{tmp_lof_table_name}", method="table")
            _executeSQL(
                query=f"""
                    CREATE LOCAL TEMPORARY TABLE {tmp_lof_table_name} 
                    ON COMMIT PRESERVE ROWS AS 
                    SELECT 
                        /*+LABEL('learn.neighbors.LocalOutlierFactor.fit')*/ 
                        x.node_id, 
                        SUM(y.lrd) / (MAX(x.node_lrd) * {n_neighbors}) AS LOF 
                    FROM 
                        (SELECT 
                            n_table.node_id, 
                            n_table.nn_id, 
                            lrd_table.lrd AS node_lrd 
                         FROM 
                            v_temp_schema.{tmp_distance_table_name} AS n_table 
                         LEFT JOIN 
                            v_temp_schema.{tmp_lrd_table_name} AS lrd_table 
                        ON n_table.node_id = lrd_table.node_id) x 
                    LEFT JOIN 
                        v_temp_schema.{tmp_lrd_table_name} AS y 
                    ON x.nn_id = y.node_id GROUP BY 1""",
                title="Computing the LOF [Step 2].",
            )
            _executeSQL(
                query=f"""
                    CREATE TABLE {self.model_name} AS 
                        SELECT 
                            /*+LABEL('learn.neighbors.LocalOutlierFactor.fit')*/ 
                            {', '.join(X + self.key_columns)}, 
                            (CASE WHEN lof > 1e100 OR lof != lof THEN 0 ELSE lof END) AS lof_score
                        FROM 
                            {main_table} AS x 
                        LEFT JOIN 
                            v_temp_schema.{tmp_lof_table_name} AS y 
                        ON x.{index} = y.node_id""",
                title="Computing the LOF [Step 3].",
            )
            self.n_errors_ = _executeSQL(
                query=f"""
                    SELECT 
                        /*+LABEL('learn.neighbors.LocalOutlierFactor.fit')*/ 
                        COUNT(*) 
                    FROM {schema}.{tmp_lof_table_name} z 
                    WHERE lof > 1e100 OR lof != lof""",
                method="fetchfirstelem",
                print_time_sql=False,
            )
            self._compute_attributes()
        finally:
            drop(f"v_temp_schema.{tmp_main_table_name}", method="table")
            drop(f"v_temp_schema.{tmp_distance_table_name}", method="table")
            drop(f"v_temp_schema.{tmp_lrd_table_name}", method="table")
            drop(f"v_temp_schema.{tmp_lof_table_name}", method="table")
        return None

    # Prediction / Transformation Methods.

    def predict(self) -> vDataFrame:
        """
        Creates a vDataFrame of the model.

        Returns
        -------
        vDataFrame
            the vDataFrame including the prediction.
        """
        return vDataFrame(self.model_name)

    # Plotting Methods.

    def plot(
        self, max_nb_points: int = 100, ax: Optional[Axes] = None, **style_kwds
    ) -> Axes:
        """
        Draws the model.

        Parameters
        ----------
        max_nb_points: int
            Maximum  number of points to display.
        ax: Axes, optional
            The axes to plot on.
        **style_kwds
            Any optional parameter to pass to the 
            Matplotlib functions.

        Returns
        -------
        Axes
            Axes.
        """
        sample = 100 * min(float(max_nb_points / self.cnt_), 1)
        return vpy_plt.LOFPlot().lof_plot(
            self.model_name, self.X, "lof_score", sample, ax=ax, **style_kwds
        )