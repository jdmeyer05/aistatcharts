"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import { ChartCard } from "@/components/ui/chart-card";
import { AIInterpretation } from "@/components/ai-interpretation";
import {
  fetchCapacityAdditions,
  fetchCapitalReference,
  fetchGridLoad,
  type CapacityAdditions,
  type CapitalReference,
  type GridLoad,
  type GridLoadRow,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, getPlotConfig, useIsMobile, CHART_HEIGHT } from "@/lib/chart-theme";

const TABS = ["Chain Overview", "Grid Reality", "Capacity Additions"] as const;
type Tab = (typeof TABS)[number];

const STALE = 60 * 60_000; // these series move monthly at best

export default function AiInfrastructurePage() {
  const [tab, setTab] = useState<Tab>("Chain Overview");

  const gridQ = useQuery({ queryKey: ["ai-infra-grid"], queryFn: () => fetchGridLoad(), staleTime: STALE });
  const capQ = useQuery({ queryKey: ["ai-infra-capacity"], queryFn: () => fetchCapacityAdditions(), staleTime: STALE });
  const refQ = useQuery({ queryKey: ["ai-infra-capital"], queryFn: () => fetchCapitalReference(), staleTime: STALE });

  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold tracking-tight">AI &amp; Data Center Infrastructure</h1>
        <p className="text-sm text-text-muted max-w-4xl leading-relaxed">
          The build-out is a physical chain with a financial chain bolted to it, and lead times across
          the links differ by more than an order of magnitude. This page measures the gaps between
          adjacent links using realised data — metered demand and operating generators — rather than
          announcements.
        </p>
      </header>

      {/* Says loading / failed / refreshing before any tile is read. Three
          queries feed this page and they fail independently. */}
      <PageStatus
        queries={[
          { name: "grid load", q: gridQ },
          { name: "capacity additions", q: capQ },
          { name: "capital reference", q: refQ },
        ]}
      />

      <nav className="flex flex-wrap gap-1 border-b border-border">
        {TABS.map((tb) => (
          <button
            key={tb}
            onClick={() => setTab(tb)}
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors border-b-2 -mb-px ${
              tab === tb
                ? "border-accent text-accent"
                : "border-transparent text-text-muted hover:text-text"
            }`}
          >
            {tb}
          </button>
        ))}
      </nav>

      {tab === "Chain Overview" && <ChainOverview gridQ={gridQ} capQ={capQ} refQ={refQ} />}
      {tab === "Grid Reality" && <GridReality query={gridQ} />}
      {tab === "Capacity Additions" && <CapacityTab query={capQ} />}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   Shared primitives
   ───────────────────────────────────────────────────────────── */

function Kpi({
  label, value, sub, tone = "neutral", pending = false, error,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "gain" | "loss" | "neutral" | "accent";
  /** Still fetching. A tile MUST NOT show "—" while loading: an em-dash is a
   *  statement that the number is absent, and it reads identically to a failed
   *  request and to a genuinely empty result. */
  pending?: boolean;
  error?: string | null;
}) {
  const color =
    tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : tone === "accent" ? "text-accent" : "text-text";
  return (
    <div className="card p-3">
      <div className="text-[0.65rem] font-bold uppercase tracking-wider text-text-muted mb-1">{label}</div>
      {pending ? (
        <>
          <div className="h-7 w-24 rounded animate-pulse bg-text-muted/20" />
          <div className="text-[0.6rem] text-text-muted mt-1">loading…</div>
        </>
      ) : error ? (
        <>
          <div className="text-xl font-bold font-data text-loss">—</div>
          <div className="text-[0.6rem] text-loss mt-1 leading-snug">couldn&apos;t load: {error}</div>
        </>
      ) : (
        <>
          <div className={`text-xl font-bold font-data ${color}`}>{value}</div>
          {sub && <div className="text-[0.65rem] text-text-muted mt-1 leading-snug">{sub}</div>}
        </>
      )}
    </div>
  );
}

/** One line saying whether the page is loading, broken, or showing real data.
 *  Three independent queries feed it and they can fail separately, so a single
 *  "—" on a tile never told a reader which of those they were looking at. */
function PageStatus({ queries }: { queries: { name: string; q: { isPending: boolean; isFetching: boolean; error: Error | null; refetch: () => void } }[] }) {
  const pending = queries.filter((x) => x.q.isPending);
  const failed = queries.filter((x) => x.q.error);
  const refreshing = queries.filter((x) => !x.q.isPending && x.q.isFetching);

  if (failed.length) {
    return (
      <div className="card p-3 border-l-4 border-l-loss space-y-1">
        <div className="text-xs font-bold text-loss">
          {failed.length} of {queries.length} data sources failed to load
        </div>
        {failed.map(({ name, q }) => (
          <div key={name} className="text-[0.7rem] text-text-muted">
            <span className="text-text">{name}</span>: {q.error?.message}
          </div>
        ))}
        <button
          type="button"
          onClick={() => failed.forEach(({ q }) => q.refetch())}
          className="text-[0.7rem] text-accent hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }
  if (pending.length) {
    return (
      <div className="card p-3 flex items-center gap-2">
        <span className="inline-block w-3.5 h-3.5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        <span className="text-xs text-text-muted">
          Loading {pending.map((x) => x.name).join(", ")}… these series are fetched live from EIA and
          can take a few seconds on a cold start.
        </span>
      </div>
    );
  }
  if (refreshing.length) {
    return (
      <div className="card p-3 text-xs text-text-muted">
        Refreshing {refreshing.map((x) => x.name).join(", ")}… showing the last values meanwhile.
      </div>
    );
  }
  return null;
}

/** Provenance strip. Every number on this page states where it came from and
 *  what it is not — the sector's headline figures are routinely quoted without
 *  their definitions, and that is the main way this page could mislead. */
function Provenance({ source, caveat, curated }: { source: string; caveat: string; curated?: boolean }) {
  return (
    <div className="card p-3 border-l-4 border-l-accent/60 space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-[0.6rem] font-bold uppercase tracking-wider text-accent">
          {curated ? "Curated figures" : "Source"}
        </span>
        <span className="text-xs text-text-muted font-data">{source}</span>
      </div>
      <p className="text-xs text-text-muted leading-relaxed">{caveat}</p>
    </div>
  );
}

function Loading({ h = 420 }: { h?: number }) {
  return <div className="card animate-pulse bg-surface-alt/60" style={{ height: h }} />;
}

function ErrorBox({ msg }: { msg: string }) {
  return <div className="card p-4 text-sm text-loss">{msg}</div>;
}

const pct = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;
/** Percentage POINTS. A spread between two percentages is not itself a
 *  percentage, and labelling it "%" invites reading 0.59pp as 0.59%. */
const pp = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)} pp`;
const gw = (mw: number) => `${(mw / 1000).toFixed(1)} GW`;
/** Collapse a range to one value when the bounds round to the same figure —
 *  "8–8%" reads as a rendering fault rather than a tight range. */
const range = (lo: number, hi: number, d = 1, unit = "") => {
  const a = lo.toFixed(d);
  const b = hi.toFixed(d);
  return a === b ? `${a}${unit}` : `${a}–${b}${unit}`;
};

/* ─────────────────────────────────────────────────────────────
   TAB 1 — Chain Overview
   ───────────────────────────────────────────────────────────── */

function ChainOverview({
  gridQ, capQ, refQ,
}: {
  gridQ: ReturnType<typeof useQuery<GridLoad, Error>>;
  capQ: ReturnType<typeof useQuery<CapacityAdditions, Error>>;
  refQ: ReturnType<typeof useQuery<CapitalReference, Error>>;
}) {
  const g = gridQ.data;
  const c = capQ.data;
  const r = refQ.data;

  const preferred = r?.revenue_scopes.find((s) => s.preferred);

  // Net capacity added across the flagged BAs, against their load growth. This
  // is the divergence the page exists to show: demand is metered, supply is
  // counted, and neither is an announcement.
  const flaggedNet = useMemo(() => {
    if (!c) return null;
    const rows = c.by_ba.filter((b) => b.dc_flagged);
    return {
      added: rows.reduce((a, b) => a + b.added_mw, 0),
      retired: rows.reduce((a, b) => a + b.planned_retirement_mw, 0),
      net: rows.reduce((a, b) => a + b.net_mw, 0),
    };
  }, [c]);

  const flaggedDeltaTwh = useMemo(
    () => g?.rows.filter((x) => x.dc_flagged).reduce((a, b) => a + (b.delta_twh ?? 0), 0) ?? null,
    [g],
  );

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Each tile carries the state of the query that actually feeds it, so
            a capital-reference failure does not make the grid tiles look empty
            and vice versa. */}
        <Kpi
          label="Capex committed, 2026"
          pending={refQ.isPending}
          error={refQ.error?.message ?? null}
          value={r ? `$${r.capex.subtotal_low_usd_bn.toFixed(0)}–${r.capex.subtotal_high_usd_bn.toFixed(0)}bn` : "—"}
          sub={r ? `${r.capex.pct_of_us_gdp_low.toFixed(2)}–${r.capex.pct_of_us_gdp_high.toFixed(2)}% of US GDP · four calendar-year reporters` : undefined}
          tone="accent"
        />
        <Kpi
          label="Coverage — end demand"
          pending={refQ.isPending}
          error={refQ.error?.message ?? null}
          value={preferred ? range(preferred.coverage_low_pct, preferred.coverage_high_pct, 1, "%") : "—"}
          sub={preferred ? `${preferred.scope} ÷ capex. Run-rate vs annual flow.` : undefined}
          tone="neutral"
        />
        <Kpi
          label="Metered load growth"
          pending={gridQ.isPending}
          error={gridQ.error?.message ?? null}
          value={pct(g?.aggregate.dc_flagged)}
          sub={
            g
              ? `DC-flagged BAs, trailing 12m. Spread vs others ${pp(g.aggregate.spread_pp)}.`
              : undefined
          }
          tone={g?.aggregate.dc_flagged != null && g.aggregate.dc_flagged > 0 ? "gain" : "neutral"}
        />
        <Kpi
          label="Build less attrition"
          pending={capQ.isPending || gridQ.isPending}
          error={capQ.error?.message ?? gridQ.error?.message ?? null}
          value={flaggedNet ? gw(flaggedNet.net) : "—"}
          sub={
            flaggedNet && c
              ? `${gw(flaggedNet.added)} entered service ${c.addition_window} · ${gw(flaggedNet.retired)} retiring ${c.retirement_window}`
              : undefined
          }
          tone={flaggedNet && flaggedNet.net > 0 ? "gain" : "loss"}
        />
      </div>

      {/* The chain, stated plainly */}
      <div className="card p-4 space-y-3">
        <h2 className="text-sm font-bold uppercase tracking-wider text-text-muted">What ties out, and what does not</h2>
        <div className="space-y-2 text-sm leading-relaxed">
          <p>
            <span className="font-bold text-accent">Capital is committed years ahead of electrons.</span>{" "}
            {r && `$${r.capex.subtotal_low_usd_bn.toFixed(0)}–${r.capex.subtotal_high_usd_bn.toFixed(0)}bn of 2026 capex guidance`}
            {flaggedDeltaTwh != null && ` sits against ${flaggedDeltaTwh.toFixed(1)} TWh of additional metered demand`}
            {g && ` across the ${g.aggregate.n_flagged} balancing authorities where data center activity is concentrated.`}
          </p>
          <p>
            <span className="font-bold text-accent">Demand is real but not yet exceptional in aggregate.</span>{" "}
            {g &&
              `Flagged BAs grew ${pct(g.aggregate.dc_flagged)} against ${pct(g.aggregate.not_flagged)} elsewhere — a spread of ${g.aggregate.spread_pp?.toFixed(2)} pp. ` +
              (Math.abs(g.aggregate.spread_pp ?? 0) < 1
                ? "That spread is small. Announced load is not yet showing up as a decisive difference in metered demand."
                : "That spread is wide enough to be visible in metered demand.")}
          </p>
          <p>
            <span className="font-bold text-accent">Supply is being added, and attrition is smaller than the build.</span>{" "}
            {flaggedNet && c &&
              `${gw(flaggedNet.added)} of generation entered service in flagged BAs over ${c.addition_window}, against ${gw(flaggedNet.retired)} scheduled to retire across ${c.retirement_window} — a difference of ${gw(flaggedNet.net)}. The two figures cover different periods, so treat this as build rate against near-term attrition rather than a balance.`}
          </p>
        </div>
        <p className="text-xs text-text-muted leading-relaxed border-t border-border pt-3">
          Announced capacity, contracted capacity, approved capacity and energised capacity are four
          different quantities. Everything on this page is realised: metered demand and generators
          that are actually running. Nothing here is a queue, a pipeline or a forecast.
        </p>
      </div>

      {r && (
        <>
          <CoverageTable r={r} />
          <CapexTable r={r} />
          <Provenance source="Company guidance and published estimates, each dated in the tables above" caveat={r.caveat} curated />
          <AIInterpretation page="ai-infra-capital" subject="capital committed vs revenue earned" data={r} />
        </>
      )}

      {(gridQ.isPending || capQ.isPending || refQ.isPending) && <Loading h={160} />}
      {/* All three, not just the capital one. A grid or capacity failure used
          to be silent here while its tiles showed a bare em-dash. */}
      {gridQ.error && <ErrorBox msg={`Grid load: ${gridQ.error.message}`} />}
      {capQ.error && <ErrorBox msg={`Capacity additions: ${capQ.error.message}`} />}
      {refQ.error && <ErrorBox msg={`Capital reference: ${refQ.error.message}`} />}
    </div>
  );
}

function CoverageTable({ r }: { r: CapitalReference }) {
  return (
    <div className="card p-0 overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-bold">Coverage ratio by scope</h3>
        <p className="text-xs text-text-muted mt-1">
          Revenue ÷ 2026 capex. The ratio moves roughly elevenfold across published definitions of
          &ldquo;AI revenue&rdquo;, so a single headline number would be a presentation choice, not a finding.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-alt">
            <tr className="text-xs font-bold uppercase tracking-wider text-text-muted">
              <th className="text-left px-3 py-2">Scope</th>
              <th className="text-right px-3 py-2">Revenue</th>
              <th className="text-right px-3 py-2">Coverage</th>
              <th className="text-left px-3 py-2">As of</th>
              <th className="text-left px-3 py-2">Note</th>
            </tr>
          </thead>
          <tbody>
            {r.revenue_scopes.map((s) => (
              <tr
                key={s.scope}
                className={`border-t border-border ${s.preferred ? "bg-accent/5" : ""}`}
              >
                <td className="px-3 py-2 font-bold">
                  {s.scope}
                  {s.preferred && (
                    <span className="ml-2 text-[0.6rem] font-bold uppercase tracking-wider text-accent">preferred</span>
                  )}
                  {s.double_counts && (
                    <span className="ml-2 text-[0.6rem] font-bold uppercase tracking-wider text-loss">double-counts</span>
                  )}
                  <div className="text-xs text-text-muted font-normal">{s.detail}</div>
                </td>
                <td className="px-3 py-2 text-right font-data">${s.value_usd_bn.toFixed(0)}bn</td>
                <td className={`px-3 py-2 text-right font-data font-bold ${s.double_counts ? "text-text-muted" : "text-accent"}`}>
                  {s.coverage_low_pct.toFixed(1)}–{s.coverage_high_pct.toFixed(1)}%
                </td>
                <td className="px-3 py-2 text-xs text-text-muted font-data">{s.as_of}</td>
                <td className="px-3 py-2 text-xs text-text-muted max-w-md">{s.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CapexTable({ r }: { r: CapitalReference }) {
  return (
    <div className="card p-0 overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-bold">2026 capital expenditure guidance</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-alt">
            <tr className="text-xs font-bold uppercase tracking-wider text-text-muted">
              <th className="text-left px-3 py-2">Entity</th>
              <th className="text-left px-3 py-2">Basis</th>
              <th className="text-right px-3 py-2">2026</th>
              <th className="text-right px-3 py-2">Prior</th>
              <th className="text-left px-3 py-2">As of</th>
            </tr>
          </thead>
          <tbody>
            {r.capex.entities.map((e) => (
              <tr key={e.entity} className="border-t border-border">
                <td className="px-3 py-2 font-bold">{e.entity}</td>
                <td className="px-3 py-2 text-xs text-text-muted">{e.basis}</td>
                <td className="px-3 py-2 text-right font-data">
                  {e.low_usd_bn === e.high_usd_bn
                    ? `$${e.low_usd_bn.toFixed(0)}bn`
                    : `$${e.low_usd_bn.toFixed(0)}–${e.high_usd_bn.toFixed(0)}bn`}
                </td>
                <td className="px-3 py-2 text-right font-data text-text-muted">
                  {e.prior_usd_bn ? `$${e.prior_usd_bn.toFixed(0)}bn` : "—"}
                </td>
                <td className="px-3 py-2 text-xs text-text-muted font-data">{e.as_of}</td>
              </tr>
            ))}
            <tr className="border-t-2 border-accent/40 bg-surface-alt/50">
              <td className="px-3 py-2 font-bold" colSpan={2}>Subtotal — calendar-year reporters</td>
              <td className="px-3 py-2 text-right font-data font-bold text-accent">
                ${r.capex.subtotal_low_usd_bn.toFixed(0)}–{r.capex.subtotal_high_usd_bn.toFixed(0)}bn
              </td>
              <td className="px-3 py-2 text-right font-data text-text-muted">
                ${r.capex.prior_year_partial_usd_bn.toFixed(0)}bn*
              </td>
              <td />
            </tr>
            {r.capex.non_additive.map((e) => (
              <tr key={e.entity} className="border-t border-border bg-warn/5">
                <td className="px-3 py-2 font-bold">
                  {e.entity}
                  <span className="ml-2 text-[0.6rem] font-bold uppercase tracking-wider text-warn">not additive</span>
                </td>
                <td className="px-3 py-2 text-xs text-text-muted">{e.basis}</td>
                <td className="px-3 py-2 text-right font-data">
                  ${e.fy26_usd_bn.toFixed(1)}bn <span className="text-text-muted">→ ${e.fy27_guided_usd_bn.toFixed(0)}bn</span>
                </td>
                <td className="px-3 py-2 text-xs text-text-muted" colSpan={2}>{e.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="px-4 py-2 text-[0.7rem] text-text-muted border-t border-border">
        * Prior-year subtotal covers only the entities that disclosed a comparable figure, so it
        understates the true 2025 base. Oracle reports on a May fiscal year and is excluded from the
        subtotal — summing it against calendar-year guidance would add different periods together.
      </p>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   TAB 2 — Grid Reality
   ───────────────────────────────────────────────────────────── */

function GridReality({ query }: { query: ReturnType<typeof useQuery<GridLoad, Error>> }) {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const isMobile = useIsMobile();
  const [onlyFlagged, setOnlyFlagged] = useState(false);

  const d = query.data;
  const rows = useMemo(
    () => (!d ? [] : onlyFlagged ? d.rows.filter((r) => r.dc_flagged) : d.rows),
    [d, onlyFlagged],
  );

  /** Two traces rather than a per-point colour array, so the flagged/unflagged
   *  split gets a real legend. Colour alone with the meaning buried in the
   *  subtitle is not a key. `categoryarray` pins the sort so the split traces
   *  still land in rank order. */
  const splitTraces = useMemo(
    () =>
      (value: (r: GridLoadRow) => number, hover: string) => {
        if (!rows.length) return { traces: [], order: [] as string[] };
        const sorted = [...rows].sort((a, b) => value(a) - value(b));
        const order = sorted.map((r) => r.ba);
        const build = (flagged: boolean, name: string, color: string) => {
          const subset = sorted.filter((r) => r.dc_flagged === flagged);
          return {
            type: "bar" as const,
            orientation: "h" as const,
            name,
            x: subset.map(value),
            y: subset.map((r) => r.ba),
            marker: { color },
            customdata: subset.map((r) => [r.name, r.delta_twh ?? 0, r.trailing_12m_twh]),
            hovertemplate: hover,
          };
        };
        return {
          traces: [
            build(true, "Data-center-concentrated", t.accent),
            build(false, "Other", t.muted),
          ],
          order,
        };
      },
    [rows, t],
  );

  const growth = useMemo(
    () =>
      splitTraces(
        (r) => r.growth_pct ?? 0,
        "<b>%{y}</b> — %{customdata[0]}<br>growth %{x:.2f}%<br>Δ %{customdata[1]:.1f} TWh<br>trailing 12m %{customdata[2]:.0f} TWh<extra></extra>",
      ),
    [splitTraces],
  );

  // Absolute TWh matters more than percentage for the large BAs — 2% on PJM is
  // a bigger physical event than 11% on a small one. Charted separately so the
  // percentage view cannot mislead on its own.
  const delta = useMemo(
    () =>
      splitTraces(
        (r) => r.delta_twh ?? 0,
        "<b>%{y}</b> — %{customdata[0]}<br>Δ %{x:.2f} TWh<extra></extra>",
      ),
    [splitTraces],
  );

  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorBox msg={query.error.message} />;
  if (!d) return null;

  const spreadSmall = Math.abs(d.aggregate.spread_pp ?? 0) < 1;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi label="DC-flagged BAs" value={pct(d.aggregate.dc_flagged)} sub={`${d.aggregate.n_flagged} BAs, demand-weighted`} tone="accent" />
        <Kpi label="Not flagged" value={pct(d.aggregate.not_flagged)} sub={`${d.aggregate.n_not_flagged} BAs, demand-weighted`} />
        <Kpi
          label="Spread"
          value={pp(d.aggregate.spread_pp)}
          sub={spreadSmall ? "Small — not a decisive difference" : "Visible in metered demand"}
          tone={spreadSmall ? "neutral" : "gain"}
        />
        <Kpi label="All BAs" value={pct(d.aggregate.all)} sub={`${d.window.recent[0]}–${d.window.recent[1]} vs prior year`} />
      </div>

      {spreadSmall && (
        <div className="card p-3 border-l-4 border-l-warn/70">
          <div className="text-[0.6rem] font-bold uppercase tracking-wider text-warn mb-1">Absence of signal</div>
          <p className="text-xs leading-relaxed">
            The demand-weighted spread between data-center-concentrated balancing authorities and the
            rest is {d.aggregate.spread_pp?.toFixed(2)} percentage points. Announced data center load
            is not yet producing a decisive difference in metered demand at the balancing-authority
            level. That is a finding, not a gap in the data.
          </p>
        </div>
      )}

      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} className="w-4 h-4 accent-accent" />
        <span className="font-bold uppercase tracking-wider text-text-muted">Data-center-flagged BAs only</span>
      </label>

      <ChartCard
        title="Demand growth by balancing authority — trailing 12m vs prior 12m"
        subtitle={`Realised metered demand. Highlighted bars are BAs where data center activity is publicly concentrated (editorial flag). Window: ${d.window.recent[0]}–${d.window.recent[1]} against ${d.window.prior[0]}–${d.window.prior[1]}.`}
        height={CHART_HEIGHT.tall}
      >
        <Plot
          data={growth.traces}
          layout={getBaseLayout(t, {
            barmode: "overlay",
            showlegend: true,
            legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
            xaxis: { title: "Growth (%)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.text },
            yaxis: { gridcolor: t.grid, automargin: true, categoryorder: "array", categoryarray: growth.order },
            margin: { l: 60, r: 20, t: 10, b: 40 },
          })}
          config={getPlotConfig(isMobile)}
          style={{ width: "100%", height: CHART_HEIGHT.tall }}
        />
      </ChartCard>

      <ChartCard
        title="Absolute demand added — TWh"
        subtitle="The same data in physical units. A small percentage on a large BA can be a far larger event than a large percentage on a small one."
        height={CHART_HEIGHT.tall}
      >
        <Plot
          data={delta.traces}
          layout={getBaseLayout(t, {
            barmode: "overlay",
            showlegend: true,
            legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
            xaxis: { title: "Δ TWh (trailing 12m less prior 12m)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.text },
            yaxis: { gridcolor: t.grid, automargin: true, categoryorder: "array", categoryarray: delta.order },
            margin: { l: 60, r: 20, t: 10, b: 40 },
          })}
          config={getPlotConfig(isMobile)}
          style={{ width: "100%", height: CHART_HEIGHT.tall }}
        />
      </ChartCard>

      <GridTable rows={rows} />
      <Provenance source={d.source} caveat={d.caveat} />

      {d.excluded.length > 0 && (
        <div className="card p-3 text-xs text-text-muted">
          <span className="font-bold">Excluded for incomplete coverage:</span>{" "}
          {d.excluded.map((e) => `${e.ba} (${(e.coverage * 100).toFixed(0)}%)`).join(", ")}
        </div>
      )}

      <AIInterpretation
        page="ai-infra-grid"
        subject="realised grid load growth"
        data={{
          window: d.window,
          aggregate: d.aggregate,
          excluded: d.excluded,
          // Drop the 24-point monthly series per BA — the payload is for
          // interpretation, and the aggregates plus per-BA totals carry the story.
          // eslint-disable-next-line @typescript-eslint/no-unused-vars -- destructured to OMIT
          rows: d.rows.map(({ monthly, ...rest }) => rest),
        }}
      />
    </div>
  );
}

function GridTable({ rows }: { rows: GridLoadRow[] }) {
  return (
    <div className="card p-0 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-alt sticky top-0">
            <tr className="text-xs font-bold uppercase tracking-wider text-text-muted">
              <th className="text-left px-3 py-2">BA</th>
              <th className="text-left px-3 py-2">Region</th>
              <th className="text-right px-3 py-2">Prior 12m</th>
              <th className="text-right px-3 py-2">Trailing 12m</th>
              <th className="text-right px-3 py-2">Δ TWh</th>
              <th className="text-right px-3 py-2">Growth</th>
              <th className="text-left px-3 py-2">Why flagged</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ba} className="border-t border-border hover:bg-surface-alt/40">
                <td className="px-3 py-2 font-bold font-data">
                  {r.ba}
                  <span className="text-text-muted text-xs font-normal ml-2">{r.name}</span>
                </td>
                <td className="px-3 py-2 text-xs text-text-muted">{r.region}</td>
                <td className="px-3 py-2 text-right font-data text-text-muted">{r.prior_12m_twh.toFixed(1)}</td>
                <td className="px-3 py-2 text-right font-data">{r.trailing_12m_twh.toFixed(1)}</td>
                <td className={`px-3 py-2 text-right font-data ${(r.delta_twh ?? 0) > 0 ? "text-gain" : "text-loss"}`}>
                  {r.delta_twh == null ? "—" : `${r.delta_twh >= 0 ? "+" : ""}${r.delta_twh.toFixed(1)}`}
                </td>
                <td className={`px-3 py-2 text-right font-data font-bold ${(r.growth_pct ?? 0) > 0 ? "text-gain" : "text-loss"}`}>
                  {pct(r.growth_pct)}
                </td>
                <td className="px-3 py-2 text-xs text-text-muted max-w-sm">{r.dc_note ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   TAB 3 — Capacity Additions
   ───────────────────────────────────────────────────────────── */

function CapacityTab({ query }: { query: ReturnType<typeof useQuery<CapacityAdditions, Error>> }) {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const isMobile = useIsMobile();

  const d = query.data;

  const netTraces = useMemo(() => {
    if (!d) return [];
    const rows = [...d.by_ba].filter((r) => r.added_mw > 0).sort((a, b) => a.net_mw - b.net_mw);
    return [
      {
        type: "bar" as const,
        orientation: "h" as const,
        name: "Added",
        x: rows.map((r) => r.added_mw / 1000),
        y: rows.map((r) => r.ba),
        marker: { color: t.gain },
        hovertemplate: "<b>%{y}</b><br>added %{x:.1f} GW<extra></extra>",
      },
      {
        type: "bar" as const,
        orientation: "h" as const,
        name: "Scheduled retirement",
        x: rows.map((r) => -r.planned_retirement_mw / 1000),
        y: rows.map((r) => r.ba),
        marker: { color: t.loss },
        hovertemplate: "<b>%{y}</b><br>retiring %{x:.1f} GW<extra></extra>",
      },
    ];
  }, [d, t]);

  const techTraces = useMemo(() => {
    if (!d) return [];
    // Drop the final year only when the snapshot is mid-year. A December
    // snapshot makes that year complete, and dropping it would throw away a
    // full year of data for no reason.
    const completeYears = d.partial_final_year ? d.years.slice(0, -1) : d.years;
    return d.by_technology.map((tech) => ({
      type: "bar" as const,
      name: tech.technology,
      x: completeYears,
      y: completeYears.map((y) => (tech.by_year[y] ?? 0) / 1000),
      marker: { color: TECH_COLORS[tech.technology] ?? t.muted },
      hovertemplate: `<b>${tech.technology}</b><br>%{x}: %{y:.1f} GW<extra></extra>`,
    }));
  }, [d, t]);

  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorBox msg={query.error.message} />;
  if (!d) return null;

  const totalAdded = d.by_ba.reduce((a, b) => a + b.added_mw, 0);
  const totalRetire = d.by_ba.reduce((a, b) => a + b.planned_retirement_mw, 0);
  const finalYear = d.years[d.years.length - 1];

  return (
    <div className="space-y-5">
      {/* The two columns cover different periods on purpose. Saying so up front
          is cheaper than letting someone read the difference as a balance. */}
      <div className="card p-3 border-l-4 border-l-accent/60">
        <p className="text-xs leading-relaxed">
          <span className="font-bold">Two different windows.</span> Additions are units that
          actually entered service in <span className="font-data">{d.addition_window}</span>.
          Retirements are operating units carrying a retirement date in{" "}
          <span className="font-data">{d.retirement_window}</span>. The difference is a
          build-rate-versus-attrition comparison, not a balance — and nothing here is a pipeline,
          because EIA&rsquo;s API publishes operable units only.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi label="Entered service" value={gw(totalAdded)} sub={`${d.addition_window}, realised`} tone="gain" />
        <Kpi label="Retiring" value={gw(totalRetire)} sub={`Scheduled ${d.retirement_window}`} tone="loss" />
        <Kpi
          label="Build less attrition"
          value={gw(totalAdded - totalRetire)}
          sub="Across the two windows above"
          tone={totalAdded - totalRetire > 0 ? "gain" : "loss"}
        />
        <Kpi
          label="Snapshot"
          value={d.snapshot}
          sub={d.partial_final_year ? `${finalYear} is a partial year` : "Full-year data"}
        />
      </div>

      <ChartCard
        title="Entered service against scheduled retirements, by balancing authority"
        subtitle="Gross additions are what get quoted; the difference after attrition is what changes the supply picture. A BA building heavily while retiring heavily is treading water."
        height={CHART_HEIGHT.tall}
      >
        <Plot
          data={netTraces}
          layout={getBaseLayout(t, {
            barmode: "relative",
            showlegend: true,
            legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
            xaxis: { title: "GW", gridcolor: t.grid, zeroline: true, zerolinecolor: t.text },
            yaxis: { gridcolor: t.grid, automargin: true },
            margin: { l: 60, r: 20, t: 10, b: 40 },
          })}
          config={getPlotConfig(isMobile)}
          style={{ width: "100%", height: CHART_HEIGHT.tall }}
        />
      </ChartCard>

      <ChartCard
        title="Additions by technology and year"
        subtitle={
          (d.partial_final_year
            ? `Complete calendar years only — ${finalYear} is excluded because the snapshot is mid-year and annualising it would overstate the run rate. `
            : "") +
          "Solar and storage interconnect fastest but are not firm capacity; a mix dominated by them against firm-load growth is a reliability story, not a solved one."
        }
        height={CHART_HEIGHT.normal}
      >
        <Plot
          data={techTraces}
          layout={getBaseLayout(t, {
            barmode: "stack",
            showlegend: true,
            legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
            xaxis: { title: "Year entered service", gridcolor: t.grid },
            yaxis: { title: "GW", gridcolor: t.grid, rangemode: "tozero" },
          })}
          config={getPlotConfig(isMobile)}
          style={{ width: "100%", height: CHART_HEIGHT.normal }}
        />
      </ChartCard>

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-alt sticky top-0">
              <tr className="text-xs font-bold uppercase tracking-wider text-text-muted">
                <th className="text-left px-3 py-2">BA</th>
                <th className="text-right px-3 py-2">Entered service</th>
                <th className="text-right px-3 py-2">Retiring</th>
                <th className="text-right px-3 py-2">Difference</th>
                <th className="text-right px-3 py-2">Fleet</th>
                <th className="text-right px-3 py-2">Fleet renewed</th>
              </tr>
            </thead>
            <tbody>
              {d.by_ba.map((r) => (
                <tr key={r.ba} className="border-t border-border hover:bg-surface-alt/40">
                  <td className="px-3 py-2 font-bold font-data">
                    {r.ba}
                    <span className="text-text-muted text-xs font-normal ml-2">{r.name}</span>
                    {r.dc_flagged && <span className="ml-2 text-[0.6rem] font-bold uppercase tracking-wider text-accent">DC</span>}
                  </td>
                  <td className="px-3 py-2 text-right font-data text-gain">{(r.added_mw / 1000).toFixed(1)}</td>
                  <td className="px-3 py-2 text-right font-data text-loss">{(r.planned_retirement_mw / 1000).toFixed(1)}</td>
                  <td className={`px-3 py-2 text-right font-data font-bold ${r.net_mw > 0 ? "text-gain" : "text-loss"}`}>
                    {(r.net_mw / 1000).toFixed(1)}
                  </td>
                  <td className="px-3 py-2 text-right font-data text-text-muted">{(r.operating_mw / 1000).toFixed(1)}</td>
                  <td className="px-3 py-2 text-right font-data">
                    {r.added_pct_of_fleet == null ? "—" : `${r.added_pct_of_fleet.toFixed(1)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-4 py-2 text-[0.7rem] text-text-muted border-t border-border">
          All capacity figures GW. &ldquo;Fleet renewed&rdquo; is capacity that entered service in{" "}
          {d.addition_window} as a share of the balancing authority&rsquo;s current operable fleet —
          how much of the generation mix is new.
        </p>
      </div>

      <Provenance source={d.source} caveat={d.caveat} />
      <AIInterpretation page="ai-infra-capacity" subject="realised generation additions" data={d} />
    </div>
  );
}

const TECH_COLORS: Record<string, string> = {
  Solar: "#E8B23A",
  Storage: "#6C8EBF",
  Wind: "#5FA8A0",
  "Gas — combined cycle": "#B5654A",
  "Gas — peaker": "#D18F6E",
  "Gas — other": "#C7A48B",
  Nuclear: "#8E6FB0",
  Hydro: "#4E80A8",
  Coal: "#6B6B6B",
  Oil: "#8C7B6B",
  Geothermal: "#7E9B76",
  "Biomass & waste": "#A3B18A",
  Other: "#9AA3AD",
};
