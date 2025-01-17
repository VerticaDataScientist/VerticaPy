"""
Copyright  (c)  2018-2023 Open Text  or  one  of its
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

# Pytest
import pytest

# Other Modules
import matplotlib.pyplot as plt

# VerticaPy
from verticapy import (
    vDataFrame,
    drop,
    set_option,
)
from verticapy.connection import current_cursor
from verticapy.datasets import load_titanic, load_winequality, load_dataset_reg
from verticapy.learn.ensemble import RandomForestRegressor

set_option("print_info", False)


@pytest.fixture(scope="module")
def winequality_vd():
    winequality = load_winequality()
    yield winequality
    drop(
        name="public.winequality",
    )


@pytest.fixture(scope="module")
def titanic_vd():
    titanic = load_titanic()
    yield titanic
    drop(
        name="public.titanic",
    )


@pytest.fixture(scope="module")
def rfr_data_vd():
    rfr_data = load_dataset_reg(table_name="rfr_data", schema="public")
    yield rfr_data
    drop(name="public.rfr_data", method="table")


@pytest.fixture(scope="module")
def model(rfr_data_vd):
    current_cursor().execute("DROP MODEL IF EXISTS rfr_model_test")

    current_cursor().execute(
        "SELECT rf_regressor('rfr_model_test', 'public.rfr_data', 'TransPortation', '*' USING PARAMETERS exclude_columns='id, transportation', mtry=4, ntree=3, max_breadth=100, sampling_size=1, max_depth=6, min_leaf_size=1, min_info_gain=0.0, nbins=40, seed=1, id_column='id')"
    )

    # I could use load_model but it is buggy
    model_class = RandomForestRegressor(
        "rfr_model_test",
        n_estimators=3,
        max_features=4,
        max_leaf_nodes=100,
        sample=1.0,
        max_depth=6,
        min_samples_leaf=1,
        min_info_gain=0.0,
        nbins=40,
    )
    model_class.input_relation = "public.rfr_data"
    model_class.test_relation = model_class.input_relation
    model_class.X = ['"Gender"', '"owned cars"', '"cost"', '"income"']
    model_class.y = '"TransPortation"'
    model_class._compute_attributes()

    yield model_class
    model_class.drop()


class TestRFR:
    def test_repr(self, model):
        assert model.__repr__() == "<RandomForestRegressor>"

    def test_contour(self, titanic_vd):
        model_test = RandomForestRegressor(
            "model_contour",
        )
        model_test.drop()
        model_test.fit(
            titanic_vd,
            ["age", "fare"],
            "survived",
        )
        result = model_test.contour()
        assert len(result.get_default_bbox_extra_artists()) == 38
        model_test.drop()

    def test_deploySQL(self, model):
        expected_sql = 'PREDICT_RF_REGRESSOR("Gender", "owned cars", "cost", "income" USING PARAMETERS model_name = \'rfr_model_test\', match_by_pos = \'true\')'
        result_sql = model.deploySQL()

        assert result_sql == expected_sql

    def test_drop(self):
        current_cursor().execute("DROP MODEL IF EXISTS rfr_model_test_drop")
        model_test = RandomForestRegressor(
            "rfr_model_test_drop",
        )
        model_test.fit(
            "public.rfr_data",
            ["Gender", '"owned cars"', "cost", "income"],
            "TransPortation",
        )

        current_cursor().execute(
            "SELECT model_name FROM models WHERE model_name = 'rfr_model_test_drop'"
        )
        assert current_cursor().fetchone()[0] == "rfr_model_test_drop"

        model_test.drop()
        current_cursor().execute(
            "SELECT model_name FROM models WHERE model_name = 'rfr_model_test_drop'"
        )
        assert current_cursor().fetchone() is None

    def test_features_importance(self, model):
        fim = model.features_importance(show=False)

        assert fim["index"] == ["cost", "owned cars", "gender", "income"]
        assert fim["importance"] == [88.41, 7.25, 4.35, 0.0]
        assert fim["sign"] == [1, 1, 1, 0]
        plt.close("all")

    def test_get_score(self, model):
        fim = model.get_score()

        assert fim["predictor_name"] == ["gender", "owned cars", "cost", "income"]
        assert fim["importance_value"] == [
            pytest.approx(0.0434782608695652),
            pytest.approx(0.072463768115942),
            pytest.approx(0.884057971014493),
            pytest.approx(0.0),
        ]

    def test_get_vertica_attributes(self, model):
        m_att = model.get_vertica_attributes()

        assert m_att["attr_name"] == [
            "tree_count",
            "rejected_row_count",
            "accepted_row_count",
            "call_string",
            "details",
        ]
        assert m_att["attr_fields"] == [
            "tree_count",
            "rejected_row_count",
            "accepted_row_count",
            "call_string",
            "predictor, type",
        ]
        assert m_att["#_of_rows"] == [1, 1, 1, 1, 4]

        m_att_details = model.get_vertica_attributes(attr_name="details")

        assert m_att_details["predictor"] == [
            "gender",
            "owned cars",
            "cost",
            "income",
        ]
        assert m_att_details["type"] == [
            "char or varchar",
            "int",
            "char or varchar",
            "char or varchar",
        ]

        assert model.get_vertica_attributes("tree_count")["tree_count"][0] == 3
        assert (
            model.get_vertica_attributes("rejected_row_count")["rejected_row_count"][0]
            == 0
        )
        assert (
            model.get_vertica_attributes("accepted_row_count")["accepted_row_count"][0]
            == 10
        )
        assert (
            model.get_vertica_attributes("call_string")["call_string"][0]
            == "SELECT rf_regressor('public.rfr_model_test', 'public.rfr_data', 'transportation', '*' USING PARAMETERS exclude_columns='id, transportation', ntree=3, mtry=4, sampling_size=1, max_depth=6, max_breadth=100, min_leaf_size=1, min_info_gain=0, nbins=40);"
        )

    def test_get_params(self, model):
        assert model.get_params() == {
            "n_estimators": 3,
            "max_features": 4,
            "max_leaf_nodes": 100,
            "sample": 1,
            "max_depth": 6,
            "min_samples_leaf": 1,
            "min_info_gain": 0,
            "nbins": 40,
        }

    def test_get_plot(self, winequality_vd):
        current_cursor().execute("DROP MODEL IF EXISTS model_test_plot")
        model_test = RandomForestRegressor(
            "model_test_plot",
        )
        model_test.fit(winequality_vd, ["alcohol"], "quality")
        result = model_test.plot()
        assert len(result.get_default_bbox_extra_artists()) == 9
        plt.close("all")
        model_test.drop()

    def test_to_python(self, model):
        current_cursor().execute(
            "SELECT PREDICT_RF_REGRESSOR('Male', 0, 'Cheap', 'Low' USING PARAMETERS model_name = '{}', match_by_pos=True)::float".format(
                model.model_name
            )
        )
        prediction = current_cursor().fetchone()[0]
        assert prediction == pytest.approx(
            model.to_python()([["Male", 0, "Cheap", "Low"]])[0]
        )

    def test_to_sql(self, model):
        current_cursor().execute(
            "SELECT PREDICT_RF_REGRESSOR(* USING PARAMETERS model_name = '{}', match_by_pos=True)::float, {}::float FROM (SELECT 'Male' AS \"Gender\", 0 AS \"owned cars\", 'Cheap' AS \"cost\", 'Low' AS \"income\") x".format(
                model.model_name, model.to_sql()
            )
        )
        prediction = current_cursor().fetchone()
        assert prediction[0] == pytest.approx(prediction[1])

    def test_to_memmodel(self, model):
        mmodel = model.to_memmodel()
        res = mmodel.predict(
            [["Male", 0, "Cheap", "Low"], ["Female", 1, "Expensive", "Low"]]
        )
        res_py = model.to_python()(
            [["Male", 0, "Cheap", "Low"], ["Female", 1, "Expensive", "Low"]]
        )
        assert res[0] == res_py[0]
        assert res[1] == res_py[1]
        vdf = vDataFrame("public.rfr_data")
        vdf["prediction_sql"] = mmodel.predict_sql(
            ['"Gender"', '"owned cars"', '"cost"', '"income"']
        )
        model.predict(vdf, name="prediction_vertica_sql")
        score = vdf.score("prediction_sql", "prediction_vertica_sql", metric="r2")
        assert score == pytest.approx(1.0)

    def test_get_predicts(self, rfr_data_vd, model):
        rfr_data_copy = rfr_data_vd.copy()
        model.predict(
            rfr_data_copy,
            X=["Gender", '"owned cars"', "cost", "income"],
            name="predicted_quality",
        )

        assert rfr_data_copy["predicted_quality"].mean() == pytest.approx(0.9, abs=1e-6)

    def test_regression_report(self, model):
        reg_rep = model.regression_report()

        assert reg_rep["index"] == [
            "explained_variance",
            "max_error",
            "median_absolute_error",
            "mean_absolute_error",
            "mean_squared_error",
            "root_mean_squared_error",
            "r2",
            "r2_adj",
            "aic",
            "bic",
        ]
        assert reg_rep["value"][0] == pytest.approx(1.0, abs=1e-6)
        assert reg_rep["value"][1] == pytest.approx(0.0, abs=1e-6)
        assert reg_rep["value"][2] == pytest.approx(0.0, abs=1e-6)
        assert reg_rep["value"][3] == pytest.approx(0.0, abs=1e-6)
        assert reg_rep["value"][4] == pytest.approx(0.0, abs=1e-6)
        assert reg_rep["value"][5] == pytest.approx(0.0, abs=1e-6)
        assert reg_rep["value"][6] == pytest.approx(1.0, abs=1e-6)
        assert reg_rep["value"][7] == pytest.approx(1.0, abs=1e-6)
        assert reg_rep["value"][8] == pytest.approx(-float("inf"), abs=1e-6)
        assert reg_rep["value"][9] == pytest.approx(-float("inf"), abs=1e-6)

        reg_rep_details = model.regression_report(metrics="details")
        assert reg_rep_details["value"][2:] == [
            10.0,
            4,
            pytest.approx(1.0),
            pytest.approx(1.0),
            float("inf"),
            pytest.approx(0.0),
            pytest.approx(-1.73372940858763),
            pytest.approx(0.223450528977454),
            pytest.approx(3.76564442746721),
        ]

        reg_rep_anova = model.regression_report(metrics="anova")
        assert reg_rep_anova["SS"] == [
            pytest.approx(6.9),
            pytest.approx(0.0),
            pytest.approx(6.9),
        ]
        assert reg_rep_anova["MS"][:-1] == [
            pytest.approx(1.725),
            pytest.approx(0.0),
        ]

    def test_score(self, model):
        # method = "max"
        assert model.score(metric="max") == pytest.approx(0, abs=1e-6)
        # method = "mae"
        assert model.score(metric="mae") == pytest.approx(0, abs=1e-6)
        # method = "median"
        assert model.score(metric="median") == pytest.approx(0, abs=1e-6)
        # method = "mse"
        assert model.score(metric="mse") == pytest.approx(0.0, abs=1e-6)
        # method = "rmse"
        assert model.score(metric="rmse") == pytest.approx(0.0, abs=1e-6)
        # method = "msl"
        assert model.score(metric="msle") == pytest.approx(0.0, abs=1e-6)
        # method = "r2"
        assert model.score() == pytest.approx(1.0, abs=1e-6)
        # method = "r2a"
        assert model.score(metric="r2a") == pytest.approx(1.0, abs=1e-6)
        # method = "var"
        assert model.score(metric="var") == pytest.approx(1.0, abs=1e-6)
        # method = "aic"
        assert model.score(metric="aic") == pytest.approx(-float("inf"), abs=1e-6)
        # method = "bic"
        assert model.score(metric="bic") == pytest.approx(-float("inf"), abs=1e-6)

    def test_set_params(self, model):
        model.set_params({"max_features": 1000})

        assert model.get_params()["max_features"] == 1000

    def test_model_from_vDF(self, rfr_data_vd):
        current_cursor().execute("DROP MODEL IF EXISTS rfr_from_vDF")
        model_test = RandomForestRegressor(
            "rfr_from_vDF",
        )
        model_test.fit(rfr_data_vd, ["gender"], "transportation")

        current_cursor().execute(
            "SELECT model_name FROM models WHERE model_name = 'rfr_from_vDF'"
        )
        assert current_cursor().fetchone()[0] == "rfr_from_vDF"

        model_test.drop()

    def test_to_graphviz(self, model):
        gvz_tree_1 = model.to_graphviz(
            tree_id=1,
            classes_color=["red", "blue", "green"],
            round_pred=4,
            percent=True,
            vertical=False,
            node_style={"shape": "box", "style": "filled"},
            arrow_style={"color": "blue"},
            leaf_style={"shape": "circle", "style": "filled"},
        )
        assert 'digraph Tree{\ngraph [rankdir = "LR"];\n0' in gvz_tree_1
        assert "0 -> 1" in gvz_tree_1

    def test_get_tree(self, model):
        tree_1 = model.get_tree(tree_id=1)

        assert tree_1["prediction"] == [
            None,
            "2.000000",
            None,
            None,
            "1.000000",
            None,
            "0.000000",
            "0.000000",
            "1.000000",
        ]

    def test_plot_tree(self, model):
        result = model.plot_tree()
        assert model.to_graphviz() == result.source.strip()

    def test_optional_name(self):
        model = RandomForestRegressor()
        assert model.model_name is not None
