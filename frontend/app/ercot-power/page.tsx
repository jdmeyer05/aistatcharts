"use client";

import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchErcotBundle } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { useState, useMemo } from "react";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Generation Mix (Live)", "Supply vs. Demand", "Duck Curve", "Generation Stack", "Load Forecast vs. Actual", "Reserve & Capacity Forecast", "Ancillary Services", "Grid Frequency"];

const FUEL_COLORS: Record<string, string> = {
  "Natural Gas": "#ff9900", Wind: "#00d1ff", Solar: "#ffdd00", Nuclear: "#a78bfa",
  "Coal and Lignite": "#888888", Hydro: "#3fb950", "Power Storage": "#f85149", Other: "#666666",
};
const STACK_ORDER = ["Nuclear", "Coal and Lignite", "Natural Gas", "Hydro", "Other", "Wind", "Solar", "Power Storage"];

export default function ErcotPower() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["ercot-bundle"],
    queryFn: fetchErcotBundle,
    staleTime: 2 * 60 * 1000,
  });

  // Parse all data
  const parsed = useMemo(() => {
    if (!data || !data.fuel_mix || !data.supply_demand) return null;

    // Supply/Demand
    const sdRaw = data.supply_demand?.data ?? [];
    const sdRows: { ts: string; demand: number; capacity: number }[] = sdRaw.map((r: Record<string, unknown>) => ({ ts: r.timestamp as string, demand: r.demand as number, capacity: r.capacity as number }));
    const latestSd = sdRows[sdRows.length - 1];
    const demand = latestSd?.demand ?? 0;
    const capacity = latestSd?.capacity ?? 0;
    const reserveMargin = demand > 0 ? (capacity - demand) / demand * 100 : 0;

    // Fuel mix
    const fuelData = data.fuel_mix?.data ?? {};
    const fuelTypes: string[] = data.fuel_mix?.types ?? [];
    const capacityData: Record<string, number> = data.fuel_mix?.monthlyCapacity ?? {};
    const allRows: Record<string, unknown>[] = [];
    for (const dayKey in fuelData) {
      const dayData = fuelData[dayKey];
      for (const tsKey in dayData) {
        const row: Record<string, unknown> = { timestamp: tsKey };
        const tsData = dayData[tsKey];
        for (const fuel in tsData) {
          if (typeof tsData[fuel] === "object" && tsData[fuel]?.gen !== undefined) row[fuel] = tsData[fuel].gen;
        }
        allRows.push(row);
      }
    }
    allRows.sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));

    const latestFuel: Record<string, number> = {};
    if (allRows.length > 0) {
      const last = allRows[allRows.length - 1];
      for (const ft of fuelTypes) { if (typeof last[ft] === "number") latestFuel[ft] = last[ft] as number; }
    }
    const totalGen = Object.values(latestFuel).filter(v => v > 0).reduce((s, v) => s + v, 0);
    const wind = latestFuel.Wind ?? 0;
    const solar = latestFuel.Solar ?? 0;
    const gas = latestFuel["Natural Gas"] ?? 0;
    const renewPct = totalGen > 0 ? (wind + solar) / totalGen * 100 : 0;

    // Load forecast
    const lfRaw = data.load_forecast;
    const loadCurrent = lfRaw?.currentDay?.data ?? [];
    const loadPrevious = lfRaw?.previousDay?.data ?? [];

    // Ancillary
    const anc = data.ancillary;
    const gridFreq = anc?.data?.[anc.data.length - 1]?.currentFrequency ?? null;
    const reserves: Record<string, number> = {
      "Reg Up (Deployed)": anc?.lastDeployedRegUp ?? 0,
      "Reg Up (Undeployed)": anc?.lastUndeployedRegUp ?? 0,
      "Reg Down (Deployed)": anc?.lastDeployedRegDown ?? 0,
      "Reg Down (Undeployed)": anc?.lastUndeployedRegDown ?? 0,
      RRS: anc?.lastRrs ?? 0,
      "Non-Spin": anc?.lastNsrs ?? 0,
      ECRS: anc?.lastEcrs ?? 0,
    };
    const freqData: { timestamp: string; currentFrequency: number }[] = anc?.data ?? [];

    // Forecast
    const forecast = data.supply_demand?.forecast ?? [];

    return {
      sdRows, demand, capacity, reserveMargin,
      fuelRows: allRows, latestFuel, totalGen, wind, solar, gas, renewPct, fuelTypes, capacityData,
      loadCurrent, loadPrevious,
      gridFreq, reserves, freqData, forecast,
      lastUpdated: data.fuel_mix?.lastUpdated ?? "N/A",
    };
  }, [data]);

  if (isLoading) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">ERCOT Power Dashboard</h1></div>
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Connecting to ERCOT...</p>
        </div>
      </div>
    );
  }
  if (isError || !parsed) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">ERCOT Power Dashboard</h1></div>
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed to connect to ERCOT. Dashboard API may be temporarily unavailable.</div>
      </div>
    );
  }

  const { sdRows, demand, capacity, reserveMargin, fuelRows, latestFuel, totalGen, wind, solar, gas, renewPct, capacityData, loadCurrent, loadPrevious, gridFreq, reserves, freqData, forecast, lastUpdated } = parsed;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">ERCOT Power Dashboard</h1>
        <p className="text-text-secondary text-sm mt-1">Live grid conditions from the Electric Reliability Council of Texas — updated {lastUpdated}</p>
      </div>

      {/* Primary metrics */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="System Demand" value={`${demand.toLocaleString()} MW`} />
          <Metric label="Available Capacity" value={`${capacity.toLocaleString()} MW`} />
          <Metric label="Reserve Margin" value={`${reserveMargin.toFixed(1)}%`} deltaType={reserveMargin > 10 ? "gain" : "loss"} />
          <Metric label="Total Generation" value={`${totalGen.toLocaleString()} MW`} />
          {gridFreq != null && <Metric label="Grid Frequency" value={`${gridFreq.toFixed(3)} Hz`} delta={`${(gridFreq - 60).toFixed(3)} Hz`} />}
        </div>
      </div>
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Wind" value={`${wind.toLocaleString()} MW`} delta={totalGen > 0 ? `${(wind / totalGen * 100).toFixed(1)}% of mix` : undefined} />
          <Metric label="Solar" value={`${Math.max(0, solar).toLocaleString()} MW`} delta={totalGen > 0 ? `${(Math.max(0, solar) / totalGen * 100).toFixed(1)}% of mix` : undefined} />
          <Metric label="Natural Gas" value={`${gas.toLocaleString()} MW`} delta={totalGen > 0 ? `${(gas / totalGen * 100).toFixed(1)}% of mix` : undefined} />
          <Metric label="Renewable Share" value={`${renewPct.toFixed(1)}%`} />
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {TABS.map((tab, i) => (
          <button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
            {tab}
          </button>
        ))}
      </div>

      {/* Tab 0: Generation Mix */}
      {activeTab === 0 && (
        <div className="card space-y-4">
          {fuelRows.length > 0 && (
            <>
              <Plot data={STACK_ORDER.filter(f => fuelRows.some(r => typeof r[f] === "number")).map(fuel => ({
                x: fuelRows.map(r => r.timestamp as string),
                y: fuelRows.map(r => Math.max(0, (r[fuel] as number) ?? 0)),
                type: "scatter" as const, mode: "lines" as const, name: fuel, stackgroup: "gen",
                line: { width: 0.5, color: FUEL_COLORS[fuel] ?? t.muted },
              }))} layout={{ height: 500, ...L, yaxis: { title: "Generation (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

              {/* Pie chart */}
              {(() => {
                const pie = Object.entries(latestFuel).filter(([, v]) => v > 0);
                return (
                  <Plot data={[{ labels: pie.map(([k]) => k), values: pie.map(([, v]) => v), type: "pie" as const,
                    marker: { colors: pie.map(([k]) => FUEL_COLORS[k] ?? t.muted) }, textinfo: "label+percent", hole: 0.4 }]}
                    layout={{ height: 350, paper_bgcolor: "transparent", font: { color: t.text, size: 10 }, margin: { l: 0, r: 0, t: 10, b: 10 }, showlegend: false }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}

              {/* Capacity table */}
              {Object.keys(capacityData).length > 0 && (
                <details><summary className="text-xs text-text-muted cursor-pointer">Installed Capacity by Fuel Type</summary>
                  <table className="data-table text-xs mt-2">
                    <thead><tr><th>Fuel Type</th><th>Capacity (MW)</th><th>Current Gen (MW)</th><th>Utilization</th></tr></thead>
                    <tbody>
                      {Object.entries(capacityData).map(([fuel, cap]) => (
                        <tr key={fuel}>
                          <td className="font-semibold">{fuel}</td>
                          <td className="font-data">{cap.toLocaleString()}</td>
                          <td className="font-data">{(latestFuel[fuel] ?? 0).toLocaleString()}</td>
                          <td className="font-data">{cap > 0 ? `${((latestFuel[fuel] ?? 0) / cap * 100).toFixed(1)}%` : "N/A"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              )}
            </>
          )}
        </div>
      )}

      {/* Tab 1: Supply vs Demand */}
      {activeTab === 1 && sdRows.length > 0 && (

        <div className="card space-y-4">
          <Plot data={[
            { x: sdRows.map(r => r.ts), y: sdRows.map(r => r.capacity), type: "scatter" as const, mode: "lines" as const, name: "Available Capacity", line: { color: t.gain, width: 2 } },
            { x: sdRows.map(r => r.ts), y: sdRows.map(r => r.demand), type: "scatter" as const, mode: "lines" as const, name: "System Demand", line: { color: t.loss, width: 2 }, fill: "tozeroy" as const, fillcolor: t.loss + "1a" },
            { x: [...sdRows.map(r => r.ts), ...sdRows.slice().reverse().map(r => r.ts)],
              y: [...sdRows.map(r => r.capacity), ...sdRows.slice().reverse().map(r => r.demand)],
              fill: "toself" as const, fillcolor: t.gain + "1a", line: { color: "transparent", width: 0 }, name: "Reserve Margin", hoverinfo: "skip" as const, type: "scatter" as const, mode: "lines" as const },
          ]} layout={{ height: 500, ...L, yaxis: { title: "Megawatts (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

          {/* Reserve margin bars */}
          {(() => {
            const resPct = sdRows.map(r => r.demand > 0 ? (r.capacity - r.demand) / r.demand * 100 : 0);
            return (
              <Plot data={[{ x: sdRows.map(r => r.ts), y: resPct, type: "bar" as const,
                marker: { color: resPct.map(v => v > 10 ? t.gain : v > 5 ? t.spot : t.loss) } }]}
                layout={{ height: 250, ...L, yaxis: { title: "Reserve Margin (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified",
                  shapes: [{ type: "line", y0: 10, y1: 10, x0: 0, x1: 1, xref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }],
                  annotations: [{ x: 1, y: 10, xref: "paper", text: "10%", showarrow: false, font: { size: 9, color: t.spot } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            );
          })()}
        </div>
      )}

      {/* Tab 2: Duck Curve */}
      {activeTab === 2 && fuelRows.length > 0 && (() => {
        const duckData = fuelRows.map(row => {
          const total = Object.entries(row).filter(([k]) => k !== "timestamp").reduce((s, [, v]) => s + (typeof v === "number" ? Math.max(0, v as number) : 0), 0);
          const renew = ((row as Record<string, unknown>)["Wind"] as number ?? 0) + ((row as Record<string, unknown>)["Solar"] as number ?? 0);
          return { ts: row.timestamp as string, total, netLoad: total - renew, renewables: renew };
        });
        return (
          <div className="card space-y-4">
            <Plot data={[
              { x: duckData.map(d => d.ts), y: duckData.map(d => d.total), type: "scatter" as const, mode: "lines" as const, name: "Total Generation", line: { color: t.accent, width: 2 } },
              { x: duckData.map(d => d.ts), y: duckData.map(d => d.netLoad), type: "scatter" as const, mode: "lines" as const, name: "Net Load (ex-Renewables)", line: { color: t.loss, width: 2 } },
              { x: duckData.map(d => d.ts), y: duckData.map(d => d.renewables), type: "scatter" as const, mode: "lines" as const, name: "Renewables", line: { color: t.gain, width: 1.5 }, fill: "tozeroy" as const, fillcolor: t.gain + "15" },
            ]} layout={{ height: 450, ...L, yaxis: { title: "MW", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified", legend: { x: 0.01, y: 0.99, bgcolor: "transparent" } }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            <p className="text-xs text-text-muted">The "duck curve" shows the belly created by midday solar reducing net load, followed by a steep evening ramp as solar drops off.</p>
          </div>
        );
      })()}

      {/* Tab 3: Generation Stack */}
      {activeTab === 3 && fuelRows.length > 0 && (
        <div className="card">
          {(() => {
            const fuels = STACK_ORDER.filter(f => fuelRows.some(row => typeof (row as Record<string, unknown>)[f] === "number"));
            return (
              <Plot data={fuels.map(fuel => ({
                x: fuelRows.map(r => r.timestamp),
                y: fuelRows.map(r => Math.max(0, ((r as Record<string, unknown>)[fuel] as number) ?? 0)),
                type: "scatter" as const, mode: "lines" as const, name: fuel, stackgroup: "gen",
                line: { width: 0.5, color: FUEL_COLORS[fuel] ?? t.muted },
              }))} layout={{ height: 500, ...L, yaxis: { title: "Generation (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            );
          })()}
        </div>
      )}

      {/* Tab 4: Load Forecast vs Actual */}
      {activeTab === 4 && (
        <div className="card space-y-4">
          {loadCurrent.length > 0 ? (<>
            <Plot data={[
              ...(loadPrevious.length > 0 ? [{ x: loadPrevious.map((r: Record<string, unknown>) => r.hourEnding), y: loadPrevious.map((r: Record<string, unknown>) => r.systemLoad), type: "scatter" as const, mode: "lines" as const, name: "Previous Day", line: { color: t.muted, width: 1.5, dash: "dot" as const } }] : []),
              ...(loadCurrent.some((r: Record<string, unknown>) => (r.systemLoad as number) > 0) ? [{
                x: loadCurrent.filter((r: Record<string, unknown>) => (r.systemLoad as number) > 0).map((r: Record<string, unknown>) => r.hourEnding),
                y: loadCurrent.filter((r: Record<string, unknown>) => (r.systemLoad as number) > 0).map((r: Record<string, unknown>) => r.systemLoad),
                type: "scatter" as const, mode: "lines+markers" as const, name: "Actual Load", line: { color: t.loss, width: 2.5 },
              }] : []),
              { x: loadCurrent.map((r: Record<string, unknown>) => r.hourEnding), y: loadCurrent.map((r: Record<string, unknown>) => r.currentLoadForecast), type: "scatter" as const, mode: "lines" as const, name: "Current Forecast", line: { color: t.accent, width: 2, dash: "dash" as const } },
              { x: loadCurrent.map((r: Record<string, unknown>) => r.hourEnding), y: loadCurrent.map((r: Record<string, unknown>) => r.dayAheadForecast), type: "scatter" as const, mode: "lines" as const, name: "Day-Ahead Forecast", line: { color: t.spot, width: 1.5, dash: "dash" as const } },
            ]} layout={{ height: 500, ...L, xaxis: { title: "Hour Ending", gridcolor: t.grid }, yaxis: { title: "Megawatts (MW)", gridcolor: t.grid }, hovermode: "x unified" }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

            {/* Forecast error */}
            {(() => {
              const actual = loadCurrent.filter((r: Record<string, unknown>) => (r.systemLoad as number) > 0);
              if (actual.length === 0) return null;
              const errors: { hr: string; err: number }[] = actual.map((r: Record<string, unknown>) => ({
                hr: r.hourEnding as string,
                err: (r.systemLoad as number) - (r.currentLoadForecast as number),
              }));
              return (
                <>
                  <p className="text-xs text-text-muted">Forecast Error: Actual - Forecast (positive = under-forecast)</p>
                  <Plot data={[{ x: errors.map(e => e.hr), y: errors.map(e => e.err), type: "bar" as const,
                    marker: { color: errors.map(e => Math.abs(e.err) < 500 ? t.gain : Math.abs(e.err) < 1000 ? t.spot : t.loss) } }]}
                    layout={{ height: 220, ...L, xaxis: { title: "Hour Ending", gridcolor: t.grid }, yaxis: { title: "Error (MW)", gridcolor: t.grid },
                      shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>
              );
            })()}
          </>) : <p className="text-sm text-text-muted">Load forecast data unavailable.</p>}
        </div>
      )}

      {/* Tab 5: Reserve & Capacity Forecast */}
      {activeTab === 5 && (
        <div className="card space-y-4">
          {forecast.length > 0 ? (() => {
            const fc = forecast as { hourEnding: number; availCapGen: number; forecastedDemand: number; deliveryDate?: string }[];
            const resMw = fc.map(r => r.availCapGen - r.forecastedDemand);
            const resPct = fc.map((r, i) => r.forecastedDemand > 0 ? resMw[i] / r.forecastedDemand * 100 : 0);
            const minRes = Math.min(...resPct);
            const minResHr = fc[resPct.indexOf(minRes)]?.hourEnding;
            const peakDemand = Math.max(...fc.map(r => r.forecastedDemand));
            const peakHr = fc[fc.map(r => r.forecastedDemand).indexOf(peakDemand)]?.hourEnding;
            return (<>
              <Plot data={[
                { x: fc.map(r => r.hourEnding), y: fc.map(r => r.availCapGen), type: "scatter" as const, mode: "lines+markers" as const, name: "Available Capacity", line: { color: t.gain, width: 2 } },
                { x: fc.map(r => r.hourEnding), y: fc.map(r => r.forecastedDemand), type: "scatter" as const, mode: "lines+markers" as const, name: "Forecasted Demand", line: { color: t.loss, width: 2 } },
                { x: [...fc.map(r => r.hourEnding), ...fc.slice().reverse().map(r => r.hourEnding)],
                  y: [...fc.map(r => r.availCapGen), ...fc.slice().reverse().map(r => r.forecastedDemand)],
                  fill: "toself" as const, fillcolor: t.gain + "1a", line: { color: "transparent", width: 0 }, name: "Reserve Band", hoverinfo: "skip" as const, type: "scatter" as const, mode: "lines" as const },
              ]} layout={{ height: 500, ...L, xaxis: { title: "Hour Ending", gridcolor: t.grid }, yaxis: { title: "Megawatts (MW)", gridcolor: t.grid }, hovermode: "x unified" }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              <div className="flex gap-6">
                <Metric label="Peak Forecast Demand" value={`${peakDemand.toLocaleString()} MW`} delta={`Hour ${peakHr}`} />
                <Metric label="Min Reserve Margin" value={`${minRes.toFixed(1)}%`} delta={`Hour ${minResHr}`} deltaType={minRes > 10 ? "gain" : "loss"} />
              </div>
            </>);
          })() : <p className="text-sm text-text-muted">Forecast data unavailable.</p>}
        </div>
      )}

      {/* Tab 6: Ancillary Services */}
      {activeTab === 6 && (
        <div className="card">
          {Object.values(reserves).some(v => v > 0) ? (
            <Plot data={[{
              x: Object.keys(reserves), y: Object.values(reserves), type: "bar" as const,
              marker: { color: ["#00d1ff", "#0090b0", "#3fb950", "#009060", "#f59e0b", "#a78bfa", "#f85149"] },
              text: Object.values(reserves).map(v => `${v.toLocaleString()} MW`), textposition: "outside" as const,
            }]} layout={{ height: 400, ...L, yaxis: { title: "Megawatts (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
          ) : <p className="text-sm text-text-muted">Ancillary services data unavailable.</p>}
        </div>
      )}

      {/* Tab 7: Grid Frequency */}
      {activeTab === 7 && (
        <div className="card space-y-4">
          {freqData.length > 0 ? (<>
            <Plot data={[{
              x: freqData.map(r => r.timestamp), y: freqData.map(r => r.currentFrequency),
              type: "scatter" as const, mode: "lines" as const, name: "Grid Frequency", line: { color: t.accent, width: 1.5 },
            }]} layout={{ height: 400, ...L,
              yaxis: { title: "Frequency (Hz)", gridcolor: t.grid, range: [59.9, 60.1] },
              xaxis: { gridcolor: t.grid }, hovermode: "x unified",
              shapes: [
                { type: "line", y0: 60, y1: 60, x0: 0, x1: 1, xref: "paper", line: { color: t.gain, width: 1 } },
                { type: "line", y0: 59.95, y1: 59.95, x0: 0, x1: 1, xref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
                { type: "line", y0: 60.05, y1: 60.05, x0: 0, x1: 1, xref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
              ],
              annotations: [
                { x: 0, y: 60, xref: "paper", text: "60 Hz Nominal", showarrow: false, font: { size: 8, color: t.gain } },
              ] }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            <div className="flex gap-6">
              <Metric label="Current" value={`${freqData[freqData.length - 1].currentFrequency.toFixed(3)} Hz`} />
              <Metric label="Min (Session)" value={`${Math.min(...freqData.map(r => r.currentFrequency)).toFixed(3)} Hz`} />
              <Metric label="Max (Session)" value={`${Math.max(...freqData.map(r => r.currentFrequency)).toFixed(3)} Hz`} />
            </div>
          </>) : <p className="text-sm text-text-muted">Frequency data unavailable.</p>}
        </div>
      )}
    </div>
  );
}
