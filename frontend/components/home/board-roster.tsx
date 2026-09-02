"use client";

/**
 * The boards that used to be cards, as one line each, grouped by how fast they
 * move.
 *
 * SEVEN, NOT NINE. Tweet watch is gone rather than demoted: it is a model's own
 * relevance score with no reference set, it costs a 17.6s scrape-plus-model
 * call on every load, and it is not read. `/trump-decoder` is the page for it.
 * Its home card is deleted with it — a component nothing renders is the rot
 * this change exists to remove.
 *
 * WHAT CHANGED AND WHY. Home had grown to seventeen top-level blocks by
 * addition without subtraction: four synthesis layers were stacked on top to
 * say where to look, and no board was ever removed from underneath. The result
 * showed the same information two and three times at different resolutions —
 * `UnusualToday` ranked four boards that also rendered as full cards below it,
 * and the ES card carried mini CTA and macro verdicts that the swing band
 * rendered in full.
 *
 * NOTHING IS HIDDEN, WHICH IS THE WHOLE DESIGN CONSTRAINT. `HorizonBand`'s
 * docstring rejects defaulting a band shut, and it is right to: folding work
 * away behind a bare chevron hides it. A row here is not a chevron. It carries
 * the board's headline reading, where that reading sits in its own history, and
 * how old it is — so a reader who never opens a row has still been told what
 * the board says. Opening one renders the original card, unchanged.
 *
 * GROUPED BY INFORMATION HALF-LIFE, which is the axis this page always had and
 * which the first cut of this file threw away.
 *
 * That first cut ranked every row by distance from the middle of its own
 * history. It was the wrong axis and the component said so itself, five times:
 * only THREE of eight boards carry a reference set to be ranked against, so
 * five rows rendered a variant of "no reference set" in the column that was
 * supposed to be the ranking signal. Two rounds went into making that label
 * tidier before it was clear the label was not the problem.
 *
 * Half-life answers the question a daily reader actually has — do I need to
 * read this again this morning — and it applies to all of them. Percentile
 * answers a different question, and it is still shown where a board has one; it
 * just no longer decides the order. `HorizonBand`'s hints are the point of the
 * grouping: "unchanged since this morning" over a board is the single most
 * useful thing this page can say about something it is asking you to look at.
 *
 * A BOARD THAT CANNOT BE PLACED SAYS SO ONCE, at the foot of the section,
 * rather than once per row. It is still not rendered as quiet — this project
 * has shipped an absence-as-calm in at least six places — but repeating the
 * same absence five times in the same column is noise, not honesty.
 *
 * EVERY CARD IS A `next/dynamic` IMPORT, and that is the only part of this that
 * touches the bundle. Measured across four production builds: deleting the
 * entire swing half of the page — its render AND its import — changed the
 * route's JavaScript by zero bytes, because the boards were static imports of
 * `home-client.tsx` and tree-shaking does not cross the "use client" boundary.
 * Collapsing never saved a byte and neither would a route split. Reaching each
 * card through `dynamic()` is what finally does: the two Plotly boards (sector
 * rotation, CTA flows) now pull the 568KB Plotly chunk only when a reader opens
 * one of those two rows.
 *
 * THE QUERIES ARE THE CARDS' OWN. Same keys, same cadences — copied, not
 * approximated. React Query dedupes on the key, so an open row and this roster
 * share one request; a closed row keeps the entry current rather than letting
 * it rot, which is the bug the retired ribbon was written to avoid. Key parity also
 * keeps `PageInterpretation` working: it subscribes to these exact keys with
 * `enabled: false` and would otherwise synthesise a page it could no longer see.
 */

import { useMemo, type ComponentType } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  fetchHeatmap,
  fetchVolLandscape,
  fetchSectorRrg,
  fetchMacroPressure,
  fetchCtaFlows,
  fetchSpValuation,
  fetchFedProbabilities,
  type VolLandscapeScan,
  type SectorRrg,
  type MacroPressureBoard,
  type CtaFlowBoard,
  type SpValuation,
  type FedProbabilities,
  type HeatmapItem,
} from "@/lib/api";
import { ordinal } from "@/lib/home-constants";
import { useStickyBoolean, minutesSince, useMinuteClock } from "@/components/home/primitives";

/* ─── lazy cards ──────────────────────────────────────────────────
   Each its own `dynamic()` call rather than one shared module, so opening the
   valuation row does not also download Plotly. `ssr: false` because none of
   these can render before their query resolves anyway, and prerendering them
   would put the markup back into the 310KB of HTML this change exists to cut. */

/** The options object is repeated at every call site rather than hoisted into a
 *  shared const, which reads like duplication and is not: `next/dynamic`
 *  requires an object LITERAL so its compiler plugin can read `ssr` statically,
 *  and a hoisted `lazy` const fails the build with "next/dynamic options must
 *  be an object literal". */
function Loading() {
  return <div className="text-[0.65rem] text-text-muted py-3">Loading the board…</div>;
}

const SectorRelativeCard = dynamic(
  () => import("@/components/home/boards-lazy").then((m) => m.SectorRelative),
  { ssr: false, loading: Loading });
const VolLandscapeCard = dynamic(
  () => import("@/components/home/boards-lazy").then((m) => m.VolLandscapeSnapshot),
  { ssr: false, loading: Loading });
const SectorRrgCard = dynamic(
  () => import("@/components/home/sector-rrg"), { ssr: false, loading: Loading });
const CtaFlowsCard = dynamic(
  () => import("@/components/home/cta-flows"), { ssr: false, loading: Loading });
const MacroPressureCard = dynamic(
  () => import("@/components/home/macro-pressure"), { ssr: false, loading: Loading });
const FedProbabilitiesCard = dynamic(
  () => import("@/components/home/fed-probabilities"), { ssr: false, loading: Loading });
const SpValuationCard = dynamic(
  () => import("@/components/home/sp-valuation"), { ssr: false, loading: Loading });

/* ─── helpers ─────────────────────────────────────────────────── */

/** Every comparison against NaN is false, so a bounds check is never a null
 *  check. This project has shipped that bug three times, once 500ing a live
 *  route — so the guard is explicit and every adapter below goes through it. */
function isNum(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n);
}

function fmt(n: unknown, digits = 2): string {
  return isNum(n) ? n.toFixed(digits) : "—";
}

/** A row's reference, in the two states it actually has.
 *
 *  `pctile` is ALWAYS 0..100 by the time it reaches a Row. The macro board
 *  reports 0..1 while every other source here reports 0..100, and mixing them
 *  puts every macro factor at "the 0th percentile" — an extreme reading
 *  manufactured out of a unit mismatch, on the exact axis this component ranks
 *  by. Normalisation happens in the macro adapter, at its own edge. */
type Ref =
  /** `qualifier` is rendered INLINE beside the ordinal, not parked in a title
   *  attribute, for rows whose percentile does not rank a level. Valuation
   *  ranks how expensive a reading is, so "100th" there means the most
   *  expensive on record — which for a yield is the LOWEST on record. A caveat
   *  that only appears on hover is a caveat most readers never see. */
  | { placed: true; pctile: number; note: string; qualifier?: string }
  /** Not "quiet" — unmeasured. The reason is stated in numbers where numbers
   *  exist, because "no reference set" and "20 of the 60 sessions needed" are
   *  different findings and a reader deserves to know which one they have. */
  | { placed: false; why: string };

/** Whether a board has answered yet, and how it failed if it did.
 *
 *  A BOARD THAT FAILED AND A BOARD THAT IS STILL LOADING RENDERED IDENTICALLY
 *  in the first cut of this file — both got an em dash. That is the single
 *  most-repeated bug in this project (an absence rendered as a calm, found in
 *  six other places), and it is worse on a roster than on a card, because a
 *  card at least shows a spinner while a row is one quiet line. */
type BoardState = { pending: boolean; error: boolean };

/** How fast a board can change — the axis the page is organised on.
 *
 *  Taken from what each endpoint actually is, not from its refetch interval:
 *  the valuation strip sat on a 60-minute refetch while describing something
 *  that moves on a quarterly earnings cycle, and that mismatch is exactly what
 *  made every card wear identical live styling. */
type HalfLife = "intraday" | "multiday" | "slow";

const BANDS: { id: HalfLife; label: string; hint: string }[] = [
  { id: "intraday", label: "Today", hint: "moves through the session, resets overnight" },
  { id: "multiday", label: "Days", hint: "unchanged since this morning — a lean, not a trigger" },
  { id: "slow", label: "Weeks to months", hint: "worth re-reading when the week turns, not today" },
];

type Row = {
  key: string;
  name: string;
  halfLife: HalfLife;
  /** The board's headline reading, or null when there is no data to read. Null
   *  is rendered from `state` rather than as a dash, so "still loading" and
   *  "this request failed" say different things. */
  value: string | null;
  state: BoardState;
  /** What the reading means at this end of its range, or what it measures when
   *  there is no range. Shown under the row when it is open. */
  meaning: string;
  ref: Ref;
  /** The dedicated page, where one exists. Valuation and the Fed path have no
   *  page anywhere in the app — they lived only on this home page — which is
   *  why every row expands in place rather than merely linking out. */
  href?: string;
  updatedAt: number;
  Card: ComponentType;
};

/** Same key and cadence as the card, so React Query dedupes to one request
 *  while a row is open and this keeps the entry current while it is closed.
 *
 *  Returns four PRIMITIVES rather than the query object, and that matters: the
 *  rows below are built in a `useMemo`, and a hook returning a fresh `{...}`
 *  every render would put a new object in that memo's dependency array every
 *  render, so the memo would recompute every time and memoise nothing. */
function useBoard<T>(queryKey: unknown[], queryFn: () => Promise<T>, refetchInterval: number) {
  // `isPending` alone would read as "loading" for a query that has failed and
  // is sitting between retries, so the error flag is carried separately and
  // wins in the render.
  const { data, dataUpdatedAt, isError, isPending } = useQuery<T>({
    queryKey,
    queryFn,
    refetchInterval,
    staleTime: refetchInterval * 0.8,
  });
  // MEMOISED HERE, at the source, rather than left to every caller.
  //
  // Returning a bare object literal mints a new identity on every render, and
  // the roster builds its rows in a `useMemo` keyed on these — so eight fresh
  // objects per render meant that memo recomputed every time while looking as
  // though it memoised. Spreading the fields into the caller's dependency array
  // instead would fix the recompute but trips
  // `react-hooks/preserve-manual-memoization`: the rule infers `vol` where the
  // array says `vol.data`, calls the memoisation unpreservable, and skips
  // optimising the component. Making the identity itself stable satisfies both.
  return useMemo(
    () => ({ data, updatedAt: dataUpdatedAt, error: isError, pending: isPending }),
    [data, dataUpdatedAt, isError, isPending],
  );
}

/** One board's load state, in the shape the rows carry. */
function stateOf(b: { error: boolean; pending: boolean }): BoardState {
  return { pending: b.pending, error: b.error };
}

/* ─── the roster ──────────────────────────────────────────────── */

export default function BoardRoster() {
  const heatmap = useBoard<{ group: string; items: HeatmapItem[] }>(
    ["heatmap", "sectors"], () => fetchHeatmap("sectors"), 60_000);
  const vol = useBoard<VolLandscapeScan>(
    ["vol-landscape-home"], fetchVolLandscape, 5 * 60_000);
  const rrg = useBoard<SectorRrg>(
    ["sector-rrg", 8], () => fetchSectorRrg(8), 30 * 60_000);
  const macro = useBoard<MacroPressureBoard>(
    ["macro-pressure"], fetchMacroPressure, 30 * 60_000);
  const cta = useBoard<CtaFlowBoard>(
    ["cta-flows", "13874A"], () => fetchCtaFlows("13874A"), 30 * 60_000);
  const fed = useBoard<FedProbabilities>(
    ["fed-probabilities", 4], () => fetchFedProbabilities(4), 30 * 60_000);
  const val = useBoard<SpValuation>(
    ["sp-valuation"], fetchSpValuation, 60 * 60_000);
  // NO TRUMP MONITOR QUERY. `/api/trump/monitor` measures 17.6s — a scrape plus
  // a model call — and removing the row removes that request from every home
  // load, which is the largest single latency this file touches.
  const nowMin = useMinuteClock();

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];

    /* ── vol landscape ── */
    {
      const d = vol.data;
      const h = d?.history?.avg_iv;
      out.push({
        key: "vol",
        name: "Vol landscape",
        halfLife: "intraday",
        value: d?.summary
          ? `avg IV ${fmt(d.summary.avg_iv, 1)} · IV/HV ${fmt(d.summary.avg_ivhv)} · ${
              d.summary.n_inverted ?? "—"} of ${d.summary.n_tickers ?? "—"} inverted`
          : null,
        state: stateOf(vol),
        meaning:
          "What options across the scanned universe are pricing, against what the underlyings " +
          "have actually delivered.",
        ref: isNum(h?.pctile)
          ? { placed: true, pctile: h.pctile, note: `of ${h.n_history} recorded sessions` }
          : {
              placed: false,
              why: isNum(h?.n_history)
                ? `${h.n_history} of the 60 sessions needed`
                : "no reference set",
            },
        href: "/vol-landscape",
        updatedAt: vol.updatedAt,
        Card: VolLandscapeCard,
      });
    }

    /* ── sector rotation: three regime measures, ranked to the most extreme ── */
    {
      const r = rrg.data?.regime;
      const measures = [
        ["Sector tilt", r?.tilt] as const,
        ["Sector dispersion", r?.dispersion] as const,
        ["Sector correlation", r?.correlation] as const,
      ].filter((m) => isNum(m[1]?.pctile));
      // The most extreme measure is the one worth surfacing. Reporting a fixed
      // one would bury a 0.6th-percentile correlation reading under a tilt
      // sitting at the 48th, which is the middle of its own range.
      const top = measures.sort(
        (a, b) => Math.abs((b[1]!.pctile as number) - 50) - Math.abs((a[1]!.pctile as number) - 50)
      )[0];
      out.push({
        key: "rrg",
        name: "Sector rotation",
        halfLife: "slow",
        value: top
          ? `${top[0].toLowerCase()} ${fmt(top[1]!.value, 3)}${top[1]!.band ? ` · ${top[1]!.band}` : ""}`
          : null,
        state: stateOf(rrg),
        meaning:
          "Where sector leadership sits — how concentrated, how far apart, and how much the " +
          "sectors are moving together.",
        ref: top
          ? {
              placed: true,
              pctile: top[1]!.pctile as number,
              note: isNum(top[1]!.n_history)
                ? `of ${top[1]!.n_history} recorded weeks`
                : "against its own history",
            }
          : { placed: false, why: "no reference set" },
        href: "/sector-analysis",
        updatedAt: rrg.updatedAt,
        Card: SectorRrgCard,
      });
    }

    /* ── sector relative: today's spread, and no reference set anywhere ── */
    {
      // Sorted over the sectors that actually reported a change. A comparator
      // that sees `undefined` returns NaN, which leaves `Array.sort` free to
      // produce any order at all — and the best/worst pair is read off the ends
      // of this array, so a single missing print could name the wrong sectors.
      const items = (heatmap.data?.items ?? []).filter((s) => isNum(s.change));
      const sorted = [...items].sort((a, b) => b.change - a.change);
      const top = sorted[0];
      const bottom = sorted[sorted.length - 1];
      const up = sorted.filter((s) => s.change > 0).length;
      // Needs two DIFFERENT sectors: one reporting sector makes "Energy to
      // Energy spread 0.00pp", which is a sentence about nothing.
      const spread = sorted.length >= 2 ? top.change - bottom.change : null;
      out.push({
        key: "heatmap",
        name: "Sector relative",
        halfLife: "intraday",
        value: sorted.length
          ? `${up} of ${sorted.length} green${
              isNum(spread) ? ` · ${top.label} to ${bottom.label} spread ${fmt(spread)}pp` : ""}`
          : null,
        state: stateOf(heatmap),
        meaning:
          "Today's dispersion in isolation. A wide spread means the index move is not the whole " +
          "story; a narrow one means everything moved together.",
        // The heatmap endpoint returns symbol, label, price and change. There
        // is no history behind it, so there is no percentile to report and
        // inventing one would be worse than saying this.
        ref: { placed: false, why: "no reference set — today's levels only" },
        href: "/sector-analysis",
        updatedAt: heatmap.updatedAt,
        Card: SectorRelativeCard,
      });
    }

    /* ── macro pressure: rows carry 0..1 percentiles. See the Ref docstring. ── */
    {
      const d = macro.data;
      const placeable = (d?.rows ?? []).filter(
        (row) =>
          isNum(row.pctile) &&
          // Excluded upstream from the net score for the same reason: a change
          // window comparing one print to itself is missing data, not a neutral
          // reading.
          !(isNum(row.stale_days) && row.stale_days > 30)
      );
      const top = placeable.sort(
        (a, b) => Math.abs((b.pctile as number) * 100 - 50) - Math.abs((a.pctile as number) * 100 - 50)
      )[0];
      out.push({
        key: "macro",
        name: "Macro pressure",
        halfLife: "multiday",
        // GATED ON `available`, not on the payload existing. An unavailable
        // board still deserialises to an object, and reading counts off it
        // produced "— · 0↑ 0– 0↓" — three zeros that look like a measured
        // balance and are actually a board that did not report.
        value: d?.available
          ? `${d.net_label ?? "—"} · ${d.counts?.supportive ?? 0}↑ ${d.counts?.neutral ?? 0}– ${
              d.counts?.headwind ?? 0}↓${
              isNum(d.net_from_n) && isNum(d.net_total_n) && d.net_from_n < d.net_total_n
                ? ` (from ${d.net_from_n} of ${d.net_total_n})`
                : ""}`
          : null,
        state: stateOf(macro),
        meaning:
          "The z-score of recent change across the financial-conditions factors, netted. The " +
          "percentile is the most extreme single factor, not the net.",
        ref: top
          ? {
              placed: true,
              // ×100 at the adapter's own edge, never downstream.
              pctile: (top.pctile as number) * 100,
              note: `${top.label} against its ${d?.lookback ?? "lookback"} range`,
            }
          : { placed: false, why: "no factor could be placed" },
        href: "/fed-macro",
        updatedAt: macro.updatedAt,
        Card: MacroPressureCard,
      });
    }

    /* ── CTA flows ── */
    {
      const d = cta.data;
      out.push({
        key: "cta",
        name: "CTA flows",
        halfLife: "multiday",
        value:
          d?.available
            ? `${(d.bias_1w ?? "—").replace(/_/g, " ")} 1w · exposure ${
                isNum(d.current_exposure)
                  ? `${d.current_exposure > 0 ? "+" : ""}${d.current_exposure.toFixed(0)}`
                  : "—"}`
            : null,
        state: stateOf(cta),
        meaning:
          "Modelled trend-follower positioning in ES and the pivots that would flip it. Model " +
          "points, not dollars. A multi-day signal — it sets which way to lean, not when.",
        // The board returns an exposure level and a bias label. Neither is
        // carried against its own history by the endpoint.
        ref: { placed: false, why: "no reference set — level, not percentile" },
        href: "/positioning",
        updatedAt: cta.updatedAt,
        Card: CtaFlowsCard,
      });
    }

    /* ── priced Fed path ── */
    {
      const d = fed.data;
      const next = d?.meetings?.[0];
      // NAME THE OUTCOME THE MARKET ACTUALLY PRICES HIGHEST, from whichever of
      // the three probabilities came back.
      //
      // The first cut derived direction from `delta_bp >= 0`, which quietly
      // substituted a default for an unknown twice over: a meeting with no
      // `delta_bp` fell through to "cut", and a meeting whose largest
      // probability was HOLD was still described as a hike or a cut. On the
      // current payload that reads "next 66% hike" against a 34% hold, which is
      // right — but on an 80%-hold meeting the old branch would have announced
      // "15% hike" as the headline. `p_hold` is in the payload; use it.
      const lean = [
        { word: "hike", p: next?.p_hike },
        { word: "hold", p: next?.p_hold },
        { word: "cut", p: next?.p_cut },
      ]
        .filter((o): o is { word: string; p: number } => isNum(o.p))
        .sort((a, b) => b.p - a.p)[0];
      const nMeetings = d?.meetings?.length ?? 0;
      out.push({
        key: "fed",
        name: "Priced Fed path",
        halfLife: "multiday",
        value: d?.available
          ? `${isNum(d.cumulative_bp) ? `${d.cumulative_bp > 0 ? "+" : ""}${d.cumulative_bp}bp` : "—"} priced to ${
              nMeetings} meeting${nMeetings === 1 ? "" : "s"}${
              lean ? ` · next ${Math.round(lean.p * 100)}% ${lean.word}` : ""}`
          : null,
        state: stateOf(fed),
        meaning:
          "What ZQ settlements imply for policy, reconstructed — not a licensed FedWatch feed. " +
          "Tested as a return predictor and it was a clean null across 24 windows.",
        // 2y of reconstructed history exists in the research, but this endpoint
        // does not return a percentile and assigning one by memory is exactly
        // the failure mode this project has a rule against.
        ref: { placed: false, why: "no reference set on this endpoint" },
        updatedAt: fed.updatedAt,
        Card: FedProbabilitiesCard,
      });
    }

    /* ── S&P valuation ── */
    {
      const d = val.data;
      const placeable = (d?.rows ?? []).filter((row) => isNum(row.percentile));
      const top = placeable.sort(
        (a, b) => Math.abs((b.percentile as number) - 50) - Math.abs((a.percentile as number) - 50)
      )[0];
      out.push({
        key: "val",
        name: "S&P valuation",
        halfLife: "slow",
        value: top
          ? `${top.label} ${fmt(top.value)}${top.unit === "x" ? "x" : "%"}`
          : d?.available
            ? `median premium ${fmt(d.median_premium_pct, 1)}%`
            : null,
        state: stateOf(val),
        // THE DIRECTION HAS TO BE SPOKEN. `SpValuationRow.percentile` answers
        // "how expensive" regardless of which way the metric points, so a
        // dividend yield at the 100th percentile means the market is at its most
        // expensive — the LOWEST yield on record — and a reader who takes it for
        // an ordinary level percentile reads the exact opposite of the measure.
        // Every other row on this roster ranks a level; this one ranks richness,
        // and the row says so rather than leaving the reader to assume.
        meaning:
          "Ranked by how EXPENSIVE the reading is, not by its level — a yield at a high " +
          "percentile is a low yield. A percentile says where a number sits in its own past, " +
          "not what happens next.",
        ref: top
          ? {
              placed: true,
              pctile: top.percentile as number,
              note: isNum(top.n_months)
                ? `on the expensive side of ${top.n_months.toLocaleString()} months`
                : "on the expensive side of its own history",
              qualifier: "expensive",
            }
          : { placed: false, why: "no reference set" },
        updatedAt: val.updatedAt,
        Card: SpValuationCard,
      });
    }

    // NOT SORTED. The rows are pushed in half-life order and grouped by it
    // below. An earlier version sorted every row by distance from the middle of
    // its own history, which reordered the page under a reader as data arrived
    // and — worse — ranked on an axis five of the boards do not have.
    //
    // Each dependency is a STABLE object identity: see `useBoard`, which
    // memoises its own return so this array only changes when a board did.
    // `nowMin` is deliberately NOT a dependency any more. It was one while the
    // tweet-watch row computed a post's age inside this memo; with that row
    // gone, nothing here reads the clock, and leaving it in would rebuild every
    // row once a minute for no change. The one place a clock is still needed —
    // each row's data age — reads it in `BoardRow`, per row.
    return out;
  }, [heatmap, vol, rrg, macro, cta, fed, val]);

  // Three states, counted separately, because collapsing them is the bug this
  // roster keeps guarding against: a board with a percentile, a board that
  // reports a level nothing keeps history for, and a board that did not report
  // at all are three different findings.
  const placed = rows.filter((r) => r.ref.placed).length;
  const silent = rows.filter((r) => r.value == null).length;
  // Named, not counted: "4 report a level" tells a reader there is something to
  // discount without telling them which four, so they have to check every row
  // anyway — which is the scrolling this section exists to remove.
  const unplacedNames = rows
    .filter((r) => r.value != null && !r.ref.placed)
    .map((r) => r.name);

  return (
    <section className="space-y-3">
      {BANDS.map((band) => {
        const inBand = rows.filter((r) => r.halfLife === band.id);
        if (inBand.length === 0) return null;
        return (
          <div key={band.id} className="space-y-1.5">
            {/* The hint is the whole point of the grouping, not decoration:
                "unchanged since this morning" over a board is the most useful
                thing this page can say about something it is asking you to
                spend attention on. */}
            <div className="flex items-baseline gap-2 flex-wrap">
              <h2 className="text-[0.6rem] font-bold uppercase tracking-[0.15em] text-text-muted">
                {band.label}
              </h2>
              <span className="text-[0.58rem] text-text-muted/70 truncate">{band.hint}</span>
              <span className="flex-1 border-t border-border ml-1" />
            </div>
            <div className="card card-compact divide-y divide-border">
              {inBand.map((r) => (
                <BoardRow key={r.key} row={r} nowMin={nowMin} />
              ))}
            </div>
          </div>
        );
      })}

      {/* SAID ONCE, AND IT NAMES THEM.
          Every unplaced board used to carry its own "no reference set — …" in
          the percentile column, so the column that exists to show a percentile
          mostly showed an apology, five times over. Naming the boards here is
          the same information in one place, and it leaves the column able to
          mean one thing.

          The whole sentence is one expression, punctuation included: JSX joins
          a `{expr}` and the text node on the line after it with a space, which
          printed "reference set ; 4 report" and "not reported . A percentile". */}
      <p className="text-[0.58rem] text-text-muted leading-snug">
        {[
          `${placed} of ${rows.length} boards carry a reference set and show a percentile`,
          unplacedNames.length > 0 &&
            `${unplacedNames.join(", ")} report a level with no history behind it`,
          silent > 0 && `${silent} ${silent === 1 ? "has" : "have"} not reported yet`,
        ]
          .filter(Boolean)
          .join("; ") +
          ". A percentile says where a number sits in its own past, not what happens next — " +
          "none of these has been tested as a forecast."}
      </p>
    </section>
  );
}

/* ─── one line ────────────────────────────────────────────────── */

function BoardRow({ row, nowMin }: { row: Row; nowMin: number | null }) {
  const [open, setOpen] = useStickyBoolean(`home.board.${row.key}`, false);
  const age = minutesSince(row.updatedAt, nowMin);
  const { Card } = row;

  // Only the tails are worth colouring. Everything between is the ordinary
  // state, and colouring it manufactures significance out of a normal reading.
  const tone = row.ref.placed
    ? row.ref.pctile >= 80
      ? "text-loss"
      : row.ref.pctile <= 20
        ? "text-gain"
        : "text-text-muted/70"
    : "text-text-muted/50";

  return (
    <div className="py-1.5 first:pt-0 last:pb-0">
      <div className="flex items-baseline gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex items-baseline gap-1.5 group text-left shrink-0"
        >
          <span
            className={`text-[0.55rem] text-text-muted transition-transform ${open ? "rotate-90" : ""}`}
          >
            ▸
          </span>
          <span className="text-[0.68rem] font-semibold text-text group-hover:text-accent whitespace-nowrap">
            {row.name}
          </span>
        </button>

        {/* `basis-full sm:basis-0` so the reading takes its own line on a phone
            instead of being truncated to nothing between the board name and the
            percentile — the row wraps rather than hiding the number it exists
            to show. */}
        {row.value != null ? (
          <span
            className="text-[0.68rem] text-text-muted tabular-nums basis-full sm:basis-0 sm:flex-1 min-w-0 truncate"
            title={row.value}
          >
            {row.value}
          </span>
        ) : (
          <span
            className={`text-[0.68rem] basis-full sm:basis-0 sm:flex-1 min-w-0 truncate ${
              row.state.error ? "text-loss" : "text-text-muted/70"
            }`}
          >
            {/* THREE STATES, THREE SENTENCES. A board still in flight, a board
                whose request failed, and a board that answered with nothing
                usable (`available: false`, an empty row set) are different
                things, and an em dash for all three is how this page has
                repeatedly told a reader that silence was calm. */}
            {row.state.error
              ? "this board failed to load"
              : row.state.pending
                ? "reading…"
                : "reported nothing usable this cycle"}
          </span>
        )}

        {/* SUPPRESSED ENTIRELY WHEN THE BOARD HAS NOT REPORTED. "No factor
            could be placed" beside "reading…" states a finding about data that
            has not arrived — it reads as a settled negative result when it is
            only a pending request. A reference claim needs something to
            reference. */}
        {row.value != null && row.ref.placed && (
          <span className={`text-[0.65rem] font-bold tabular-nums shrink-0 ${tone}`}>
            <span title={`Percentile ${row.ref.note}.`}>
              {ordinal(Math.round(row.ref.pctile))}
              {row.ref.qualifier && (
                <span className="ml-1 font-normal text-[0.55rem] text-text-muted">
                  {row.ref.qualifier}
                </span>
              )}
            </span>
          </span>
        )}

        {age != null && (
          <span
            className={`text-[0.55rem] tabular-nums shrink-0 ${age > 90 ? "text-amber-400" : "text-text-muted/70"}`}
            title="How long ago this board last returned data."
          >
            {age < 1 ? "now" : age < 60 ? `${age}m` : `${Math.floor(age / 60)}h`}
          </span>
        )}

        {row.href && (
          <Link
            href={row.href}
            className="text-[0.55rem] text-text-muted hover:text-accent shrink-0"
          >
            page →
          </Link>
        )}
      </div>

      {open && (
        <div className="mt-2 space-y-2">
          <p className="text-[0.58rem] text-text-muted leading-snug">
            {row.meaning}
            {/* The per-board reason left the row's percentile column, but it did
                not leave the component — it lands here, where a reader who
                opened the board is actually asking about it. */}
            {!row.ref.placed && row.value != null && ` Relative to what: ${row.ref.why}.`}
          </p>
          <Card />
        </div>
      )}
    </div>
  );
}
