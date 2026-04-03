"use client";

import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchErcotBundle } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

export default function ErcotCapacity() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const { data, isLoading } = useQuery({ queryKey: ["ercot-bundle"], queryFn: fetchErcotBundle, staleTime: 5 * 60 * 1000 });

  const capacityData: Record<string, number> = data?.fuel_mix?.monthlyCapacity ?? {};
  const entries = Object.entries(capacityData).sort(([, a], [, b]) => b - a);
  const total = entries.reduce((s, [, v]) => s + v, 0);

  const FUEL_COLORS: Record<string, string> = { "Natural Gas": "#ff9900", Wind: "#00d1ff", Solar: "#ffdd00", Nuclear: "#a78bfa", "Coal and Lignite": "#888888", Hydro: "#3fb950", "Power Storage": "#f85149" };

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-bold tracking-tight">ERCOT Capacity</h1>
        <p className="text-text-secondary text-sm mt-1">Installed generation capacity by fuel type from ERCOT.</p></div>

      {isLoading && <div className="card text-center py-12"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>}

      {entries.length > 0 && (<>
        <div className="card card-compact"><div className="flex flex-wrap gap-6">
          <Metric label="Total Capacity" value={`${(total / 1000).toFixed(1)} GW`} />
          <Metric label="Fuel Types" value={String(entries.length)} />
          <Metric label="Renewables" value={`${(((capacityData["Wind"] ?? 0) + (capacityData["Solar"] ?? 0)) / total * 100).toFixed(1)}%`} />
        </div></div>

        <div className="card">
          <Plot data={[{
            x: entries.map(([f]) => f), y: entries.map(([, v]) => v),
            type: "bar" as const,
            marker: { color: entries.map(([f]) => FUEL_COLORS[f] ?? t.muted) },
            text: entries.map(([, v]) => `${(v / 1000).toFixed(1)} GW`),
            textposition: "outside" as const, textfont: { size: 10, color: t.text },
          }]} layout={{ height: 400, ...L, yaxis: { title: "Capacity (MW)", gridcolor: t.grid } }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
        </div>

        <div className="card">
          <Plot data={[{
            labels: entries.map(([f]) => f), values: entries.map(([, v]) => v),
            type: "pie" as const, hole: 0.4,
            marker: { colors: entries.map(([f]) => FUEL_COLORS[f] ?? t.muted) },
            textinfo: "label+percent", textfont: { size: 10 },
          }]} layout={{ height: 400, paper_bgcolor: "transparent", font: { color: t.text, size: 10 }, margin: { l: 0, r: 0, t: 10, b: 10 }, showlegend: false }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
        </div>

        <div className="card"><div className="overflow-x-auto"><table className="data-table text-xs">
          <thead><tr><th>Fuel Type</th><th>Capacity (MW)</th><th>Share (%)</th></tr></thead>
          <tbody>{entries.map(([fuel, cap]) => (
            <tr key={fuel}><td className="font-semibold" style={{ color: FUEL_COLORS[fuel] }}>{fuel}</td>
              <td className="font-data">{cap.toLocaleString()}</td>
              <td className="font-data">{(cap / total * 100).toFixed(1)}%</td></tr>
          ))}</tbody></table></div></div>
      </>)}
    </div>
  );
}
