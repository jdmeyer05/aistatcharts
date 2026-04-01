"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { fetchOptionsChain, fetchSnapshot } from "@/lib/api";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface SkewData {
  label: string;
  dte: number;
  strikes: number[];
  ivs: number[];
  atmIv: number;
  putSkew25d: number | null;  // 25d put IV / ATM IV
  skewSlope: number;          // bps per delta step
}

interface Dislocation {
  strike: number;
  exp: string;
  iv: number;
  atmIv: number;
  diff: number;     // iv - atmIv (bps)
  type: "rich" | "cheap";
}

interface SurfaceData {
  strikes: number[];
  expLabels: string[];
  zMatrix: (number | null)[][];
  spot: number;
  spotColIdx: number;
  spotZLine: number[];
  atmIv: number;
  hv20: number | null;
  vrp: number | null;
  termStructure: { label: string; iv: number; dte: number }[];
  tsShape: string;
  skews: SkewData[];
  dislocations: Dislocation[];
  avgDislocation: number;
}

function buildSurface(chainData: Record<string, unknown>[], spot: number): SurfaceData | null {
  if (!chainData || chainData.length < 20 || spot <= 0) return null;

  const strikeLo = spot * 0.75;
  const strikeHi = spot * 1.25;

  // Use puts below ATM, calls at/above ATM — stitched at spot (matches Streamlit)
  const rows: { strike: number; exp: string; dte: number; iv: number }[] = [];
  const now = Date.now();

  for (const c of chainData) {
    const ct = c.contract_type as string;
    const k = c.strike_price as number;
    const iv = c.implied_volatility as number;
    const exp = c.expiration_date as string;
    if (!iv || iv <= 0.01 || iv >= 3.0 || k < strikeLo || k > strikeHi) continue;

    // Puts below ATM, calls at/above ATM
    const isPut = ct === "put" && k <= spot;
    const isCall = ct === "call" && k >= spot;
    if (!isPut && !isCall) continue;

    const dte = Math.max(1, Math.round((new Date(exp + "T16:00:00").getTime() - now) / 86400000));
    rows.push({ strike: k, exp, dte, iv: iv * 100 });
  }

  if (rows.length < 15) return null;

  // Get unique expirations with enough data, sorted by DTE
  const expCounts = new Map<string, { count: number; dte: number }>();
  rows.forEach(r => {
    const e = expCounts.get(r.exp);
    if (e) e.count++;
    else expCounts.set(r.exp, { count: 1, dte: r.dte });
  });

  const goodExps = Array.from(expCounts.entries())
    .filter(([, v]) => v.count >= 3)
    .sort(([, a], [, b]) => a.dte - b.dte)
    .slice(0, 10)
    .map(([exp, v]) => ({ exp, dte: v.dte }));

  if (goodExps.length < 2) return null;

  // Get all strikes that appear in the data, sorted
  const allStrikes = [...new Set(rows.map(r => r.strike))].sort((a, b) => a - b);
  if (allStrikes.length < 5) return null;

  // Build IV matrix — pivot (expiration × strike), then interpolate gaps
  const ivMap = new Map<string, Map<number, number>>();
  rows.forEach(r => {
    if (!ivMap.has(r.exp)) ivMap.set(r.exp, new Map());
    const existing = ivMap.get(r.exp)!.get(r.strike);
    if (!existing || r.iv > 0) ivMap.get(r.exp)!.set(r.strike, r.iv);
  });

  // Build raw matrix
  const rawMatrix: (number | null)[][] = [];
  goodExps.forEach(({ exp }) => {
    const expData = ivMap.get(exp);
    const row = allStrikes.map(k => expData?.get(k) ?? null);
    rawMatrix.push(row);
  });

  // Interpolate along strikes (axis=1) then along expirations (axis=0)
  function interpolateRow(row: (number | null)[]): (number | null)[] {
    const result = [...row];
    // Forward fill
    let last: number | null = null;
    for (let i = 0; i < result.length; i++) {
      if (result[i] !== null) last = result[i];
      else if (last !== null) result[i] = last;
    }
    // Backward fill
    last = null;
    for (let i = result.length - 1; i >= 0; i--) {
      if (result[i] !== null) last = result[i];
      else if (last !== null) result[i] = last;
    }
    // Linear interpolation between known points
    for (let i = 0; i < row.length; i++) {
      if (row[i] !== null) continue;
      // Find nearest known points
      let li = i - 1, ri = i + 1;
      while (li >= 0 && row[li] === null) li--;
      while (ri < row.length && row[ri] === null) ri++;
      if (li >= 0 && ri < row.length && row[li] !== null && row[ri] !== null) {
        const t = (i - li) / (ri - li);
        result[i] = row[li]! + t * (row[ri]! - row[li]!);
      }
    }
    return result;
  }

  // Interpolate each row (across strikes)
  const interpMatrix = rawMatrix.map(interpolateRow);

  // Interpolate each column (across expirations)
  for (let col = 0; col < allStrikes.length; col++) {
    const colVals = interpMatrix.map(row => row[col]);
    const interpCol = interpolateRow(colVals);
    interpCol.forEach((v, rowIdx) => { interpMatrix[rowIdx][col] = v; });
  }

  // Expiration labels
  const expLabels = goodExps.map(({ exp, dte }) => {
    const d = new Date(exp + "T12:00:00");
    return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} (${dte}d)`;
  });

  // Spot column — for the yellow spot line
  const spotColIdx = allStrikes.reduce((best, k, idx) =>
    Math.abs(k - spot) < Math.abs(allStrikes[best] - spot) ? idx : best, 0);
  const spotZLine = interpMatrix.map(row => row[spotColIdx] ?? 0);

  // ATM IV
  const atmIv = interpMatrix[0]?.[spotColIdx] ?? 0;

  // Term structure
  const termStructure = goodExps.map(({ exp, dte }, i) => ({
    label: new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    iv: interpMatrix[i]?.[spotColIdx] ?? 0,
    dte,
  }));

  // Term structure shape
  const backAtmIv = interpMatrix[interpMatrix.length - 1]?.[spotColIdx] ?? 0;
  const tsShape = backAtmIv > atmIv * 1.02 ? "Contango" : backAtmIv < atmIv * 0.98 ? "Backwardation" : "Flat";

  // Per-expiration skew data
  const skews: SkewData[] = goodExps.map(({ exp, dte }, expIdx) => {
    const row = interpMatrix[expIdx];
    const expAtmIv = row[spotColIdx] ?? 0;
    const ivs = allStrikes.map((_, i) => row[i] ?? 0);

    // 25d put skew: find strike ~8% below spot
    const put25Idx = allStrikes.reduce((best, k, idx) =>
      Math.abs(k - spot * 0.92) < Math.abs(allStrikes[best] - spot * 0.92) ? idx : best, 0);
    const put25Iv = row[put25Idx] ?? 0;
    const putSkew25d = expAtmIv > 0 ? put25Iv / expAtmIv : null;

    // Skew slope: (put25 IV - call25 IV) across the strike range in bps per step
    const call25Idx = allStrikes.reduce((best, k, idx) =>
      Math.abs(k - spot * 1.08) < Math.abs(allStrikes[best] - spot * 1.08) ? idx : best, 0);
    const call25Iv = row[call25Idx] ?? 0;
    const skewSlope = expAtmIv > 0 && put25Idx !== call25Idx
      ? Math.round((put25Iv - call25Iv) / Math.abs(call25Idx - put25Idx) * 100) : 0;

    return {
      label: new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      dte, strikes: allStrikes, ivs, atmIv: expAtmIv,
      putSkew25d: putSkew25d ? Math.round(putSkew25d * 100) / 100 : null,
      skewSlope,
    };
  });

  // Dislocations: contracts where IV deviates significantly from ATM IV for that expiration
  const dislocations: Dislocation[] = [];
  goodExps.forEach(({ exp }, expIdx) => {
    const expAtmIv = interpMatrix[expIdx][spotColIdx] ?? 0;
    if (expAtmIv <= 0) return;
    allStrikes.forEach((k, kIdx) => {
      const iv = interpMatrix[expIdx][kIdx] ?? 0;
      if (iv <= 0) return;
      const diff = Math.round((iv - expAtmIv) * 100); // bps
      if (Math.abs(diff) > 150) { // >1.5% dislocation
        dislocations.push({ strike: k, exp, iv, atmIv: expAtmIv, diff, type: diff > 0 ? "rich" : "cheap" });
      }
    });
  });
  dislocations.sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff));
  const avgDislocation = dislocations.length > 0
    ? Math.round(dislocations.reduce((s, d) => s + d.diff, 0) / dislocations.length) : 0;

  // HV20 approximation (not available from chain — return null, can be fetched separately)
  const vrp = null;
  const hv20 = null;

  return {
    strikes: allStrikes, expLabels, zMatrix: interpMatrix,
    spot, spotColIdx, spotZLine, atmIv, hv20, vrp,
    termStructure, tsShape, skews, dislocations, avgDislocation,
  };
}

const TABS = ["3D Surface", "IV Skew", "Term Structure", "Dislocations", "Skew Metrics"];

function fmtExp(exp: string) {
  try { return new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  catch { return exp; }
}

export default function VolSurface() {
  const [ticker, setTicker] = useState("SPY");
  const [surface, setSurface] = useState<SurfaceData | null>(null);
  const [viewMode, setViewMode] = useState<"3d" | "heatmap">("3d");
  const [activeTab, setActiveTab] = useState(0);

  const load = useMutation({
    mutationFn: async (tk: string) => {
      const [chain, snap] = await Promise.all([
        fetchOptionsChain(tk),
        fetchSnapshot([tk]),
      ]);
      return { chain, spot: snap[tk]?.price ?? 0 };
    },
    onSuccess: (data) => setSurface(buildSurface(data.chain.data, data.spot)),
  });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Volatility Surface</h1>
        <p className="text-text-secondary text-sm mt-1">
          3D implied volatility surface — puts below ATM, calls above, stitched at spot.
        </p>
      </div>

      {/* Controls */}
      <div className="card card-compact">
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && load.mutate(ticker.toUpperCase())}
            placeholder="SPY"
            className="w-32 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface"
          />
          <button
            onClick={() => load.mutate(ticker.toUpperCase())}
            disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover
                       disabled:opacity-50 transition-colors text-sm"
          >
            {load.isPending ? "Loading..." : "Load Surface"}
          </button>
          {surface && (
            <div className="flex gap-1 ml-auto">
              {(["3d", "heatmap"] as const).map(mode => (
                <button
                  key={mode}
                  onClick={() => setViewMode(mode)}
                  className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                    viewMode === mode ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
                  }`}
                >
                  {mode === "3d" ? "3D Surface" : "Heatmap"}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching options chain...</p>
        </div>
      )}

      {surface && (
        <>
          {/* Key Metrics Bar */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Spot" value={`$${surface.spot.toFixed(2)}`} />
              <Metric label="ATM IV" value={`${surface.atmIv.toFixed(1)}%`} />
              <Metric label="Term Structure" value={surface.tsShape} />
              <Metric label="Put Skew (25Δ)" value={surface.skews[0]?.putSkew25d ? `${surface.skews[0].putSkew25d.toFixed(2)}x` : "N/A"} />
              <Metric label="Dislocations" value={`${surface.dislocations.length} (avg ${surface.avgDislocation > 0 ? "+" : ""}${surface.avgDislocation}bp)`} />
              <Metric label="Expirations" value={String(surface.expLabels.length)} />
            </div>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 border-b border-border pb-1">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* Tab: 3D Surface */}
          {activeTab === 0 && (
            <div className="card">
              <div className="flex justify-end mb-2">
                {(["3d", "heatmap"] as const).map(mode => (
                  <button key={mode} onClick={() => setViewMode(mode)}
                    className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
                      viewMode === mode ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                    {mode === "3d" ? "3D" : "Heatmap"}
                  </button>
                ))}
              </div>
              {viewMode === "3d" ? (
                <Plot
                data={[
                  // Main surface
                  {
                    type: "surface" as const,
                    x: surface.strikes,
                    y: Array.from({ length: surface.expLabels.length }, (_, i) => i),
                    z: surface.zMatrix,
                    colorscale: "Viridis",
                    colorbar: {
                      title: { text: "IV %", font: { size: 10 } },
                      tickformat: ".0f",
                      len: 0.6,
                      thickness: 15,
                    },
                    hovertemplate: "Strike: $%{x:,.0f}<br>IV: %{z:.1f}%<extra></extra>",
                    lighting: { ambient: 0.6, diffuse: 0.5, specular: 0.3, roughness: 0.5 },
                    opacity: 0.92,
                  },
                  // Spot price line (yellow)
                  {
                    type: "scatter3d" as const,
                    x: Array(surface.expLabels.length).fill(surface.spot),
                    y: Array.from({ length: surface.expLabels.length }, (_, i) => i),
                    z: surface.spotZLine,
                    mode: "lines+markers" as const,
                    line: { color: "#f59e0b", width: 5 },
                    marker: { size: 3, color: "#f59e0b" },
                    name: "Spot",
                  },
                ]}
                layout={{
                  height: 600,
                  margin: { l: 0, r: 0, t: 10, b: 10 },
                  paper_bgcolor: "#f8f9fa",
                  font: { family: "Inter, sans-serif", color: "#1a2332", size: 10 },
                  scene: {
                    xaxis: {
                      title: "Strike ($)",
                      backgroundcolor: "#f1f3f5",
                      gridcolor: "rgba(0,0,0,0.08)",
                      showbackground: true,
                    },
                    yaxis: {
                      title: "Expiration",
                      tickvals: Array.from({ length: surface.expLabels.length }, (_, i) => i),
                      ticktext: surface.expLabels,
                      backgroundcolor: "#f1f3f5",
                      gridcolor: "rgba(0,0,0,0.08)",
                      showbackground: true,
                    },
                    zaxis: {
                      title: "IV (%)",
                      backgroundcolor: "#f1f3f5",
                      gridcolor: "rgba(0,0,0,0.08)",
                      showbackground: true,
                    },
                    camera: { eye: { x: 1.8, y: -1.4, z: 0.9 } },
                    aspectratio: { x: 1.5, y: 1, z: 0.6 },
                  },
                  legend: { x: 0, y: 1, bgcolor: "rgba(255,255,255,0.7)" },
                }}
                config={{ displayModeBar: true, responsive: true }}
                style={{ width: "100%", height: "600px" }}
              />
            ) : (
              <Plot
                data={[{
                  type: "heatmap" as const,
                  x: surface.strikes,
                  y: surface.expLabels,
                  z: surface.zMatrix,
                  colorscale: "Viridis",
                  colorbar: { title: { text: "IV %", font: { size: 10 } }, thickness: 15 },
                  hovertemplate: "Strike: $%{x:,.0f}<br>%{y}<br>IV: %{z:.1f}%<extra></extra>",
                  zsmooth: "best",
                }]}
                layout={{
                  height: 450,
                  margin: { l: 100, r: 20, t: 20, b: 50 },
                  paper_bgcolor: "transparent",
                  plot_bgcolor: "#ffffff",
                  font: { family: "Inter, sans-serif", color: "#1a2332", size: 10 },
                  xaxis: { title: "Strike ($)", gridcolor: "#f1f3f5" },
                  yaxis: { gridcolor: "#f1f3f5" },
                  shapes: [{
                    type: "line",
                    x0: surface.spot, x1: surface.spot,
                    y0: 0, y1: 1, yref: "paper",
                    line: { color: "#f59e0b", width: 2, dash: "dash" },
                  }],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%", height: "450px" }}
              />
            )}
          </div>
          )}

          {/* Tab: IV Skew */}
          {activeTab === 1 && (
            <div className="card space-y-4">
              {surface.skews.map((skew, i) => (
                <div key={i}>
                  <div className="metric-label mb-1">{skew.label} ({skew.dte}d) — ATM {skew.atmIv.toFixed(1)}%</div>
                  <Plot
                    data={[{
                      x: skew.strikes, y: skew.ivs,
                      type: "scatter" as const, mode: "lines+markers" as const,
                      line: { color: "#1a56db", width: 2 },
                      marker: { size: 3 },
                      hovertemplate: "$%{x:,.0f}: %{y:.1f}%<extra></extra>",
                    }]}
                    layout={{
                      height: 180, margin: { l: 40, r: 10, t: 5, b: 30 },
                      paper_bgcolor: "transparent", plot_bgcolor: "#fff",
                      font: { family: "Inter", color: "#1a2332", size: 9 },
                      xaxis: { title: i === surface.skews.length - 1 ? "Strike ($)" : "", gridcolor: "#f1f3f5" },
                      yaxis: { title: "IV %", gridcolor: "#f1f3f5" },
                      showlegend: false,
                      shapes: [{ type: "line", x0: surface.spot, x1: surface.spot, y0: 0, y1: 1, yref: "paper",
                        line: { color: "#f59e0b", width: 1, dash: "dash" } }],
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%" }}
                  />
                </div>
              ))}
            </div>
          )}

          {/* Tab: Term Structure */}
          {activeTab === 2 && (
            <div className="card">
              <Plot
                data={[{
                  x: surface.termStructure.map(t => `${t.label} (${t.dte}d)`),
                  y: surface.termStructure.map(t => t.iv),
                  type: "scatter" as const, mode: "lines+markers" as const,
                  line: { color: "#1a56db", width: 2 },
                  marker: { color: "#1a56db", size: 8 },
                  text: surface.termStructure.map(t => `${t.iv.toFixed(1)}%`),
                  textposition: "top center" as const,
                  textfont: { size: 9, color: "#1a2332" },
                  hovertemplate: "%{x}<br>IV: %{y:.1f}%<extra></extra>",
                }]}
                layout={{
                  height: 350, margin: { l: 50, r: 20, t: 20, b: 60 },
                  paper_bgcolor: "transparent", plot_bgcolor: "#fff",
                  font: { family: "Inter", color: "#1a2332", size: 11 },
                  xaxis: { title: "Expiration", gridcolor: "#f1f3f5" },
                  yaxis: { title: "ATM IV (%)", gridcolor: "#f1f3f5" },
                  showlegend: false,
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
              <div className="mt-3 text-sm text-text-muted">
                Shape: <strong className={surface.tsShape === "Contango" ? "text-gain" : surface.tsShape === "Backwardation" ? "text-loss" : ""}>{surface.tsShape}</strong>
                {surface.tsShape === "Contango" && " — back-month IV higher than front (normal, favorable for calendars)"}
                {surface.tsShape === "Backwardation" && " — front-month IV higher than back (event risk, favor condors)"}
              </div>
            </div>
          )}

          {/* Tab: Dislocations */}
          {activeTab === 3 && (
            <div className="card">
              {surface.dislocations.length > 0 ? (
                <>
                  <div className="text-sm text-text-muted mb-3">
                    {surface.dislocations.length} contracts with IV &gt;1.5% from ATM.
                    Avg dislocation: <strong>{surface.avgDislocation > 0 ? "+" : ""}{surface.avgDislocation}bp</strong>
                    ({surface.dislocations.filter(d => d.type === "rich").length} rich,
                    {surface.dislocations.filter(d => d.type === "cheap").length} cheap)
                  </div>
                  <div className="overflow-x-auto">
                    <table className="data-table">
                      <thead>
                        <tr><th>Strike</th><th>Expiration</th><th>IV</th><th>ATM IV</th><th>Diff (bp)</th><th>Type</th></tr>
                      </thead>
                      <tbody>
                        {surface.dislocations.slice(0, 20).map((d, i) => (
                          <tr key={i}>
                            <td className="font-data">${d.strike.toFixed(0)}</td>
                            <td>{fmtExp(d.exp)}</td>
                            <td className="font-data">{d.iv.toFixed(1)}%</td>
                            <td className="font-data">{d.atmIv.toFixed(1)}%</td>
                            <td className={`font-data font-semibold ${d.type === "rich" ? "text-loss" : "text-gain"}`}>
                              {d.diff > 0 ? "+" : ""}{d.diff}
                            </td>
                            <td><span className={`badge ${d.type === "rich" ? "badge-loss" : "badge-gain"}`}>{d.type}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <p className="text-sm text-text-muted">No significant dislocations detected. Surface is well-behaved.</p>
              )}
            </div>
          )}

          {/* Tab: Skew Metrics */}
          {activeTab === 4 && (
            <div className="card">
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr><th>Expiration</th><th>DTE</th><th>ATM IV</th><th>25Δ Put Skew</th><th>Skew Slope (bp/step)</th></tr>
                  </thead>
                  <tbody>
                    {surface.skews.map((s, i) => (
                      <tr key={i}>
                        <td className="font-semibold">{s.label}</td>
                        <td className="font-data">{s.dte}d</td>
                        <td className="font-data">{s.atmIv.toFixed(1)}%</td>
                        <td className={`font-data ${s.putSkew25d && s.putSkew25d > 1.10 ? "text-loss font-semibold" : s.putSkew25d && s.putSkew25d < 1.02 ? "text-gain" : ""}`}>
                          {s.putSkew25d ? `${s.putSkew25d.toFixed(2)}x` : "N/A"}
                        </td>
                        <td className="font-data">{s.skewSlope > 0 ? "+" : ""}{s.skewSlope}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 text-xs text-text-muted">
                <strong>25Δ Put Skew:</strong> ratio of 25-delta put IV to ATM IV. &gt;1.10 = steep (fear premium). &lt;1.02 = flat (complacent).
                <strong className="ml-3">Skew Slope:</strong> IV change per strike step in basis points. Positive = normal put skew.
              </div>
            </div>
          )}
        </>
      )}

      {surface === null && !load.isPending && load.isSuccess && (
        <div className="card text-center py-8 text-text-muted">
          Not enough options data to build a surface. Try a more liquid ticker.
        </div>
      )}

      {load.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
          Failed to load: {(load.error as Error).message}
        </div>
      )}
    </div>
  );
}
