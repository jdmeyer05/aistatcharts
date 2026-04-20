declare module "react-plotly.js" {
  import { Component } from "react";
  import Plotly from "plotly.js";

  export interface PlotParams {
    data: Plotly.Data[];
    layout?: Partial<Plotly.Layout>;
    config?: Partial<Plotly.Config>;
    style?: React.CSSProperties;
    className?: string;
    onInitialized?: (figure: Plotly.Figure, graphDiv: HTMLElement) => void;
    onUpdate?: (figure: Plotly.Figure, graphDiv: HTMLElement) => void;
    onPurge?: (figure: Plotly.Figure, graphDiv: HTMLElement) => void;
    onError?: (err: Error) => void;
    onClick?: (event: Plotly.PlotMouseEvent) => void;
    onHover?: (event: Plotly.PlotHoverEvent) => void;
    onRelayout?: (event: Plotly.PlotRelayoutEvent) => void;
  }

  export default class Plot extends Component<PlotParams> {}
}

declare module "react-plotly.js/factory" {
  import { ComponentType } from "react";
  import { PlotParams } from "react-plotly.js";

  export default function createPlotlyComponent(plotly: unknown): ComponentType<PlotParams>;
}
