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
from typing import Literal, Optional
import numpy as np
import pandas as pd

import plotly.express as px
import plotly.graph_objects as go
from plotly.graph_objs._figure import Figure

import verticapy._config.config as conf

from verticapy.plotting._plotly.base import PlotlyBase


class ScatterMatrix(PlotlyBase):
    ...


class ScatterPlot(PlotlyBase):

    # Properties.

    @property
    def _category(self) -> Literal["plot"]:
        return "plot"

    @property
    def _kind(self) -> Literal["scatter"]:
        return "scatter"

    @property
    def _compute_method(self) -> Literal["sample"]:
        return "sample"

    # Styling Methods.

    def _init_style(self) -> None:
        self.init_style = {
            "width":700, "height":500, "autosize": False,
            "xaxis_title": self.layout["columns"][0][1:-1],
            "yaxis_title": self.layout["columns"][1][1:-1],
            "xaxis": dict(showline=True, linewidth=1, linecolor='black', mirror=True,zeroline= False),
            "yaxis": dict(showline=True, linewidth=1, linecolor='black', mirror=True,zeroline= False)
            }
        return None

    # Draw.

    def draw(
        self,
        catcol: str = "",
        **style_kwargs,
    ) -> Figure:
        """
        Draws a scatter plot using the Plotly API.
        """ 
        color_option={}
        data = np.column_stack((self.data['X'], self.data['c'])) if self.data['c'] is not None else self.data['X']
        column_names=self._format_col_names(self.layout['columns'])
        columns=column_names+[self.layout['c'][1:-1]] if self.layout['c'] is not None else column_names
        df=pd.DataFrame(data=data,columns=columns,)
        if self.layout['c']:
            color_option["color"]= self.layout['c'][1:-1]
        if self.data['X'].shape[1]<3:
            fig=px.scatter(df,x=column_names[0],y=column_names[1],**color_option,**style_kwargs)
            fig.update_layout(**self.init_style)
        elif self.data['X'].shape[1]==3:
            fig=px.scatter_3d(df,x=column_names[0],y=column_names[1],z=column_names[2],**color_option,**style_kwargs)
            fig.update_layout(scene = dict(xaxis_title=columns[0],
                    yaxis_title=columns[1],
                    zaxis_title=columns[2]),
                    scene_aspectmode='cube',
                    height=700,
                    autosize= False,
                    )
        return fig
