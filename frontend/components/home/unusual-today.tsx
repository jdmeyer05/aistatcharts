"use client";

/**
 * What on this page is actually at an extreme today.
 *
 * THE PROBLEM IT SOLVES. Every card here already answers "relative to what" —
 * the vol landscape carries a percentile per summary measure, the RRG carries
 * one per regime measure, the macro board carries one per factor, the valuation
 * strip carries one for the risk premium and one for the rate beta. Nothing
 * ever compared them. So a reader had to scroll twelve blocks to discover that
 * eleven of them were sitting in the middle of their own history, which is the
 * most common state and the one the page was worst at communicating.
 *
 * IT KEEPS ITS OWN INPUTS FRESH, and that is deliberate rather than lazy.
 *
 * The obvious design — `enabled: false`, read whatever the cards have loaded,
 * issue nothing — is what the interpretation panel does, and it was wrong HERE
 * the moment the horizon bands became collapsible. Collapsing a band unmounts
 * its cards, which stops their refetch intervals; a cache-only observer keeps
 * the entry alive (so it is not garbage-collected) but never refreshes it. The
 * ribbon would then go on rendering a percentile from a board that stopped
 * being maintained an hour ago, with nothing to say so — an absence rendered as
 * a calm, in the one component whose entire job is flagging what is unusual.
 *
 * So each query below carries the SAME key and the SAME cadence as the card it
 * shadows. React Query dedupes on the key, so while a card is mounted the two
 * observers share one request and nothing changes; while it is collapsed, this
 * keeps the reading honest. The staleness bound is stated on the card.
 *
 * THE EMPTY STATE IS THE POINT, and it is stated in numbers rather than by
 * disappearing. "Nothing is at an extreme" and "we could not place anything"
 * are completely different findings that a hidden component renders
 * identically — and this project has shipped that bug in five other places.
 *
 * SCALE TRAP, worth naming because it is invisible and silent: the macro board
 * reports `pctile` on 0..1 while every other source on this page reports 0..100.
 * Mixing them puts every macro factor at "the 0th percentile" — an extreme
 * reading, manufactured out of a unit mismatch, on the exact axis this
 * component ranks by. Each source is normalised at its own adapter below.
 */

import { useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  fetchVolLandscape,
  fetchSectorRrg,
  fetchMacroPressure,
  fetchSpValuation,
  type VolLandscapeScan,
  type SectorRrg,
  type MacroPressureBoard,
  type SpValuation,
} from "@/lib/api";
import { ordinal } from "@/lib/home-constants";
import { minutesSince, useMinuteClock } from "@/components/home/primitives";

/** How far into a tail a reading has to sit before it is worth a chip.
 *  Symmetric, and wide enough that an ordinary day produces an empty ribbon —
 *  which is the honest output for an ordinary day. */
const HIGH = 80;
const LOW = 20;

type Reading = {
  key: string;
  label: string;
  value: string;
  /** Always 0..100 by the time it reaches here. */
  pctile: number;
  nHistory: number | null;
  source: string;
  href?: string;
  /** What being at this end of the range actually means, in plain words. */
  meaning: string;
};

/** Same key and cadence as the card being shadowed, so React Query dedupes to
 *  one request while the card is mounted and this keeps the entry current while
 *  it is collapsed. Returns the data and when it was last fetched — the age is
 *  rendered, because a percentile from a board that stopped updating looks
 *  exactly like one that did not move. */
function useShadowed<T>(
  queryKey: unknown[],
  queryFn: () => Promise<T>,
  refetchInterval: number,
) {
  const q = useQuery<T>({
    queryKey,
    queryFn,
    refetchInterval,
    staleTime: refetchInterval * 0.8,
  });
  return { data: q.data, updatedAt: q.dataUpdatedAt };
}

function fmt(n: number | null | undefined, digits = 2): string {
  return n == null || Number.isNaN(n) ? "—" : n.toFixed(digits);
}

export default function UnusualToday() {
  // Cadences copied from the cards these shadow, so the dedupe is exact.
  const volQ = useShadowed<VolLandscapeScan>(
    ["vol-landscape-home"], fetchVolLandscape, 5 * 60_000);
  const rrgQ = useShadowed<SectorRrg>(
    ["sector-rrg", 8], () => fetchSectorRrg(8), 30 * 60_000);
  const macroQ = useShadowed<MacroPressureBoard>(
    ["macro-pressure"], fetchMacroPressure, 30 * 60_000);
  const valuationQ = useShadowed<SpValuation>(
    ["sp-valuation"], fetchSpValuation, 60 * 60_000);

  const vol = volQ.data;
  const rrg = rrgQ.data;
  const macro = macroQ.data;
  const valuation = valuationQ.data;

  // The oldest input behind any chip shown. A ribbon is a summary, and a
  // summary of four boards is only as current as its stalest member.
  //
  // Driven off the shared minute clock rather than `Date.now()`: inside a memo
  // the latter never recomputes as time passes, so a board that stopped
  // refreshing would keep reporting the age it had when its data arrived.
  const nowMin = useMinuteClock();
  const oldest = useMemo(() => {
    const ages = [volQ.updatedAt, rrgQ.updatedAt, macroQ.updatedAt, valuationQ.updatedAt]
      .map((t) => minutesSince(t, nowMin))
      .filter((a): a is number => a != null);
    return ages.length === 0 ? null : Math.max(...ages);
  }, [volQ.updatedAt, rrgQ.updatedAt, macroQ.updatedAt, valuationQ.updatedAt, nowMin]);

  const { extremes, placed, unplaced } = useMemo(() => {
    const all: Reading[] = [];
    let cannotPlace = 0;

    // ── vol landscape: percentiles are 0..100, null until 60 sessions ──
    const volLabels: Record<string, { label: string; value: string; high: string; low: string }> = {
      avg_iv: {
        label: "Average IV",
        value: `${fmt(vol?.summary?.avg_iv, 1)}`,
        high: "options across the scanned universe are pricing more movement than they usually do",
        low: "options are pricing less movement than they usually do",
      },
      avg_ivhv: {
        label: "IV / HV",
        value: `${fmt(vol?.summary?.avg_ivhv, 2)}`,
        high: "implied is running far above what has actually been delivered",
        low: "implied has fallen toward or below delivered movement",
      },
      n_inverted: {
        label: "Inverted terms",
        value: `${vol?.summary?.n_inverted ?? "—"} of ${vol?.summary?.n_tickers ?? "—"}`,
        high: "an unusual number of names are pricing near-term event risk above back months",
        low: "unusually few names carry an inverted term structure",
      },
      n_steep_skew: {
        label: "Steep put skew",
        value: `${vol?.summary?.n_steep_skew ?? "—"} of ${vol?.summary?.n_skew_rated ?? vol?.summary?.n_tickers ?? "—"}`,
        high: "downside protection is bid across an unusual share of the universe",
        low: "unusually few names carry steep put skew",
      },
    };
    for (const [k, h] of Object.entries(vol?.history ?? {})) {
      const meta = volLabels[k];
      if (!meta) continue;
      if (h.pctile == null) { cannotPlace += 1; continue; }
      all.push({
        key: `vol.${k}`, label: meta.label, value: meta.value,
        pctile: h.pctile, nHistory: h.n_history, source: "Vol landscape",
        href: "/vol-landscape",
        meaning: h.pctile >= 50 ? meta.high : meta.low,
      });
    }

    // ── sector RRG regime measures: 0..100, null below the history floor ──
    const rrgLabels: Record<string, { label: string; high: string; low: string }> = {
      tilt: {
        label: "Sector tilt",
        high: "sector leadership is unusually concentrated on the cyclical side",
        low: "sector leadership is unusually concentrated on the defensive side",
      },
      dispersion: {
        label: "Sector dispersion",
        high: "sectors are moving unusually far apart — an index move is not the whole story",
        low: "sectors are moving unusually together — little to pick between them",
      },
      correlation: {
        label: "Sector correlation",
        high: "sectors are moving together to an unusual degree; single-name selection is paying less",
        low: "pairwise sector correlation is unusually low",
      },
    };
    // Named explicitly rather than iterated: `regime` also carries a `current`
    // block that is a plain {realized_vol, avg_sector_corr, trend_vs_50dma}
    // object with no `pctile` at all, and walking Object.entries over the union
    // would type-error on it — or, worse, compile and read `undefined` as a
    // missing percentile.
    const rrgMeasures = [
      ["tilt", rrg?.regime?.tilt] as const,
      ["dispersion", rrg?.regime?.dispersion] as const,
      ["correlation", rrg?.regime?.correlation] as const,
    ];
    for (const [k, m] of rrgMeasures) {
      const meta = rrgLabels[k];
      if (!meta || !m) continue;
      if (m.pctile == null) { cannotPlace += 1; continue; }
      all.push({
        key: `rrg.${k}`, label: meta.label, value: `${fmt(m.value, 2)}${m.band ? ` · ${m.band}` : ""}`,
        pctile: m.pctile, nHistory: m.n_history, source: "Sector rotation",
        href: "/sector-analysis",
        meaning: m.pctile >= 50 ? meta.high : meta.low,
      });
    }

    // ── macro pressure: 0..1, so ×100. See the module docstring. ──
    for (const row of macro?.rows ?? []) {
      if (row.pctile == null || Number.isNaN(row.pctile)) { cannotPlace += 1; continue; }
      // Stale factors are excluded upstream from the net score for the same
      // reason they are excluded here: a change window comparing one print to
      // itself is missing data, not a neutral reading.
      if (row.stale_days != null && row.stale_days > 30) { cannotPlace += 1; continue; }
      all.push({
        key: `macro.${row.key}`, label: row.label,
        value: `${fmt(row.display_level, 2)}${row.display_unit ?? ""}`,
        pctile: row.pctile * 100, nHistory: null, source: "Macro pressure",
        meaning: row.pctile >= 0.5
          ? `sits near the top of its ${macro?.lookback ?? "lookback"} range`
          : `sits near the bottom of its ${macro?.lookback ?? "lookback"} range`,
      });
    }

    // ── valuation: the two readings on the strip that carry a percentile ──
    const rc = valuation?.rate_context;
    if (rc?.erp_pctile != null) {
      all.push({
        key: "val.erp", label: "Earnings yield vs 10y",
        value: `${rc.erp_pct != null && rc.erp_pct > 0 ? "+" : ""}${fmt(rc.erp_pct, 2)}pp`,
        pctile: rc.erp_pctile, nHistory: rc.erp_n_months ?? null,
        source: "Valuation",
        meaning: rc.erp_pctile >= 50
          ? "the index is yielding unusually well against the 10-year"
          : "the index is yielding unusually poorly against the 10-year",
      });
    }
    if (rc?.beta_pctile != null) {
      all.push({
        key: "val.beta", label: "SPX per 10bp on the 10y",
        value: `${rc.move_per_10bp_pct != null && rc.move_per_10bp_pct > 0 ? "+" : ""}${fmt(rc.move_per_10bp_pct, 2)}%`,
        pctile: rc.beta_pctile, nHistory: null, source: "Valuation",
        meaning: rc.beta_pctile >= 50
          ? "the index is unusually sensitive to the long end right now"
          : "the index is unusually insensitive to the long end right now",
      });
    }

    const ext = all
      .filter((r) => r.pctile >= HIGH || r.pctile <= LOW)
      .sort((a, b) => Math.abs(b.pctile - 50) - Math.abs(a.pctile - 50));

    return { extremes: ext, placed: all.length, unplaced: cannotPlace };
  }, [vol, rrg, macro, valuation]);

  // Before any card has hydrated there is genuinely nothing to say, and a
  // "nothing is unusual" banner over an empty page would be a lie by timing.
  if (placed === 0 && unplaced === 0) return null;

  const shown = extremes.slice(0, 6);

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">
          At an extreme today
        </h3>
        <span className="text-[0.55rem] text-text-muted">
          readings past the {ordinal(HIGH)} or under the {ordinal(LOW)} percentile of their own history
        </span>
        {oldest != null && (
          <span
            className={`ml-auto text-[0.55rem] tabular-nums ${oldest > 90 ? "text-amber-400" : "text-text-muted"}`}
            title="Age of the OLDEST board feeding this ribbon. A summary of four boards is only as current as its stalest member."
          >
            {/* "oldest input just now old" is what a template reads like when
                the zero case was never looked at. The whole phrase varies, not
                just the number. */}
            {oldest < 1
              ? "all inputs current"
              : `oldest input ${oldest < 60 ? `${oldest}m` : `${Math.floor(oldest / 60)}h`} old`}
          </span>
        )}
      </div>

      {placed === 0 ? (
        <p className="text-[0.68rem] text-text-muted leading-snug">
          Nothing on this page can be placed against its own history yet —{" "}
          {unplaced} reading{unplaced === 1 ? "" : "s"} exist but none has enough recorded
          sessions behind it. Until then the numbers below are levels, not readings.
        </p>
      ) : shown.length === 0 ? (
        <p className="text-[0.68rem] text-text-muted leading-snug">
          Nothing is at an extreme. All {placed} placed reading{placed === 1 ? "" : "s"} on this
          page sit between the {ordinal(LOW)} and {ordinal(HIGH)} percentile of their own
          history{unplaced > 0 ? `, and ${unplaced} more could not be placed` : ""}. An
          ordinary session by every measure this page keeps a reference set for.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5">
            {shown.map((r) => {
              const high = r.pctile >= HIGH;
              const chip = (
                <span
                  className={`inline-flex items-baseline gap-1.5 px-2 py-1 rounded border text-[0.65rem] ${
                    high ? "border-loss/40 bg-loss/10" : "border-gain/40 bg-gain/10"
                  }`}
                  title={`${r.source} — ${r.meaning}.${r.nHistory ? ` Placed against ${r.nHistory} recorded periods.` : ""}`}
                >
                  <span className="text-text-muted">{r.label}</span>
                  <span className="font-semibold tabular-nums text-text">{r.value}</span>
                  <span className={`tabular-nums font-bold ${high ? "text-loss" : "text-gain"}`}>
                    {ordinal(Math.round(r.pctile))}
                  </span>
                </span>
              );
              return r.href ? (
                <Link key={r.key} href={r.href} className="hover:opacity-80">{chip}</Link>
              ) : (
                <span key={r.key}>{chip}</span>
              );
            })}
          </div>
          <p className="text-[0.58rem] text-text-muted leading-snug">
            {shown.length} of {placed} placed reading{placed === 1 ? "" : "s"} sit in a tail
            {extremes.length > shown.length ? `, ${extremes.length - shown.length} more not shown` : ""}
            {unplaced > 0 ? `; ${unplaced} could not be placed against a reference set` : ""}.
            {" "}A percentile says where a number sits in its own past, not what happens next —
            none of these has been tested as a forecast.
          </p>
        </>
      )}
    </div>
  );
}
