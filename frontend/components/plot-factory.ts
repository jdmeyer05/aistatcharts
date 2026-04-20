// Custom Plotly build — only the trace types used across this app.
// Full plotly.js is ~3.5MB minified because it includes geo/map/sankey/
// treemap/violin/etc. A grep of every chart across app/ shows only these 10
// trace types, so we register them explicitly against plotly.js/lib/core.
//
// Roughly halves the Plotly chunk. Non-obvious constraint: adding a new
// trace type elsewhere silently falls back to undefined — add its import
// here if a chart starts rendering as blank.
import createPlotlyComponent from "react-plotly.js/factory";
// @ts-expect-error — no types for lib/core subpath
import Plotly from "plotly.js/lib/core";
// @ts-expect-error — no types for lib/<trace> subpaths
import scatter from "plotly.js/lib/scatter";
// @ts-expect-error
import bar from "plotly.js/lib/bar";
// @ts-expect-error
import pie from "plotly.js/lib/pie";
// @ts-expect-error
import heatmap from "plotly.js/lib/heatmap";
// @ts-expect-error
import histogram from "plotly.js/lib/histogram";
// @ts-expect-error
import candlestick from "plotly.js/lib/candlestick";
// @ts-expect-error
import waterfall from "plotly.js/lib/waterfall";
// @ts-expect-error
import scatterpolar from "plotly.js/lib/scatterpolar";
// @ts-expect-error
import surface from "plotly.js/lib/surface";
// @ts-expect-error
import scatter3d from "plotly.js/lib/scatter3d";

Plotly.register([
  scatter, bar, pie, heatmap, histogram, candlestick,
  waterfall, scatterpolar, surface, scatter3d,
]);

const Plot = createPlotlyComponent(Plotly);
export default Plot;
