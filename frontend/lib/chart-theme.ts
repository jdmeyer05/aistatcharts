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
