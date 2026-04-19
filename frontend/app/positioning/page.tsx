"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import {
  fetchCftcDashboard, fetchCftcHistory, fetchCftcContracts,
  fetchCtaModel, fetchCtaBiasScan, fetchCtaPnl, fetchHistoricalAnalog,
  createAlert, fetchAlerts, fetchAlertFirings,
  type CftcAssetClass, type CftcHeatmapTile, type CftcHistoryRow,
  type CftcContract, type CtaBias, type CtaBiasRow,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Pulse", "Heatmap", "Divergence", "CTA Watch", "CTA Model", "Historical", "Drill-Down"] as const;
type Tab = (typeof TABS)[number];

const ASSET_CLASSES: { key: CftcAssetClass | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "equity", label: "Equities" },
  { key: "rates", label: "Rates" },
  { key: "fx", label: "FX" },
  { key: "energy", label: "Energy" },
  { key: "metals", label: "Metals" },
  { key: "grains", label: "Grains" },
  { key: "softs", label: "Softs" },
  { key: "meats", label: "Meats" },
];

/* ─────────────────────────────────────────────────────────────
   Helpers
   ───────────────────────────────────────────────────────────── */

function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(0)}k`;
  return String(Math.round(n));
}

function pctileColor(p: number | null | undefined): string {
  if (p == null) return "bg-surface-alt text-text-muted";
  if (p >= 0.9) return "bg-loss text-white";
  if (p >= 0.75) return "bg-loss/60 text-white";
  if (p >= 0.55) return "bg-warn/40 text-warn";
  if (p >= 0.45) return "bg-surface-alt text-text-muted";
  if (p >= 0.25) return "bg-gain/30 text-gain";
  if (p >= 0.1) return "bg-gain/60 text-white";
  return "bg-gain text-white";
}

function divergenceColor(z: number | null | undefined): string {
  if (z == null) return "text-text-muted";
  const abs = Math.abs(z);
  if (abs >= 2.5) return z > 0 ? "text-loss" : "text-gain";
  if (abs >= 1.5) return z > 0 ? "text-loss/80" : "text-gain/80";
  if (abs >= 0.75) return "text-warn";
  return "text-text-muted";
}

/* ─────────────────────────────────────────────────────────────
   Tab 1 — Pulse (regime composites + 4 big gauges)
   ───────────────────────────────────────────────────────────── */

function RegimeGauge({ label, value, positiveLabel, negativeLabel, subtitle }: {
  label: string;
  value: number;
  positiveLabel: string;
  negativeLabel: string;
  subtitle: string;
}) {
  // Clamp to ±3 for gauge visual; scale position to -1..1
  const clamped = Math.max(-3, Math.min(3, value));
  const pct = ((clamped + 3) / 6) * 100;
  const color = value >= 1 ? "bg-gain" : value <= -1 ? "bg-loss" : value > 0 ? "bg-gain/60" : value < 0 ? "bg-loss/60" : "bg-text-muted";
  const textColor = value >= 1 ? "text-gain" : value <= -1 ? "text-loss" : "";
  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-xs font-bold uppercase tracking-wider">{label}</span>
        <span className={`text-2xl font-bold font-data ${textColor}`}>{value >= 0 ? "+" : ""}{value.toFixed(2)}</span>
      </div>
      <div className="relative h-2 bg-surface-alt rounded-full overflow-hidden">
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-text-muted/50" />
        <div
          className={`absolute top-0 bottom-0 ${color} transition-all`}
          style={
            value >= 0
              ? { left: "50%", width: `${(pct - 50)}%` }
              : { right: "50%", width: `${(50 - pct)}%` }
          }
        />
      </div>
      <div className="flex justify-between text-[0.55rem] text-text-muted mt-1 font-data">
        <span>← {negativeLabel}</span>
        <span>{positiveLabel} →</span>
      </div>
      <p className="text-[0.65rem] text-text-muted mt-2 leading-snug">{subtitle}</p>
    </div>
  );
}

function PulseTab({ dashboard }: { dashboard: ReturnType<typeof useDashboardQuery> }) {
  const d = dashboard.data;

  if (dashboard.isPending) {
    // Shape-matched skeletons: 4 regime-gauge cards → 2 stat-table cards →
    // CTA P&L chart → AI panel. Same vertical rhythm as the real page to
    // prevent layout shift when data lands.
    return (
      <div className="space-y-5">
        <div>
          <div className="h-4 w-40 rounded bg-surface-alt animate-pulse mb-2" />
          <div className="h-3 w-96 max-w-full rounded bg-surface-alt animate-pulse mb-3" />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="card p-4">
                <div className="flex items-baseline justify-between mb-2">
                  <div className="h-3 w-16 rounded bg-surface-alt animate-pulse" />
                  <div className="h-6 w-12 rounded bg-surface-alt animate-pulse" />
                </div>
                <div className="h-2 w-full rounded-full bg-surface-alt animate-pulse" />
                <div className="h-2 w-3/4 rounded bg-surface-alt animate-pulse mt-3" />
                <div className="h-2 w-2/3 rounded bg-surface-alt animate-pulse mt-1" />
              </div>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="card p-4 space-y-2">
              <div className="h-3 w-32 rounded bg-surface-alt animate-pulse" />
              <div className="h-3 w-full rounded bg-surface-alt animate-pulse" />
              {Array.from({ length: 6 }).map((_, j) => (
                <div key={j} className="h-4 w-full rounded bg-surface-alt animate-pulse opacity-60" />
              ))}
            </div>
          ))}
        </div>
        <div className="card p-4 h-64">
          <div className="h-3 w-40 rounded bg-surface-alt animate-pulse mb-3" />
          <div className="h-48 w-full rounded bg-surface-alt animate-pulse" />
        </div>
      </div>
    );
  }

  if (dashboard.isError || !d) {
    return (
      <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
        Dashboard load failed: {(dashboard.error as Error)?.message ?? "unknown error"}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-sm font-bold uppercase tracking-wider mb-2">Regime Composites</h2>
        <p className="text-xs text-text-muted mb-3">
          Four synthesized positioning signals computed from z-scored managed-money nets across multi-contract baskets.
          Each number is in standard-deviation units. |value| ≥ 1.5 is meaningful, ≥ 2 is extreme.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <RegimeGauge
            label="Risk On/Off"
            value={d.regime.risk_on_off}
            positiveLabel="Risk-on"
            negativeLabel="Risk-off"
            subtitle={d.regime.interpretation.risk_on_off}
          />
          <RegimeGauge
            label="Reflation"
            value={d.regime.reflation}
            positiveLabel="Reflation"
            negativeLabel="Disinflation"
            subtitle={d.regime.interpretation.reflation}
          />
          <RegimeGauge
            label="Safe Haven"
            value={d.regime.safe_haven}
            positiveLabel="Flight-to-safety"
            negativeLabel="Complacent"
            subtitle={d.regime.interpretation.safe_haven}
          />
          <RegimeGauge
            label="Dollar"
            value={d.regime.dollar}
            positiveLabel="Long USD"
            negativeLabel="Short USD"
            subtitle={d.regime.interpretation.dollar}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="card p-4">
          <div className="flex items-baseline justify-between mb-2">
            <h3 className="text-sm font-bold uppercase tracking-wider">Top Divergences</h3>
            <span className="text-[0.6rem] text-text-muted">spec − commercial Z-score</span>
          </div>
          <p className="text-xs text-text-muted mb-2">
            Extreme |Z| = specs crowded one way while commercials (producers) sit the other way. The classic
            2008-oil / 2013-gold / 2020-bond setup.
          </p>
          <table className="w-full text-xs font-data">
            <tbody>
              {d.divergence_top.slice(0, 8).map((r) => (
                <tr key={r.code} className="border-b border-border/40">
                  <td className="py-1.5 px-1 font-semibold w-10">{r.symbol}</td>
                  <td className="py-1.5 px-1 text-text-muted">{r.name}</td>
                  <td className={`py-1.5 px-1 text-right font-bold ${divergenceColor(r.divergence_z)}`}>
                    {r.divergence_z >= 0 ? "+" : ""}{r.divergence_z.toFixed(2)}σ
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card p-4">
          <div className="flex items-baseline justify-between mb-2">
            <h3 className="text-sm font-bold uppercase tracking-wider">CTA Unwind Risk</h3>
            <span className="text-[0.6rem] text-text-muted">crowded × elevated vol</span>
          </div>
          <p className="text-xs text-text-muted mb-2">
            Extremity of positioning combined with an (assumed-median) vol regime. Contracts at the top of this list
            are where a vol spike would force the most deleveraging.
          </p>
          <table className="w-full text-xs font-data">
            <tbody>
              {d.cta_unwind_top.slice(0, 8).map((r) => (
                <tr key={r.code} className="border-b border-border/40">
                  <td className="py-1.5 px-1 font-semibold w-10">{r.symbol}</td>
                  <td className="py-1.5 px-1 text-text-muted">{r.name}</td>
                  <td className="py-1.5 px-1 text-right">
                    <span className={r.direction === "long" ? "text-gain" : "text-loss"}>{r.direction}</span>
                  </td>
                  <td className="py-1.5 px-1 text-right font-bold">{r.unwind_score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <CtaPnlChart />

      <AIInterpretation
        page="positioning"
        data={{
          regime: d.regime,
          top_divergences: d.divergence_top.slice(0, 10),
          top_unwind: d.cta_unwind_top.slice(0, 10),
          top_flows: d.flow_radar_top.slice(0, 10),
        }}
      />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Tab 2 — Heatmap (full cross-asset grid)
   ───────────────────────────────────────────────────────────── */

function HeatmapTab({ tiles }: { tiles: CftcHeatmapTile[] | undefined }) {
  const [filter, setFilter] = useState<CftcAssetClass | "all">("all");

  if (!tiles) return <div className="text-xs text-text-muted">Loading heatmap…</div>;

  const filtered = filter === "all" ? tiles : tiles.filter((t) => t.asset_class === filter);
  // AI payload — slim records so the context stays under 10 KB
  const aiPayload = {
    filter,
    tiles: filtered.map((t) => ({
      symbol: t.symbol, name: t.name, asset_class: t.asset_class,
      pctile_3y: t.pctile_3y, zscore_3y: t.zscore_3y,
      chg_1w: t.chg_1w, divergence_z: t.divergence_z,
    })),
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-1 flex-wrap">
        {ASSET_CLASSES.map((c) => (
          <button
            key={c.key}
            onClick={() => setFilter(c.key)}
            className={`px-3 py-1 text-[0.65rem] rounded-full ${
              filter === c.key ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      <div className="text-[0.65rem] text-text-muted flex flex-wrap gap-3">
        <span><span className="inline-block w-3 h-3 rounded bg-gain align-middle mr-1" />crowded short (buy signal)</span>
        <span><span className="inline-block w-3 h-3 rounded bg-surface-alt align-middle mr-1" />neutral</span>
        <span><span className="inline-block w-3 h-3 rounded bg-loss align-middle mr-1" />crowded long (sell signal)</span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-2">
        {filtered.map((t) => (
          <div
            key={t.code}
            className={`rounded-lg p-3 ${pctileColor(t.pctile_3y)} transition-colors`}
            title={`${t.name} — ${t.pctile_3y != null ? `${Math.round(t.pctile_3y * 100)}th pctile of 3Y` : "—"}, Z ${t.zscore_3y?.toFixed(2) ?? "—"}, net ${fmtInt(t.spec_net)}`}
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-bold">{t.symbol}</span>
              <span className="text-[0.55rem] opacity-80">{t.asset_class}</span>
            </div>
            <div className="text-[0.55rem] opacity-80 leading-tight truncate">{t.name}</div>
            <div className="flex items-baseline gap-1 mt-2">
              <span className="text-lg font-bold font-data">
                {t.pctile_3y != null ? Math.round(t.pctile_3y * 100) : "—"}
              </span>
              <span className="text-[0.55rem] opacity-80">pctile 3Y</span>
            </div>
            <div className="flex justify-between text-[0.55rem] font-data opacity-90 mt-0.5">
              <span>Z {t.zscore_3y != null ? (t.zscore_3y >= 0 ? "+" : "") + t.zscore_3y.toFixed(1) : "—"}</span>
              <span>{t.chg_1w != null && t.chg_1w >= 0 ? "▲" : "▼"} {fmtInt(t.chg_1w)}</span>
            </div>
          </div>
        ))}
      </div>

      <AIInterpretation page="positioning_heatmap" data={aiPayload} buttonLabel="Interpret Heatmap" />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Tab 3 — Divergence
   ───────────────────────────────────────────────────────────── */

function DivergenceTab({ tiles }: { tiles: CftcHeatmapTile[] | undefined }) {
  if (!tiles) return <div className="text-xs text-text-muted">Loading…</div>;
  const ranked = tiles
    .filter((t) => t.divergence_z != null)
    .sort((a, b) => Math.abs((b.divergence_z ?? 0)) - Math.abs((a.divergence_z ?? 0)));
  const aiPayload = {
    ranked: ranked.slice(0, 20).map((t) => ({
      symbol: t.symbol, name: t.name, asset_class: t.asset_class,
      divergence_z: t.divergence_z,
      spec_pctile_3y: t.pctile_3y, comm_pctile_3y: t.comm_pctile_3y,
      spec_net: t.spec_net,
    })),
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-text-muted">
        <strong>How to read:</strong> Z-score of the spread between speculator net and commercial (producer/merchant) net.
        <strong className="text-loss ml-1">Positive</strong> = specs crowded long + commercials crowded short (classic
        bearish contrarian setup). <strong className="text-gain ml-1">Negative</strong> = the inverse (bullish contrarian).
        |Z| ≥ 2 is the historical threshold for extreme setups.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs font-data">
          <thead className="border-b border-border text-text-muted text-left">
            <tr>
              <th className="py-1.5 px-2">Symbol</th>
              <th className="py-1.5 px-2">Name</th>
              <th className="py-1.5 px-2">Class</th>
              <th className="py-1.5 px-2 text-right">Divergence Z</th>
              <th className="py-1.5 px-2 text-right">Spec 3Y</th>
              <th className="py-1.5 px-2 text-right">Comm 3Y</th>
              <th className="py-1.5 px-2 text-right">Spec Net</th>
            </tr>
          </thead>
          <tbody>
            {ranked.slice(0, 30).map((t) => (
              <tr key={t.code} className="border-b border-border/40 hover:bg-surface-alt">
                <td className="py-1 px-2 font-semibold">{t.symbol}</td>
                <td className="py-1 px-2">{t.name}</td>
                <td className="py-1 px-2 text-text-muted">{t.asset_class}</td>
                <td className={`py-1 px-2 text-right font-bold ${divergenceColor(t.divergence_z)}`}>
                  {t.divergence_z != null ? (t.divergence_z >= 0 ? "+" : "") + t.divergence_z.toFixed(2) + "σ" : "—"}
                </td>
                <td className="py-1 px-2 text-right">
                  {t.pctile_3y != null ? Math.round(t.pctile_3y * 100) + "%" : "—"}
                </td>
                <td className="py-1 px-2 text-right">
                  {t.comm_pctile_3y != null ? Math.round(t.comm_pctile_3y * 100) + "%" : "—"}
                </td>
                <td className="py-1 px-2 text-right">{fmtInt(t.spec_net)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <AIInterpretation page="positioning_divergence" data={aiPayload} buttonLabel="Interpret Divergences" />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Tab 4 — CTA Watch (unwind + flow radar)
   ───────────────────────────────────────────────────────────── */

function CtaWatchTab({ dashboard }: { dashboard: ReturnType<typeof useDashboardQuery> }) {
  const d = dashboard.data;
  if (!d) return <div className="text-xs text-text-muted">Loading…</div>;
  const aiPayload = {
    unwind: d.cta_unwind_top,
    flows: d.flow_radar_top,
  };

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-sm font-bold uppercase tracking-wider mb-1">CTA Unwind Risk</h3>
        <p className="text-xs text-text-muted mb-3">
          Score = positioning extremity × realized-vol percentile. Where these align, trend-followers are most likely
          to get force-deleveraged on the next vol spike. Long column = contracts specs are net long; short = net short.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted text-left">
              <tr>
                <th className="py-1.5 px-2">Symbol</th>
                <th className="py-1.5 px-2">Name</th>
                <th className="py-1.5 px-2">Direction</th>
                <th className="py-1.5 px-2 text-right">Pctile 3Y</th>
                <th className="py-1.5 px-2 text-right">Unwind Score</th>
              </tr>
            </thead>
            <tbody>
              {d.cta_unwind_top.map((r) => (
                <tr key={r.code} className="border-b border-border/40 hover:bg-surface-alt">
                  <td className="py-1 px-2 font-semibold">{r.symbol}</td>
                  <td className="py-1 px-2">{r.name}</td>
                  <td className="py-1 px-2">
                    <span className={r.direction === "long" ? "text-gain" : "text-loss"}>{r.direction}</span>
                  </td>
                  <td className="py-1 px-2 text-right">{Math.round(r.pctile_3y * 100)}%</td>
                  <td className="py-1 px-2 text-right font-bold">{r.unwind_score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-bold uppercase tracking-wider mb-1">This-Week Flow Radar</h3>
        <p className="text-xs text-text-muted mb-3">
          Biggest net-position changes over the most recent report, normalized by open interest. Shows where managed
          money actually moved this week (cover/build). Sorted by |change as % of OI|.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted text-left">
              <tr>
                <th className="py-1.5 px-2">Symbol</th>
                <th className="py-1.5 px-2">Name</th>
                <th className="py-1.5 px-2 text-right">WoW Chg</th>
                <th className="py-1.5 px-2 text-right">% OI</th>
                <th className="py-1.5 px-2 text-right">4W Chg</th>
                <th className="py-1.5 px-2 text-right">Pctile 3Y</th>
              </tr>
            </thead>
            <tbody>
              {d.flow_radar_top.map((r) => (
                <tr key={r.code} className="border-b border-border/40 hover:bg-surface-alt">
                  <td className="py-1 px-2 font-semibold">{r.symbol}</td>
                  <td className="py-1 px-2">{r.name}</td>
                  <td className={`py-1 px-2 text-right font-bold ${r.chg_1w >= 0 ? "text-gain" : "text-loss"}`}>
                    {r.chg_1w >= 0 ? "+" : ""}{fmtInt(r.chg_1w)}
                  </td>
                  <td className={`py-1 px-2 text-right ${r.chg_1w_pct_oi >= 0 ? "text-gain" : "text-loss"}`}>
                    {r.chg_1w_pct_oi >= 0 ? "+" : ""}{r.chg_1w_pct_oi.toFixed(1)}%
                  </td>
                  <td className={`py-1 px-2 text-right ${(r.chg_4w ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>
                    {r.chg_4w != null ? (r.chg_4w >= 0 ? "+" : "") + fmtInt(r.chg_4w) : "—"}
                  </td>
                  <td className="py-1 px-2 text-right">
                    {r.pctile_3y != null ? Math.round(r.pctile_3y * 100) + "%" : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <AIInterpretation page="positioning_cta_watch" data={aiPayload} buttonLabel="Interpret CTA Setup" />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Alerts panel — subscribe to CFTC positioning thresholds
   ───────────────────────────────────────────────────────────── */

const CFTC_ALERT_TYPES = [
  { type: "cftc_crowded_long" as const, label: "Crowded long (≥95th pctile)" },
  { type: "cftc_crowded_short" as const, label: "Crowded short (≤5th pctile)" },
  { type: "cftc_sign_flip" as const, label: "Spec net crosses zero" },
  { type: "cftc_new_extreme" as const, label: "New 3Y extreme" },
];

function AlertsPanel({ code, symbol }: { code: string; symbol: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const alertsQ = useQuery({ queryKey: ["user-alerts"], queryFn: fetchAlerts, staleTime: 60_000 });
  const firingsQ = useQuery({ queryKey: ["alert-firings"], queryFn: () => fetchAlertFirings(10), staleTime: 60_000 });

  const existing = (alertsQ.data?.data ?? []).filter((a) =>
    typeof a.alert_type === "string" && a.alert_type.startsWith("cftc_") && a.target === code,
  );
  const firingsForCode = (firingsQ.data?.firings ?? []).filter((f) => f.target === code);

  async function subscribe(type: (typeof CFTC_ALERT_TYPES)[number]["type"], label: string) {
    setBusy(type);
    setStatus("");
    try {
      await createAlert({
        alert_type: type,
        target: code,
        label: `${symbol}: ${label}`,
        channels: ["email"],
      });
      setStatus(`✓ ${label} subscribed`);
      alertsQ.refetch();
    } catch (e) {
      setStatus((e as Error)?.message ?? "Subscription failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card p-4 space-y-2">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-bold uppercase tracking-wider">Alerts for {symbol}</h3>
        {existing.length > 0 && (
          <span className="text-[0.6rem] text-gain">{existing.length} active</span>
        )}
      </div>
      <p className="text-xs text-text-muted">
        Get emailed when this contract hits a positioning threshold. One-click subscription per alert type.
      </p>
      <div className="flex flex-wrap gap-2">
        {CFTC_ALERT_TYPES.map(({ type, label }) => {
          const already = existing.some((a) => a.alert_type === type);
          return (
            <button
              key={type}
              onClick={() => subscribe(type, label)}
              disabled={!!busy || already}
              className={`px-3 py-1.5 text-xs rounded border ${
                already
                  ? "border-gain/40 bg-gain/10 text-gain"
                  : "border-border hover:border-accent hover:bg-accent/10 disabled:opacity-50"
              }`}
            >
              {already ? "✓ " : ""}{label}
            </button>
          );
        })}
      </div>
      {status && <div className="text-[0.65rem] text-text-muted">{status}</div>}

      {firingsForCode.length > 0 && (
        <div className="mt-3 pt-3 border-t border-border/50">
          <div className="text-[0.65rem] font-semibold uppercase tracking-wider text-text-muted mb-1">
            Recent firings for {symbol}
          </div>
          <div className="space-y-1">
            {firingsForCode.slice(0, 5).map((f) => {
              const ctx = f.context as Record<string, string | number | undefined>;
              const label = f.alert_type.replace("cftc_", "").replace("_", " ");
              return (
                <div key={f.id} className="flex items-baseline justify-between text-[0.65rem] font-data">
                  <span className="text-text">
                    <span className="font-semibold text-warn">{label}</span>
                    {ctx.pctile_3y != null && <> · pctile {Math.round(Number(ctx.pctile_3y) * 100)}%</>}
                    {ctx.direction && <> · {String(ctx.direction)}</>}
                    {ctx.extreme && <> · {String(ctx.extreme)}</>}
                  </span>
                  <span className="text-text-muted">
                    {new Date(f.fired_at).toLocaleDateString()}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Tab 5 — Contract drill-down (time series + all derivatives)
   ───────────────────────────────────────────────────────────── */

function DrillDownTab() {
  const contractsQ = useQuery({
    queryKey: ["cftc-contracts"],
    queryFn: () => fetchCftcContracts(),
    staleTime: 24 * 60 * 60_000,
  });
  const [code, setCode] = useState<string>("067651"); // default WTI Crude
  const historyQ = useQuery({
    queryKey: ["cftc-history", code],
    queryFn: () => fetchCftcHistory(code, 260),
    enabled: !!code,
    staleTime: 6 * 60 * 60_000,
  });

  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const rows: CftcHistoryRow[] = historyQ.data?.data ?? [];
  const dates = rows.map((r) => r.date);
  const specNet = rows.map((r) => r.spec_net);
  const commNet = rows.map((r) => r.comm_net ?? null);
  const cotIdx = rows.map((r) => r.cot_index_3y);
  const divZ = rows.map((r) => r.spec_vs_comm_z);

  const contracts: CftcContract[] = useMemo(() => contractsQ.data?.contracts ?? [], [contractsQ.data]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-xs font-semibold">Contract:</label>
        <select
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="px-3 py-1.5 border border-border rounded bg-surface text-sm min-w-[280px]"
        >
          {contracts.map((c) => (
            <option key={c.code} value={c.code}>
              {c.symbol} — {c.name} ({c.asset_class})
            </option>
          ))}
        </select>
        {historyQ.isPending && <span className="text-xs text-text-muted">Loading…</span>}
        {historyQ.isError && <span className="text-xs text-loss">Load failed</span>}
      </div>

      {rows.length > 0 && (() => {
        const last = rows[rows.length - 1];
        return (
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Spec Net" value={fmtInt(last.spec_net)} />
              <Metric label="Pctile 3Y" value={last.spec_pctile_3y != null ? `${Math.round(last.spec_pctile_3y * 100)}%` : "—"} />
              <Metric label="COT Index 3Y" value={last.cot_index_3y != null ? last.cot_index_3y.toFixed(2) : "—"} />
              <Metric label="Z 3Y" value={last.spec_zscore_3y != null ? last.spec_zscore_3y.toFixed(2) : "—"} />
              <Metric label="Divergence Z" value={last.spec_vs_comm_z != null ? last.spec_vs_comm_z.toFixed(2) : "—"} />
              <Metric label="WoW Chg" value={last.spec_chg_1w != null ? (last.spec_chg_1w >= 0 ? "+" : "") + fmtInt(last.spec_chg_1w) : "—"} />
              <Metric label="Reporting Date" value={last.date} />
            </div>
          </div>
        );
      })()}

      <AlertsPanel code={code} symbol={contracts.find((c) => c.code === code)?.symbol ?? code} />

      {rows.length > 0 && (
        <div className="card">
          <Plot
            data={[
              { x: dates, y: specNet, name: "Speculator Net", type: "scatter", mode: "lines", line: { color: t.accent, width: 2 } },
              { x: dates, y: commNet, name: "Commercial Net", type: "scatter", mode: "lines", line: { color: t.spot, width: 1.5 } },
            ]}
            layout={{
              ...L,
              height: CHART_HEIGHT.normal,
              title: { text: "Speculator vs Commercial Net Positions", font: { size: 13, color: t.text } },
              yaxis: { title: { text: "Contracts" }, gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.2 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {rows.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="card">
            <Plot
              data={[{ x: dates, y: cotIdx, name: "COT Index 3Y", type: "scatter", mode: "lines", line: { color: t.accent, width: 2 }, fill: "tozeroy" }]}
              layout={{
                ...L,
                height: CHART_HEIGHT.normal,
                title: { text: "COT Index (0 = 3Y low, 1 = 3Y high)", font: { size: 13, color: t.text } },
                yaxis: { range: [0, 1], gridcolor: t.grid },
                xaxis: { gridcolor: t.grid },
                shapes: [
                  { type: "line", x0: dates[0], x1: dates[dates.length - 1], y0: 0.8, y1: 0.8, line: { color: t.loss, width: 1, dash: "dot" }, yref: "y" },
                  { type: "line", x0: dates[0], x1: dates[dates.length - 1], y0: 0.2, y1: 0.2, line: { color: t.gain, width: 1, dash: "dot" }, yref: "y" },
                ],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
          <div className="card">
            <Plot
              data={[{ x: dates, y: divZ, name: "Divergence Z", type: "scatter", mode: "lines", line: { color: t.hv60, width: 2 } }]}
              layout={{
                ...L,
                height: CHART_HEIGHT.normal,
                title: { text: "Spec vs Commercial Divergence (Z-score)", font: { size: 13, color: t.text } },
                yaxis: { gridcolor: t.grid },
                xaxis: { gridcolor: t.grid },
                shapes: [
                  { type: "line", x0: dates[0], x1: dates[dates.length - 1], y0: 2, y1: 2, line: { color: t.loss, width: 1, dash: "dot" }, yref: "y" },
                  { type: "line", x0: dates[0], x1: dates[dates.length - 1], y0: -2, y1: -2, line: { color: t.gain, width: 1, dash: "dot" }, yref: "y" },
                ],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Tab 6 — CTA Model (ZeroHedge/Nomura framework)
   ───────────────────────────────────────────────────────────── */

function biasColor(b: CtaBias | undefined): string {
  if (b === "all_buying") return "bg-gain text-white";
  if (b === "all_selling") return "bg-loss text-white";
  if (b === "mixed") return "bg-warn/30 text-warn";
  if (b === "neutral") return "bg-surface-alt text-text-muted";
  return "bg-surface-alt text-text-muted";
}

function biasLabel(b: CtaBias | undefined): string {
  switch (b) {
    case "all_buying": return "Buying in all scenarios";
    case "all_selling": return "Selling in all scenarios";
    case "mixed": return "Mixed";
    case "neutral": return "Neutral";
    default: return "—";
  }
}

function CtaModelTab() {
  const biasQ = useQuery({
    queryKey: ["cta-bias-scan"],
    queryFn: fetchCtaBiasScan,
    staleTime: 6 * 60 * 60_000,
  });
  const [selected, setSelected] = useState<string>("13874A"); // default ES
  const modelQ = useQuery({
    queryKey: ["cta-model", selected],
    queryFn: () => fetchCtaModel(selected),
    enabled: !!selected,
    staleTime: 6 * 60 * 60_000,
  });

  const m = modelQ.data;
  const biasRows: CtaBiasRow[] = biasQ.data?.rows ?? [];
  const allBuying = biasRows.filter((r) => r.bias_1w === "all_buying");
  const allSelling = biasRows.filter((r) => r.bias_1w === "all_selling");
  const mixed = biasRows.filter((r) => r.bias_1w === "mixed" || r.bias_1w === "neutral");

  return (
    <div className="space-y-5">
      <div className="card card-compact">
        <p className="text-xs text-text-muted">
          <strong>Framework:</strong> Replicates Nomura / GS CTA desk readouts (the &ldquo;we see buying in all scenarios&rdquo;
          language from ZeroHedge). Each contract runs an SMA+breakout+momentum ensemble on the underlying futures.
          <em className="text-gain ml-1">All buying</em> = CTAs add exposure in every price scenario over the horizon.
          <em className="text-loss ml-1">All selling</em> = forced net selling regardless of direction.
          <em className="text-warn ml-1">Mixed</em> = behavior depends on which way price moves.
        </p>
      </div>

      {/* Bias tiles — quick glance */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-4 border-l-4 border-l-gain">
          <div className="text-sm font-bold uppercase tracking-wider text-gain mb-1">All Buying (1w)</div>
          <div className="text-[0.65rem] text-text-muted mb-2">Asymmetric upside — flows in every scenario</div>
          <div className="flex flex-wrap gap-1">
            {allBuying.length > 0
              ? allBuying.map((r) => (
                  <button
                    key={r.code}
                    onClick={() => setSelected(r.code)}
                    className="px-2 py-0.5 rounded border border-gain/40 text-xs font-data hover:bg-gain/10"
                  >
                    {r.symbol}
                  </button>
                ))
              : <span className="text-xs text-text-muted">None this week</span>}
          </div>
        </div>
        <div className="card p-4 border-l-4 border-l-loss">
          <div className="text-sm font-bold uppercase tracking-wider text-loss mb-1">All Selling (1w)</div>
          <div className="text-[0.65rem] text-text-muted mb-2">Forced-seller set-up — flows negative everywhere</div>
          <div className="flex flex-wrap gap-1">
            {allSelling.length > 0
              ? allSelling.map((r) => (
                  <button
                    key={r.code}
                    onClick={() => setSelected(r.code)}
                    className="px-2 py-0.5 rounded border border-loss/40 text-xs font-data hover:bg-loss/10"
                  >
                    {r.symbol}
                  </button>
                ))
              : <span className="text-xs text-text-muted">None this week</span>}
          </div>
        </div>
        <div className="card p-4 border-l-4 border-l-warn">
          <div className="text-sm font-bold uppercase tracking-wider text-warn mb-1">Mixed / Neutral</div>
          <div className="text-[0.65rem] text-text-muted mb-2">Flow depends on direction — watch triggers</div>
          <div className="flex flex-wrap gap-1">
            {mixed.slice(0, 20).map((r) => (
              <button
                key={r.code}
                onClick={() => setSelected(r.code)}
                className="px-2 py-0.5 rounded border border-border text-xs font-data hover:bg-surface-alt"
              >
                {r.symbol}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Selected contract detail */}
      {m && m.available && (
        <div className="card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-bold">{m.symbol} — {m.name}</h3>
              <div className="text-[0.6rem] text-text-muted">
                Last: {m.last_price.toFixed(2)} · yfinance: {m.yf_symbol}
              </div>
            </div>
            <div className={`px-3 py-1 rounded text-xs font-bold ${biasColor(m.scenarios?.bias_1w)}`}>
              1W: {biasLabel(m.scenarios?.bias_1w)}
            </div>
            <div className={`px-3 py-1 rounded text-xs font-bold ${biasColor(m.scenarios?.bias_1m)}`}>
              1M: {biasLabel(m.scenarios?.bias_1m)}
            </div>
          </div>

          {/* Exposure bar */}
          <div>
            <div className="flex justify-between text-[0.65rem] text-text-muted mb-1">
              <span>Short -100</span>
              <span>Current exposure</span>
              <span>Long +100</span>
            </div>
            <div className="relative h-3 bg-surface-alt rounded-full overflow-hidden">
              <div className="absolute left-1/2 top-0 bottom-0 w-px bg-text-muted/50" />
              <div
                className={`absolute top-0 bottom-0 ${(m.exposure ?? 0) >= 0 ? "bg-gain" : "bg-loss"}`}
                style={{
                  left: (m.exposure ?? 0) >= 0 ? "50%" : `${50 + (m.exposure ?? 0) / 2}%`,
                  width: `${Math.abs((m.exposure ?? 0)) / 2}%`,
                }}
              />
            </div>
            <div className="text-center text-lg font-bold font-data mt-1">
              {(m.exposure ?? 0) >= 0 ? "+" : ""}{m.exposure?.toFixed(1)}
            </div>
          </div>

          {/* Scenario tables */}
          {m.scenarios && (["1w", "1m"] as const).map((h) => {
            const flows = m.scenarios!.horizons[h];
            if (!flows) return null;
            const sigma = h === "1w" ? m.scenarios!.vol_1w_pct : m.scenarios!.vol_1m_pct;
            return (
              <div key={h}>
                <div className="text-xs font-semibold mb-1 uppercase tracking-wider">
                  {h === "1w" ? "1-Week" : "1-Month"} scenarios
                  {sigma != null && <span className="text-text-muted ml-2 font-normal">1σ = ±{sigma.toFixed(2)}%</span>}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs font-data">
                    <thead className="text-text-muted border-b border-border">
                      <tr>
                        <th className="text-left py-1 px-2">Scenario</th>
                        <th className="text-right py-1 px-2">Target Price</th>
                        <th className="text-right py-1 px-2">Δ Exposure</th>
                        <th className="text-right py-1 px-2">Projected Exposure</th>
                      </tr>
                    </thead>
                    <tbody>
                      {["down_2sig", "down_1sig", "flat", "up_1sig", "up_2sig"].map((k) => {
                        const v = flows[k];
                        if (!v) return null;
                        return (
                          <tr key={k} className="border-b border-border/30">
                            <td className="py-1 px-2 capitalize">{k.replace("_", " ").replace("sig", "σ")}</td>
                            <td className="py-1 px-2 text-right">{v.target_price.toFixed(2)}</td>
                            <td className={`py-1 px-2 text-right font-semibold ${v.delta_exposure > 0 ? "text-gain" : v.delta_exposure < 0 ? "text-loss" : "text-text-muted"}`}>
                              {v.delta_exposure > 0 ? "+" : ""}{v.delta_exposure.toFixed(1)}
                            </td>
                            <td className="py-1 px-2 text-right">
                              {v.projected_exposure > 0 ? "+" : ""}{v.projected_exposure.toFixed(1)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}

          {/* Trigger ladder */}
          <div>
            <div className="text-xs font-semibold mb-1 uppercase tracking-wider">Nearest Triggers</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-data">
                <thead className="text-text-muted border-b border-border">
                  <tr>
                    <th className="text-left py-1 px-2">Trigger</th>
                    <th className="text-right py-1 px-2">Level</th>
                    <th className="text-right py-1 px-2">Distance</th>
                    <th className="text-right py-1 px-2">Flip to</th>
                  </tr>
                </thead>
                <tbody>
                  {m.triggers?.slice(0, 6).map((t, i) => (
                    <tr key={i} className="border-b border-border/30">
                      <td className="py-1 px-2">{t.type} {t.window}</td>
                      <td className="py-1 px-2 text-right">{t.level.toFixed(2)}</td>
                      <td className={`py-1 px-2 text-right ${t.distance_pct > 0 ? "text-gain" : "text-loss"}`}>
                        {t.distance_pct > 0 ? "+" : ""}{t.distance_pct.toFixed(2)}%
                      </td>
                      <td className={`py-1 px-2 text-right font-semibold ${t.side_if_breached === "long" ? "text-gain" : "text-loss"}`}>
                        {t.side_if_breached}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {m && !m.available && (
        <div className="card text-sm text-text-muted py-4 px-5">
          Price data unavailable for this contract ({m.reason ?? "no yfinance mapping"}).
        </div>
      )}

      {m && m.available && (
        <AIInterpretation
          page="positioning_cta_model"
          subject={m.name ?? m.symbol ?? undefined}
          data={{
            symbol: m.symbol, name: m.name, asset_class: m.asset_class,
            last_price: m.last_price,
            exposure: m.exposure,
            bias_1w: m.scenarios?.bias_1w,
            bias_1m: m.scenarios?.bias_1m,
            vol_1w_pct: m.scenarios?.vol_1w_pct,
            triggers: m.triggers?.slice(0, 8),
            scenarios_1w: m.scenarios?.horizons?.["1w"],
            scenarios_1m: m.scenarios?.horizons?.["1m"],
          }}
          buttonLabel="Interpret CTA Model"
        />
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Tab 7 — Historical analog
   ───────────────────────────────────────────────────────────── */

function HistoricalTab() {
  const analogQ = useQuery({
    queryKey: ["cftc-historical-analog"],
    queryFn: () => fetchHistoricalAnalog(5),
    staleTime: 6 * 60 * 60_000,
  });

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <p className="text-xs text-text-muted">
          <strong>Framework:</strong> Build a positioning vector for each historical week (spec percentile + divergence Z
          across all 45 contracts). Compare today&apos;s vector to every prior week via cosine similarity. Top-5 most similar
          historical weeks are shown with the SPY forward 1-month and 3-month return that followed each. Not a
          prediction — base-rate evidence.
        </p>
      </div>

      {analogQ.isPending && <div className="text-xs text-text-muted">Scanning historical positioning vectors…</div>}
      {analogQ.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
          Historical analog failed: {(analogQ.error as Error)?.message ?? "unknown error"}
        </div>
      )}
      {analogQ.data && (
        <div>
          <div className="text-xs text-text-muted mb-2">
            Current reporting date: <span className="font-semibold">{analogQ.data.current_date}</span>
          </div>
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted">
              <tr>
                <th className="text-left py-1.5 px-2">Analog Week</th>
                <th className="text-right py-1.5 px-2">Cosine Similarity</th>
                <th className="text-right py-1.5 px-2">SPY +1M</th>
                <th className="text-right py-1.5 px-2">SPY +3M</th>
              </tr>
            </thead>
            <tbody>
              {analogQ.data.analogs.map((a, i) => (
                <tr key={i} className="border-b border-border/40">
                  <td className="py-1 px-2 font-semibold">{a.date}</td>
                  <td className="py-1 px-2 text-right">{a.cosine_similarity.toFixed(4)}</td>
                  <td className={`py-1 px-2 text-right ${(a.spy_fwd_1m ?? 0) > 0 ? "text-gain" : "text-loss"}`}>
                    {a.spy_fwd_1m != null ? (a.spy_fwd_1m > 0 ? "+" : "") + a.spy_fwd_1m.toFixed(2) + "%" : "—"}
                  </td>
                  <td className={`py-1 px-2 text-right ${(a.spy_fwd_3m ?? 0) > 0 ? "text-gain" : "text-loss"}`}>
                    {a.spy_fwd_3m != null ? (a.spy_fwd_3m > 0 ? "+" : "") + a.spy_fwd_3m.toFixed(2) + "%" : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {analogQ.data.analogs.length > 0 && (() => {
            const m1 = analogQ.data.analogs.map((a) => a.spy_fwd_1m).filter((x): x is number => x != null);
            const m3 = analogQ.data.analogs.map((a) => a.spy_fwd_3m).filter((x): x is number => x != null);
            const avg1 = m1.length > 0 ? m1.reduce((s, x) => s + x, 0) / m1.length : null;
            const avg3 = m3.length > 0 ? m3.reduce((s, x) => s + x, 0) / m3.length : null;
            const pos1 = m1.filter((x) => x > 0).length;
            const pos3 = m3.filter((x) => x > 0).length;
            return (
              <div className="card card-compact mt-4 text-xs">
                <strong>Base rates across these analogs:</strong>{" "}
                {avg1 != null && <>SPY 1M avg <span className={avg1 > 0 ? "text-gain" : "text-loss"}>{avg1 > 0 ? "+" : ""}{avg1.toFixed(2)}%</span> ({pos1}/{m1.length} positive)</>}
                {avg3 != null && <> · SPY 3M avg <span className={avg3 > 0 ? "text-gain" : "text-loss"}>{avg3 > 0 ? "+" : ""}{avg3.toFixed(2)}%</span> ({pos3}/{m3.length} positive)</>}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   CTA P&L chart (embedded in Pulse tab)
   ───────────────────────────────────────────────────────────── */

function CtaPnlChart() {
  const q = useQuery({
    queryKey: ["cftc-cta-pnl"],
    queryFn: () => fetchCtaPnl(156),
    staleTime: 6 * 60 * 60_000,
  });
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  if (q.isPending) {
    return <div className="card h-64 bg-surface-alt animate-pulse rounded-lg" />;
  }
  if (q.isError || !q.data || q.data.dates.length === 0) {
    return (
      <div className="card card-compact text-xs text-text-muted">
        CTA P&L reconstruction unavailable.
      </div>
    );
  }

  const last = q.data.cumulative[q.data.cumulative.length - 1];
  const totalRet = (last - 1) * 100;

  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <h3 className="text-sm font-bold uppercase tracking-wider">Reconstructed CTA P&L</h3>
          <p className="text-[0.6rem] text-text-muted mt-0.5">
            Managed-money positioning × forward weekly returns, OI-weighted. Approximates CTA composite performance.
            Not a fund return — a signal-quality proxy.
          </p>
        </div>
        <div className="text-right">
          <div className={`text-lg font-bold font-data ${totalRet >= 0 ? "text-gain" : "text-loss"}`}>
            {totalRet >= 0 ? "+" : ""}{totalRet.toFixed(1)}%
          </div>
          <div className="text-[0.55rem] text-text-muted">3Y cumulative</div>
        </div>
      </div>
      <Plot
        data={[{
          x: q.data.dates,
          y: q.data.cumulative,
          name: "Cumulative",
          type: "scatter",
          mode: "lines",
          line: { color: t.accent, width: 2 },
          fill: "tozeroy",
        }]}
        layout={{
          ...L,
          height: CHART_HEIGHT.compact,
          yaxis: { title: { text: "Cumulative (1.0 = start)" }, gridcolor: t.grid },
          xaxis: { gridcolor: t.grid },
          margin: { l: 50, r: 20, t: 10, b: 30 },
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%" }}
      />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Top-level page
   ───────────────────────────────────────────────────────────── */

function useDashboardQuery() {
  return useQuery({
    queryKey: ["cftc-dashboard"],
    queryFn: fetchCftcDashboard,
    // Data refreshes Friday 3:30pm ET only — 6h is plenty.
    staleTime: 6 * 60 * 60_000,
    retry: 1,
  });
}

export default function PositioningPage() {
  const [tab, setTab] = useState<Tab>("Pulse");
  const dashboard = useDashboardQuery();

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Positioning — CFTC</h1>
        <p className="text-text-secondary text-sm mt-1">
          Weekly Commitments of Traders across 45 flagship contracts. Managed-money & leveraged-funds net vs commercials,
          with percentiles, COT Index, divergence Z, and synthesized regime composites.
        </p>
      </div>

      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              tab === t ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div>
        {tab === "Pulse" && <PulseTab dashboard={dashboard} />}
        {tab === "Heatmap" && <HeatmapTab tiles={dashboard.data?.heatmap} />}
        {tab === "Divergence" && <DivergenceTab tiles={dashboard.data?.heatmap} />}
        {tab === "CTA Watch" && <CtaWatchTab dashboard={dashboard} />}
        {tab === "CTA Model" && <CtaModelTab />}
        {tab === "Historical" && <HistoricalTab />}
        {tab === "Drill-Down" && <DrillDownTab />}
      </div>

      <div className="card card-compact text-[11px] text-text-muted">
        <strong>About the data:</strong> CFTC publishes four weekly reports every Friday at 3:30pm ET covering positioning
        as of the prior Tuesday close. Commodities use the Disaggregated report (Managed Money = CTAs + hedge funds).
        Financials (equities, rates, FX) use Traders in Financial Futures (Leveraged Funds). Commercials come from the
        Legacy report where available (history to 1986) for the longest-baseline divergence signal. Source:
        <a href="https://publicreporting.cftc.gov/" target="_blank" rel="noreferrer" className="text-accent hover:underline ml-1">
          CFTC Public Reporting API
        </a>.
      </div>
    </div>
  );
}
