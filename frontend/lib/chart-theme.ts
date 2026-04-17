/**
 * Theme-aware Plotly chart configuration.
 * Returns colors and layout defaults that match the current light/dark mode.
 */

export interface ChartTheme {
  paper: string;
  plot: string;
  text: string;
  grid: string;
  accent: string;
  spot: string;
  gain: string;
  loss: string;
  muted: string;
  hv20: string;
  hv60: string;
  surface3dBg: string;
}

const LIGHT: ChartTheme = {
  paper: "transparent",
  plot: "#ffffff",
  text: "#1a2332",
  grid: "#f1f3f5",
  accent: "#1a56db",
  spot: "#f59e0b",
  gain: "#0f7b3f",
  loss: "#b91c1c",
  muted: "#868e96",
  hv20: "#f97316",
  hv60: "#8b5cf6",
  surface3dBg: "#f1f3f5",
};

const DARK: ChartTheme = {
  paper: "transparent",
  plot: "#161b22",
  text: "#e6edf3",
  grid: "#21262d",
  accent: "#58a6ff",
  spot: "#f59e0b",
  gain: "#3fb950",
  loss: "#f85149",
  muted: "#8b949e",
  hv20: "#f97316",
  hv60: "#a78bfa",
  surface3dBg: "#0d1117",
};

export function getChartTheme(isDark: boolean): ChartTheme {
  return isDark ? DARK : LIGHT;
}

/** Base Plotly layout merged with theme colors */
export function getBaseLayout(t: ChartTheme, overrides?: Record<string, unknown>) {
  return {
    paper_bgcolor: t.paper,
    plot_bgcolor: t.plot,
    font: { family: "Inter, sans-serif", color: t.text, size: 10 },
    margin: { l: 50, r: 20, t: 20, b: 50 },
    ...overrides,
  };
}

/** Standard chart heights. Prefer these over magic numbers. */
export const CHART_HEIGHT = {
  sparkline: 80,   // tiny trend in a card
  compact: 220,    // secondary chart, nested grid
  normal: 340,     // primary chart
  tall: 460,       // detail view / 2-row subplot
} as const;

/** Standard heatmap text size + cell gap. */
export const HEATMAP_FONT_SIZE = 10;
export const HEATMAP_CELL_GAP = 1.5;

/**
 * Colorscale for heatmaps. Pick by semantic "kind":
 * - divergent: signed data centered at 0 (red ↔ green).
 * - correlation: -1 to 1 (red ↔ brand accent, avoiding green/gain/loss confusion).
 * - sequential: unsigned magnitude, low → high (paper ↔ accent ↔ gain).
 * - intensity: low-high, single ramp (paper → accent), no divergence.
 */
export function heatmapColorscale(
  t: ChartTheme,
  kind: "divergent" | "correlation" | "sequential" | "intensity",
): Array<[number, string]> {
  switch (kind) {
    case "divergent":   return [[0, t.loss], [0.5, t.plot], [1, t.gain]];
    case "correlation": return [[0, t.loss], [0.5, t.plot], [1, t.accent]];
    case "sequential":  return [[0, t.plot], [0.5, t.accent], [1, t.gain]];
    case "intensity":   return [[0, t.plot], [1, t.accent]];
  }
}

/** Height for a heatmap with n rows. Caps at 720px so long grids stay usable. */
export function heatmapHeight(nRows: number, opts?: { compact?: boolean; padding?: number }): number {
  const perRow = opts?.compact ? 24 : 32;
  const padding = opts?.padding ?? 90;
  return Math.max(260, Math.min(720, nRows * perRow + padding));
}

/**
 * Shared heatmap trace defaults. Spread this into your heatmap trace and
 * layer z/x/y/text/zmid on top. Standardizes font, gap, colorbar style,
 * and `hoverongaps: false` so null cells don't emit stray tooltips.
 */
export function heatmapTrace(
  t: ChartTheme,
  kind: "divergent" | "correlation" | "sequential" | "intensity",
  opts?: { colorbarTitle?: string },
) {
  return {
    type: "heatmap" as const,
    colorscale: heatmapColorscale(t, kind),
    xgap: HEATMAP_CELL_GAP,
    ygap: HEATMAP_CELL_GAP,
    hoverongaps: false,
    texttemplate: "%{text}",
    textfont: { size: HEATMAP_FONT_SIZE, color: t.text },
    colorbar: {
      thickness: 10,
      len: 0.92,
      outlinewidth: 0,
      tickfont: { size: 9, color: t.text },
      ...(opts?.colorbarTitle
        ? { title: { text: opts.colorbarTitle, side: "right" as const, font: { size: 10, color: t.text } } }
        : {}),
    },
  };
}

/** 3D scene defaults for surface plots */
export function get3dScene(t: ChartTheme) {
  const axis = {
    backgroundcolor: t.surface3dBg,
    gridcolor: t.grid,
    showbackground: true,
    color: t.text,
  };
  return {
    xaxis: { ...axis, title: "Strike ($)" },
    yaxis: { ...axis, title: "Expiration" },
    zaxis: { ...axis, title: "IV (%)" },
    camera: { eye: { x: 1.8, y: -1.4, z: 0.9 } },
    aspectratio: { x: 1.5, y: 1, z: 0.6 },
  };
}
