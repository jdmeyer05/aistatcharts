"use client";

/**
 * Home — real-time market dashboard (client islands).
 *
 * GROUPED BY HOW FAST THINGS MOVE, not by topic. Counting honestly, one card
 * here changes intraday (the ES briefing), three change daily, five change over
 * weeks to months, and the valuation strip describes something that moves on a
 * quarterly earnings cycle — while sitting on a 60-minute refetch. Every card
 * wore identical live styling, so the page read as uniformly current when it
 * was current in one place. The bands say which is which, and they collapse
 * (remembered per reader) so the slow half can be folded away — never folded by
 * default, because hiding built work behind a chevron is not an improvement.
 *
 * SPLIT INTO TWO ISLANDS. `HomeFast` is the pulse strip and the ES briefing —
 * the two things a session actually turns on. `HomeSwing` is everything else,
 * and `app/page.tsx` streams it behind a Suspense boundary so one slow upstream
 * cannot hold up the price at the top of the page.
 *
 *   HomeFast
 *     Market Pulse Strip                          (30s)
 *     ES Session Briefing                         (3 min — levels develop)
 *   HomeSwing
 *     At an extreme today                         (derived from cached cards)
 *     One interpretation for the whole page
 *     TODAY:            driver · sector relative | vol landscape · news | tweets
 *     WEEKS TO MONTHS:  rotation | CTA · macro pressure | Fed · valuation
 *     THE BOOK:         12-month trend book
 *     SCHEDULED AHEAD:  macro calendar
 */

import Link from "next/link";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchSnapshot,
  fetchMarketDriver,
  fetchHeatmap,
  fetchEvents,
  fetchVolLandscape,
  fetchTrumpMonitor,
  type MarketDriverResponse,
  type TrumpPost,
  type CalendarEvent,
} from "@/lib/api";
import { PULSE_TICKERS, PULSE_LABELS, ordinal } from "@/lib/home-constants";
import EsBriefing from "@/components/home/es-briefing";
import PageInterpretation from "@/components/home/page-interpretation";
import CtaFlows from "@/components/home/cta-flows";
import MacroPressure from "@/components/home/macro-pressure";
import SectorRrgCard from "@/components/home/sector-rrg";
import SpValuationStrip from "@/components/home/sp-valuation";
import FedProbabilitiesCard from "@/components/home/fed-probabilities";
import TsmomBookCard from "@/components/home/tsmom-book";
import UnusualToday from "@/components/home/unusual-today";
import { CardHeader, HorizonBand, Takeaway, fmtAgo, useMinuteClock } from "@/components/home/primitives";

// `fmtAgo` moved to primitives and now takes the minute clock. It used to call
// `Date.now()` during render, which on an ISR-cached page is wrong by
// construction — see the function's own comment.

function pctClass(n: number | undefined | null): string {
  if (n == null || n === 0) return "text-text-muted";
  return n > 0 ? "text-gain" : "text-loss";
}

/** One definition of the driver query, used by the card and by the news panel.
 *
 *  It was declared twice with identical options in two places. React Query
 *  deduped the request so nothing broke, but the two cadences could drift apart
 *  in a later edit and the symptom — one copy refetching on a schedule the
 *  other did not expect — would be close to invisible. */
function useMarketDriver() {
  return useQuery({
    queryKey: ["market-driver"],
    queryFn: fetchMarketDriver,
    refetchInterval: 5 * 60_000,
    staleTime: 3 * 60_000,
  });
}

/* ─── Market Pulse Strip ──────────────────────────────────────── */

function MarketPulse() {
  // Null on the server, a real minute in the browser. Used only to gate the
  // locale-formatted timestamp below — see the comment there.
  const nowMin = useMinuteClock();
  const q = useQuery({
    queryKey: ["pulse", PULSE_TICKERS.join(",")],
    queryFn: () => fetchSnapshot([...PULSE_TICKERS]),
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
  // `change` is genuinely optional: when no prior close can be established the
  // backend omits it rather than publishing a confident 0.00%. The render below
  // already treats null as "no percentage to show".
  //
  // Memoised because the `?? {}` fallback minted a fresh object every render,
  // and the summary below depends on it — unmemoised, that read recomputed on
  // every render including the once-per-second ones.
  const data: Record<string, { price: number; change?: number; prev_close?: number }> =
    useMemo(() => q.data ?? {}, [q.data]);

  // What the strip amounts to, instead of eight numbers to read across. Counted
  // over the tickers that actually reported a change — a missing percentage is
  // not a flat one, and folding the two together is how "0.00%" gets published
  // for an instrument nobody could price.
  const read = useMemo(() => {
    const pairs: Array<{ tk: string; change: number }> = [];
    for (const tk of PULSE_TICKERS) {
      const c = data[tk]?.change;
      if (typeof c === "number") pairs.push({ tk, change: c });
    }
    if (pairs.length === 0) return null;
    const up = pairs.filter((p) => p.change > 0).length;
    const down = pairs.filter((p) => p.change < 0).length;
    const widest = [...pairs].sort((a, b) => Math.abs(b.change) - Math.abs(a.change))[0];
    return { n: pairs.length, up, down, biggest: widest.change, biggestTk: widest.tk };
  }, [data]);

  return (
    <div className="card card-compact space-y-1.5">
      <div className="flex flex-wrap gap-x-5 gap-y-2 items-center">
        {PULSE_TICKERS.map((tk) => {
          const s = data[tk] || { price: 0, change: 0 };
          const label = PULSE_LABELS[tk] ?? tk;
          return (
            <div key={tk} className="flex items-baseline gap-1.5 min-w-0">
              <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">{label}</span>
              <span className="text-sm font-semibold tabular-nums">
                {s.price ? s.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}
              </span>
              <span className={`text-xs tabular-nums ${pctClass(s.change)}`}>
                {s.change == null ? "" : `${s.change > 0 ? "+" : ""}${s.change.toFixed(2)}%`}
              </span>
            </div>
          );
        })}
        {/* THE ONLY HYDRATION MISMATCH ON THIS PAGE, and it printed a wrong
            number rather than just warning.

            `toLocaleTimeString` formats in whatever timezone the renderer sits
            in. Cloud Run runs UTC and the reader does not, so the server-
            rendered HTML said "as of 12:16:21" where the browser then said
            "as of 07:16:50" — the same instant, five hours apart. React logged
            error #418 and re-rendered, but for the frames before hydration the
            strip showed a quote age that was five hours wrong, on the one card
            whose entire job is saying how fresh the price is.

            Gating on the clock fixes it at the root: `useMinuteClock` returns
            null on the server, so the server renders nothing here and the
            browser fills in its own local time after hydration. Nothing is ever
            formatted in a timezone the reader is not in. */}
        <div className="ml-auto text-[0.6rem] text-text-muted">
          {q.isFetching
            ? "updating…"
            : nowMin != null && q.dataUpdatedAt
              ? `as of ${new Date(q.dataUpdatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
              : ""}
        </div>
      </div>
      {read && (
        <div className="text-[0.58rem] text-text-muted leading-snug border-t border-border pt-1.5">
          {read.up} up / {read.down} down of {read.n} reporting
          {" · widest is "}
          <span className="text-text">{PULSE_LABELS[read.biggestTk] ?? read.biggestTk}</span>
          {" at "}
          <span className={`tabular-nums ${pctClass(read.biggest)}`}>
            {read.biggest > 0 ? "+" : ""}{read.biggest.toFixed(2)}%
          </span>
          {read.n < PULSE_TICKERS.length && (
            <span className="text-text-muted/70">
              {" "}· {PULSE_TICKERS.length - read.n} could not be priced against a prior close and
              are excluded rather than counted flat
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── What's Driving Markets ──────────────────────────────────── */

function DriverPill({ label, source }: { label: string; source: string }) {
  const sourceColors: Record<string, string> = {
    news: "bg-accent/15 text-accent",
    quotes: "bg-gain/15 text-gain",
    vol: "bg-spot/15 text-spot",
    cftc: "bg-loss/15 text-loss",
    polymarket: "bg-violet-500/15 text-violet-400",
    release: "bg-amber-500/15 text-amber-400",
  };
  const cls = sourceColors[source] ?? "bg-surface-alt text-text-muted";
  return (
    <span className={`text-[0.6rem] font-semibold px-2 py-0.5 rounded ${cls}`} title={source}>
      {label}
    </span>
  );
}

function MarketDriverCard() {
  const q = useMarketDriver();
  const d = q.data;
  // Null on the server, so no relative age is baked into the cached HTML.
  const nowMin = useMinuteClock();
  const asOf = fmtAgo(d?.as_of_utc, nowMin);

  // MEASURED, not narrated. `drivers` is the cross-asset attribution — a
  // regression, not another model's prose — and it rode along in the payload
  // for the interpretation panel without ever being rendered on the card whose
  // request carries it. It is the only part of this card that can be checked.
  const attribution = d?.drivers;
  const topDriver = attribution?.available ? attribution.ranking?.[0] : null;

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-bold uppercase tracking-wider text-accent">What&apos;s Driving Markets</h2>
            {d?.regime_label && (
              <span className="text-[0.65rem] font-bold uppercase px-2 py-0.5 rounded bg-accent/15 text-accent tracking-wider">
                {d.regime_label}
              </span>
            )}
          </div>
          <div className="text-[0.6rem] text-text-muted mt-0.5">
            {d?.model ? `${d.model}${d.escalated ? " (escalated)" : ""}` : ""}
            {asOf ? `  ·  ${asOf}` : ""}
            {d?.cache_hit ? "  ·  cached" : ""}
            {/* The model's self-reported confidence used to render here as
                "conf 7/10". Removed 2026-08-29: a number with no measured
                meaning. Over 11 resolved calls this surface is statistically
                indistinguishable from a model with zero information that simply
                speaks confidently — its Brier skill sits on the median of that
                null. And the advice-taking evidence is blunt about what such a
                number does: when an advisor's track record is unavailable,
                stated confidence drives how much you believe it while actual
                accuracy has no measurable effect, and willingness to check the
                record FALLS as stated confidence rises. It is not neutral
                decoration — it buys credibility it has not earned and suppresses
                the audit that would expose that. The falsifiable `calls` behind
                it are still generated and still settled against price; they were
                already lifted out server-side and never rendered. The
                measurement continues, the anchor goes. */}
          </div>
        </div>
        <button
          onClick={() => q.refetch()}
          disabled={q.isFetching}
          className="text-[0.65rem] px-2 py-1 rounded border border-border hover:bg-surface-alt disabled:opacity-50"
          title="Recompute the driver synthesis"
        >
          {q.isFetching ? "…" : "Refresh"}
        </button>
      </div>

      {q.isLoading && (
        <div className="py-6 text-center">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-xs text-text-muted mt-2">Reading the tape…</p>
        </div>
      )}

      {q.isError && !d && (
        <p className="text-xs text-loss">
          Driver synthesis unavailable: {(q.error as Error)?.message ?? "unknown error"}.
        </p>
      )}

      {/* The endpoint answers 200 with empty paragraphs when the model's output
          couldn't be used, so an empty body is a failure the card has to name —
          otherwise it renders as a blank panel that looks like it is still
          loading. Not cached server-side, so Refresh genuinely retries. */}
      {d && !d.paragraphs?.what_happened && (
        <div className="py-4 flex items-baseline gap-2 flex-wrap">
          <p className="text-xs text-text-muted">
            The model&apos;s output couldn&apos;t be read this cycle
            {d.error ? ` (${d.error})` : ""}.
          </p>
          <button
            type="button"
            onClick={() => q.refetch()}
            disabled={q.isFetching}
            className="text-[0.65rem] text-accent hover:underline disabled:opacity-50"
          >
            {q.isFetching ? "Retrying…" : "Retry"}
          </button>
        </div>
      )}

      {d && (
        <>
          <div className="space-y-2.5 text-sm leading-relaxed text-text">
            {d.paragraphs?.what_happened && <p>{d.paragraphs.what_happened}</p>}
            {d.paragraphs?.whats_driving && <p>{d.paragraphs.whats_driving}</p>}
            {d.paragraphs?.what_to_watch && <p>{d.paragraphs.what_to_watch}</p>}
          </div>
          {d.citations && d.citations.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1 border-t border-border">
              {d.citations.map((c, i) => (
                <DriverPill key={i} label={c.label} source={c.source} />
              ))}
            </div>
          )}

          {/* The prose above is a model's reading. This is a regression, and the
              only part of the card that can be wrong in a checkable way — so it
              says itself rather than sitting in the payload for another panel. */}
          {topDriver && attribution && (
            <Takeaway
              label="Measured, not narrated"
              headline={
                `Over the last ${attribution.window_sessions} sessions, ${topDriver.driver} ` +
                `(${topDriver.ticker}) is the most closely linked cross-asset driver at ` +
                `${(topDriver.share_of_variance * 100).toFixed(0)}% of explained variance — ` +
                /* THE SIGN HAS TO BE SPOKEN, not just printed. The ranking is by
                   share of variance, which is unsigned, so the top driver is
                   routinely an INVERSE one. Saying "has moved with the S&P" over
                   a correlation of −0.52 states the opposite of what was
                   measured, in the one block on this card that is a regression
                   rather than prose. */
                `moving ${topDriver.corr_with_spy < 0 ? "INVERSELY to" : "with"} it, correlation ` +
                `${topDriver.corr_with_spy > 0 ? "+" : ""}${topDriver.corr_with_spy.toFixed(2)}.`
              }
              detail={
                `The macro drivers together explain ${(attribution.explained_share * 100).toFixed(0)}% of the index's ` +
                `daily variance` +
                (attribution.explained_share_a_year_ago != null
                  ? `, against ${(attribution.explained_share_a_year_ago * 100).toFixed(0)}% a year ago`
                  : "") +
                `. ` +
                (topDriver.rank_a_year_ago != null
                  ? `${topDriver.driver} ranked ${ordinal(topDriver.rank_a_year_ago)} a year ago — the ranking rotates visibly between windows, so read this as a description of the current one rather than a standing fact. `
                  : "The ranking rotates visibly between windows, so read this as a description of the current one rather than a standing fact. ") +
                `"Has moved with", never "drives": every next-day correlation in this set measured inside noise.`
              }
            />
          )}
        </>
      )}
    </div>
  );
}

/* ─── Sector Relative ─────────────────────────────────────────── */

function SectorRelative() {
  const q = useQuery({
    queryKey: ["heatmap", "sectors"],
    queryFn: () => fetchHeatmap("sectors"),
    refetchInterval: 60_000,
    staleTime: 45_000,
  });
  // Fall back inside useMemo so an undefined `q.data` doesn't churn the
  // `[]` reference every render and re-trigger the sort.
  const sorted = useMemo(
    () => [...(q.data?.items ?? [])].sort((a, b) => b.change - a.change),
    [q.data?.items]
  );
  const maxAbs = Math.max(0.5, ...sorted.map((s) => Math.abs(s.change || 0)));

  // The spread between best and worst is what says whether today was a sector
  // day or an index day, and nothing computed it.
  const read = useMemo(() => {
    if (sorted.length < 2) return null;
    const top = sorted[0];
    const bottom = sorted[sorted.length - 1];
    const spread = (top.change ?? 0) - (bottom.change ?? 0);
    const up = sorted.filter((s) => (s.change ?? 0) > 0).length;
    return { top, bottom, spread, up, n: sorted.length };
  }, [sorted]);

  return (
    <div className="card card-compact space-y-2">
      <CardHeader
        title="Sector Relative"
        href="/sector-analysis"
        asOf={q.dataUpdatedAt || null}
        staleAfterMin={15}
      />
      {q.isLoading && <div className="text-xs text-text-muted">Loading…</div>}
      {/* Without this the card rendered a header over an empty box whenever the
          rows were missing, which reads as "still loading" forever rather than
          as a fault. Say which it is. */}
      {!q.isLoading && sorted.length === 0 && (
        <div className="py-2 flex items-baseline gap-2 flex-wrap">
          <p className="text-xs text-text-muted">
            {q.isError ? "Couldn't load sector performance." : "No sector data returned."}
          </p>
          {q.isError && (
            <button
              type="button"
              onClick={() => q.refetch()}
              disabled={q.isFetching}
              className="text-[0.65rem] text-accent hover:underline disabled:opacity-50"
            >
              {q.isFetching ? "Retrying…" : "Retry"}
            </button>
          )}
        </div>
      )}
      <div className="space-y-1">
        {sorted.map((s) => {
          const pct = s.change || 0;
          const width = Math.abs(pct) / maxAbs * 50;
          const isUp = pct >= 0;
          return (
            <div key={s.symbol} className="flex items-center gap-2 text-xs tabular-nums">
              <div className="w-16 truncate text-text-muted" title={s.label}>{s.label}</div>
              <div className="flex-1 flex h-4 items-center relative">
                <div className="absolute left-1/2 top-0 bottom-0 w-px bg-border" />
                {isUp ? (
                  <div
                    className="absolute left-1/2 top-0.5 bottom-0.5 bg-gain/70 rounded-r"
                    style={{ width: `${width}%` }}
                  />
                ) : (
                  <div
                    className="absolute right-1/2 top-0.5 bottom-0.5 bg-loss/70 rounded-l"
                    style={{ width: `${width}%` }}
                  />
                )}
              </div>
              <div className={`w-14 text-right ${pctClass(pct)}`}>
                {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
              </div>
            </div>
          );
        })}
      </div>
      {read && (
        <Takeaway
          headline={
            `${read.up} of ${read.n} sectors green, and the spread from ${read.top.label} to ` +
            `${read.bottom.label} is ${read.spread.toFixed(2)} points.`
          }
          detail={
            `A wide spread means the index move is not the whole story and there is something to ` +
            `pick between sectors; a narrow one means everything moved together. This is today's ` +
            `dispersion in isolation — where it sits against its own history is on the rotation ` +
            `board, which is the card that keeps a reference set for it.`
          }
        />
      )}
    </div>
  );
}

/* ─── Vol Landscape Snapshot ──────────────────────────────────── */

/** "Relative to what" for a single measure.
 *
 *  Renders the percentile against the measure's own recorded history — never a
 *  placeholder and never a middle value, because a stand-in reads as a real
 *  reading.
 *
 *  `gap` handles the MIXED case. `percentiles()` counts history per measure, so
 *  a measure added to TRACKED later carries fewer rows than its neighbours; once
 *  the older ones clear the 60-row floor and it has not, this row would show
 *  percentiles on three stats and a silently bare number on the fourth — which
 *  is the exact ambiguity this whole change exists to remove. When some measures
 *  can be placed and this one cannot, say so in place. When NONE can, the row
 *  note says it once instead (see `refNote`) rather than repeating a dash. */
function Ref({ h, gap }: { h?: { pctile: number | null; n_history: number }; gap?: boolean }) {
  if (!h || h.pctile == null) {
    if (!gap || !h) return null;
    return (
      <span
        className="ml-1 text-text-muted/50"
        title={`No reference for this measure yet — ${h.n_history} recorded sessions, and 60 are needed. The other stats in this row have enough history; this one does not.`}
      >
        —
      </span>
    );
  }
  const p = Math.round(h.pctile);
  // Only the tails are worth colouring. Everything between is the normal state
  // and colouring it would manufacture significance out of an ordinary reading.
  const tone = p >= 80 ? "text-loss" : p <= 20 ? "text-gain" : "text-text-muted/70";
  // `ordinal` rather than an inline suffix: the inline version is where "1th
  // pctile" came from, and it was already fixed once at three other sites.
  return (
    <span className={`ml-1 ${tone}`} title={`Percentile against its own last ${h.n_history} recorded sessions.`}>
      {ordinal(p)}
    </span>
  );
}

function VolLandscapeSnapshot() {
  const q = useQuery({
    queryKey: ["vol-landscape-home"],
    queryFn: fetchVolLandscape,
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  });
  const d = q.data;

  // This card read `top_dislocations` / `rows` / `items`, and the endpoint
  // returns none of them — it returns `metrics`, `divergences`, `summary`,
  // `regime` and `regime_action`. Every one of those lookups resolved to
  // undefined, so the fallback chain always produced an empty array and the
  // card permanently displayed "No dislocations surfaced right now." It had
  // never shown data. `divergences` is the field that actually carries the
  // dislocations the card was written to show.
  const divergences = useMemo(() => (d?.divergences ?? []).slice(0, 5), [d]);
  const s = d?.summary;

  // One honest sentence when the reference set is too thin, instead of a
  // per-stat "n/a". `n_history` is the same for every measure (they are
  // recorded as one row per session), so take it from whichever is present.
  const refNote = useMemo(() => {
    const hist = d?.history;
    if (!hist) return null;
    const entries = Object.values(hist);
    if (entries.length === 0) return null;
    if (entries.some((e) => e.pctile != null)) return null;
    const n = Math.max(...entries.map((e) => e.n_history));
    return `No historical reference yet — ${n} session${n === 1 ? "" : "s"} recorded, and the percentiles above need 60. Until then these are levels, not readings: nothing here says whether they are high or low.`;
  }, [d]);

  // True only in the mixed state: at least one measure placed, at least one not.
  // Drives the in-place dash so no number is ever silently uncontextualised.
  const refPartial = useMemo(() => {
    const entries = Object.values(d?.history ?? {});
    return entries.some((e) => e.pctile != null) && entries.some((e) => e.pctile == null);
  }, [d]);

  // Cuts that cannot discriminate, named rather than left in the payload.
  const nearMedianCuts = useMemo(() => {
    const t = d?.thresholds;
    if (!t) return [];
    return Object.entries(t)
      // `pctile_in_universe != null` is not redundant with `near_median`.
      // threshold_report omits `near_median` entirely when it cannot compute a
      // percentile, so the pair is unreachable today — but the type permits it,
      // and the previous `?? 0` would have printed "0th percentile", inventing
      // the exact statistic this sentence exists to report. Filter, never
      // default: a fabricated number is worse than a missing line.
      .filter(([, v]) => v.near_median && v.pctile_in_universe != null)
      .map(([k, v]) => {
        const where = `the ${k.replace(/_/g, " ")} cut of ${v.cut} sits at the ${ordinal(v.pctile_in_universe as number)} percentile`;
        return v.n ? `${where} of today's ${v.n} names` : where;
      });
  }, [d]);

  return (
    <div className="card card-compact space-y-2">
      <CardHeader
        title="Vol Landscape"
        href="/vol-landscape"
        asOf={q.dataUpdatedAt || null}
        staleAfterMin={30}
      />

      {q.isLoading && <div className="text-xs text-text-muted">Loading…</div>}

      {!q.isLoading && !d && (
        <div className="text-xs text-text-muted">Vol landscape unavailable.</div>
      )}

      {d && (
        <>
          {d.regime && (
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-[0.65rem] font-bold px-1.5 py-0.5 rounded bg-accent/15 text-accent">
                {d.regime}
              </span>
              {d.regime_action && (
                <span className="text-[0.65rem] text-text">{d.regime_action}</span>
              )}
            </div>
          )}

          {s && (
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[0.62rem] text-text-muted tabular-nums">
              <span title="Average front-month implied vol across the scanned universe.">
                avg IV <span className="text-text">{s.avg_iv?.toFixed(1)}</span>
                <Ref h={d.history?.avg_iv} gap={refPartial} />
              </span>
              <span title="Implied over realised. Above 1 means options are pricing more movement than has been delivered.">
                IV/HV <span className="text-text">{s.avg_ivhv?.toFixed(2)}</span>
                <Ref h={d.history?.avg_ivhv} gap={refPartial} />
              </span>
              <span title="Names whose term structure is inverted — front vol above back vol, which prices near-term event risk.">
                <span className="text-text">{s.n_inverted}</span> inverted
                <span className="text-text-muted/70"> of {s.n_tickers}</span>
                <Ref h={d.history?.n_inverted} gap={refPartial} />
              </span>
              {/* Separate denominators on purpose. Skew is counted only over
                  chains that pass put-call parity, so a shared "of 20" would
                  overstate it — the two numbers are no longer out of the same
                  pool and cannot share a label. */}
              <span title="Names with unusually steep put skew. Counted only over chains whose ATM put and ATM call agree to within put-call parity — a chain quoting stale wings gets no vote.">
                <span className="text-text">{s.n_steep_skew}</span> steep skew
                <span className="text-text-muted/70"> of {s.n_skew_rated ?? s.n_tickers}</span>
                <Ref h={d.history?.n_steep_skew} gap={refPartial} />
              </span>
            </div>
          )}

          {/* RELATIVE TO WHAT. A bare "avg IV 20.7" reads as a fact about the
              market; without a reference set it is a fact about nothing. The
              percentiles above are computed and typed already — they were just
              never rendered, so the card printed raw levels and the reader had
              no way to tell whether 20.7 was calm, ordinary or extreme.

              When the reference does not exist yet, say so ONCE here rather
              than stamping "n/a" on every stat. Silence would be worse than
              either: an uncontextualised number looks identical to a
              contextualised one that happens to be normal. */}
          {refNote && (
            <p className="text-[0.58rem] text-text-muted/80 leading-snug">
              {refNote}
            </p>
          )}

          {/* A cut sitting at the median of the cross section splits the
              universe in half, so a count taken against it cannot separate a
              regime from its opposite — "10 of 17 have steep skew" is then
              close to "10 of 17 are above average". The backend already
              discloses this in `thresholds`; nothing displayed it. Shown only
              when it is true, because a cut that DOES discriminate is not news. */}
          {nearMedianCuts.length > 0 && (
            <p className="text-[0.58rem] text-amber-400/80 leading-snug">
              {nearMedianCuts.join("; ")} — that count separates less than it
              appears to.
            </p>
          )}

          {/* What the scan above means for the instrument actually being traded.
              Everything else on this card describes the vol universe; this is
              the only part that answers "so what for ES". Each row is the
              measured value on the left and the reading beside it, because a
              reader who disagrees with the reading still needs the number. */}
          {(d.es_read?.reads?.length ?? 0) > 0 && (
            <div className="space-y-1 border-t border-border pt-1.5">
              <h4 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                What this says for ES
              </h4>
              {d.es_read!.reads!.map((r, i) => (
                <div key={i} className="text-[0.65rem] leading-snug">
                  <div className="flex items-baseline gap-2">
                    <span className="text-text-muted shrink-0 w-[8.5rem] truncate" title={r.label}>
                      {r.label}
                    </span>
                    <span className="text-text font-medium tabular-nums">{r.value}</span>
                  </div>
                  <p className="text-text-muted pl-[9.25rem] leading-snug">{r.note}</p>
                  {/* Rendered, not tucked into a tooltip. A caveat that only
                      appears on hover is a caveat the reader will act without. */}
                  {r.caveat && (
                    <p className="text-[0.55rem] text-text-muted/70 pl-[9.25rem] leading-snug italic">
                      {r.caveat}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}

          {divergences.length === 0 ? (
            <div className="text-xs text-text-muted">No cross-asset dislocations right now.</div>
          ) : (
            <div className="space-y-1 text-[0.65rem]">
              {divergences.map((x, i) => (
                <div key={i} className="flex items-start gap-2" title={x.description}>
                  <span className="font-bold shrink-0 w-[4.5rem] truncate">{x.pair}</span>
                  <span className="text-text-muted shrink-0 w-[3.5rem] truncate">{x.metric}</span>
                  <span className="text-text flex-1 min-w-0 leading-snug">{x.signal}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ─── News (derived from driver citations) ─────────────────────── */

function NewsPanel({ citations }: { citations: MarketDriverResponse["citations"] | undefined }) {
  const newsItems = useMemo(
    () => (citations ?? []).filter((c) => c.source === "news" || c.source === "release").slice(0, 6),
    [citations]
  );
  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Market-Moving News</h3>
        {/* Stated rather than implied by the heading. This is not a wire — it is
            the subset of the synthesis's own citations that came from headlines,
            so its coverage is whatever that model happened to cite this cycle.
            A reader who thinks they are looking at a feed will read silence here
            as "nothing happened", which is the one thing it does not mean. */}
        <span className="text-[0.55rem] text-text-muted shrink-0">cited by the synthesis above</span>
      </div>
      {newsItems.length === 0 ? (
        <p className="text-xs text-text-muted">
          The synthesis cited no headlines or releases this cycle — a statement about what it drew
          on, not about whether news broke.
        </p>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {newsItems.map((c, i) => (
            <li key={i} className="leading-snug">
              <span className="text-text">{c.label}</span>
              {c.detail && <span className="text-text-muted text-xs ml-1">— {c.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ─── Tweet Watch (Trump for now; Fed/Treasury RSS later) ──────── */

function TweetWatch() {
  const nowMin = useMinuteClock();
  const q = useQuery({
    queryKey: ["trump-monitor-home"],
    queryFn: fetchTrumpMonitor,
    refetchInterval: 2 * 60_000,
    staleTime: 90_000,
  });
  const posts: TrumpPost[] = q.data?.posts ?? [];
  const latest = posts[0];

  const sentimentColor = (s: string) => {
    const v = (s || "").toLowerCase();
    if (v.includes("bull")) return "text-gain";
    if (v.includes("bear")) return "text-loss";
    return "text-text-muted";
  };

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Tweet Watch</h3>
        <Link href="/trump-decoder" className="text-[0.6rem] text-text-muted hover:text-accent">Decoder →</Link>
      </div>
      {q.isLoading && <div className="text-xs text-text-muted">Loading…</div>}
      {q.isError && !latest && <p className="text-xs text-loss">Tweet fetch failed.</p>}
      {!q.isLoading && !latest && <p className="text-xs text-text-muted">No recent posts.</p>}
      {latest && (
        <>
          <div className="text-[0.6rem] text-text-muted flex items-center gap-2">
            <span className="font-semibold">@realDonaldTrump</span>
            {/* Both the separator and the age are gated on the age existing.
                An always-rendered "·" beside a span that goes from empty to
                filled is the same server/client text difference the ES card's
                headline rows had. */}
            {(() => {
              const age = fmtAgo(latest.timestamp, nowMin);
              return age ? (
                <>
                  <span>·</span>
                  <span>{age}</span>
                </>
              ) : null;
            })()}
            <span className={`ml-auto ${sentimentColor(latest.sentiment)}`}>{latest.sentiment}</span>
          </div>
          <p className="text-sm text-text leading-snug line-clamp-4">{latest.text}</p>
          {latest.interpretation && (
            <p className="text-xs text-text-muted leading-snug border-t border-border pt-1.5">
              {latest.interpretation}
            </p>
          )}
          {/* The sentiment tag is a model's label with no score attached HERE.
              Saying so costs one line and stops it reading as a measurement; the
              settled record for this surface lives on the decoder page. */}
          <p className="text-[0.55rem] text-text-muted/70 leading-snug">
            Sentiment and interpretation are model-written and unscored on this card — the settled
            track record for the surface is on the decoder page.
          </p>
        </>
      )}
      {q.data?.market_alert && (
        <div className="text-[0.65rem] font-semibold text-loss border-l-2 border-loss pl-2 mt-1">
          {q.data.market_alert}
        </div>
      )}
    </div>
  );
}

/* ─── Macro Calendar ──────────────────────────────────────────── */

/** The measured range multiplier, or an explicit "not measured".
 *
 *  This is what replaces reading `impact` as though it answered the sizing
 *  question. It does not: `impact` is an assigned TIMING label, and on the
 *  measured axis CPI is 1.06x and 12th of 23 while quad witching is 0.94x —
 *  narrower than an ordinary day. Both used to render identically to payrolls,
 *  which is the only event on this calendar whose expansion survives correction
 *  across the 23 tested. */
function MeasuredChip({ ev }: { ev: CalendarEvent }) {
  const m = ev.measured;
  if (!m) {
    return (
      <span
        className="text-[0.55rem] px-1 py-0.5 rounded bg-surface-alt text-text-muted/70 shrink-0"
        title="This release was not in the study's universe, so no measurement exists either way. That is a different statement from 'measured, and ordinary'."
      >
        not measured
      </span>
    );
  }
  const cls =
    m.band === "established" ? "bg-loss/15 text-loss"
      : m.band === "unconfirmed" ? "bg-amber-500/15 text-amber-400"
        : "bg-surface-alt text-text-muted";
  return (
    <span
      className={`text-[0.55rem] px-1 py-0.5 rounded tabular-nums shrink-0 font-semibold ${cls}`}
      title={
        `${m.headline}` +
        ` 95% CI [${m.ci95[0].toFixed(2)}, ${m.ci95[1].toFixed(2)}].` +
        ` ${(m.share_over_1_5x * 100).toFixed(0)}% of these prints ran past 1.5x.` +
        ` Next session ${m.next_session.toFixed(2)}x — nothing carries past the print.` +
        (m.rank_sd != null
          ? ` Its yearly rank moves by ${m.rank_sd.toFixed(1)} places, so one multiplier is not a fixed property of the event.`
          : "") +
        (m.caveat ? ` ${m.caveat}` : "")
      }
    >
      {m.multiplier.toFixed(2)}×
    </span>
  );
}

function MacroCalendar() {
  const q = useQuery({
    queryKey: ["events-home"],
    queryFn: fetchEvents,
    refetchInterval: 10 * 60_000,
    staleTime: 9 * 60_000,
  });

  // SELECTION BY BOTH AXES, because they answer different questions and
  // dropping either loses something real. A plain date-ordered slice(0, 6) used
  // to cut payrolls and CPI — the ones furthest out — so high-impact (timing)
  // events are taken first. But the measured axis has to be honoured too: an
  // event whose range expansion is established or even unconfirmed belongs in
  // the window whether or not anyone labelled it high.
  const events = useMemo(() => {
    const all = q.data?.events ?? [];
    const priority = all.filter(
      (e) => e.impact === "high" || (e.measured != null && e.measured.band !== "none")
    );
    const prioritySet = new Set(priority);
    const rest = all.filter((e) => !prioritySet.has(e));
    return [...priority, ...rest.slice(0, Math.max(0, 12 - priority.length))]
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  }, [q.data?.events]);

  const read = useMemo(() => {
    if (events.length === 0) return null;
    const measured = events.filter((e) => e.measured != null);
    const established = measured.filter((e) => e.measured!.survives_fdr);
    const widest = [...measured].sort(
      (a, b) => b.measured!.multiplier - a.measured!.multiplier
    )[0];
    // Labelled as a scheduled discontinuity but measured indistinguishable from
    // an ordinary session. This is the specific disagreement the card exists to
    // surface, so it is named rather than left for the reader to spot.
    const overbilled = measured.filter(
      (e) => e.impact === "high" && e.measured!.band === "none"
    );
    return { measured, established, widest, overbilled, n: events.length };
  }, [events]);

  return (
    <div className="card card-compact space-y-2">
      <CardHeader
        title="Next 2 Weeks — Macro Calendar"
        href="/economic-calendar"
        asOf={q.dataUpdatedAt || null}
        staleAfterMin={60}
      />
      {events.length === 0 ? (
        <p className="text-xs text-text-muted">
          {q.isLoading ? "Loading…" : "No scheduled events in window."}
        </p>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-1">
            {events.map((ev, i) => (
              <div key={`${ev.date}-${ev.name}-${i}`} className="flex items-baseline gap-1.5 text-xs min-w-0">
                <span className="text-text-muted text-[0.6rem] tabular-nums w-[4.5rem] shrink-0">
                  {ev.date.slice(5)} · {ev.days_away === 0 ? "today" : `+${ev.days_away}d`}
                </span>
                <span
                  className={`truncate min-w-0 flex-1 ${ev.impact === "high" ? "text-text font-semibold" : "text-text-muted"}`}
                  title={`${ev.name}${ev.note ? ` — ${ev.note}` : ""}`}
                >
                  {/* The dot now means TIMING only: a release lands at a known
                      time and you should be at the screen for it. It used to be
                      read as "this will be a wide day", which is the question
                      the chip beside it answers with a measurement. */}
                  {ev.impact === "high" && (
                    <span className="text-spot mr-0.5" title="Scheduled discontinuity — a release lands at a known time">•</span>
                  )}
                  {ev.name}
                </span>
                <MeasuredChip ev={ev} />
              </div>
            ))}
          </div>

          {read && (
            <Takeaway
              tone={read.established.length > 0 ? "warn" : "neutral"}
              headline={
                read.established.length > 0
                  ? `${read.established.map((e) => e.name).join(", ")} — the only event in this window whose range expansion survives correction, at ${read.established[0].measured!.multiplier.toFixed(2)}× a normal session over ${read.established[0].measured!.n} prints.`
                  : read.widest
                    ? `Nothing in this window has established range expansion. The widest measured is ${read.widest.name} at ${read.widest.measured!.multiplier.toFixed(2)}× a normal session, and it does not survive correction across the 23 events tested.`
                    : `Nothing in this window has been measured for range expansion.`
              }
              detail={
                `${read.measured.length} of ${read.n} shown events carry a measurement; the rest were never in the study's universe. ` +
                (read.overbilled.length > 0
                  ? `${read.overbilled.map((e) => `${e.name} (${e.measured!.multiplier.toFixed(2)}×, ${ordinal(e.measured!.rank)} of ${e.measured!.of})`).join(", ")} ${read.overbilled.length === 1 ? "is" : "are"} marked as a scheduled discontinuity but measured indistinguishable from an ordinary session — a reason to be at the screen, not a reason to size the whole day. `
                  : "") +
                `The dot marks timing; the multiplier is measured magnitude. Neither carries direction — the study measured |move| only — and every next-session multiplier sits near 1.0, so a wide print says nothing about the day after it.`
              }
            />
          )}
        </>
      )}
    </div>
  );
}

/* ─── Islands ─────────────────────────────────────────────────── */

/** The two blocks a session actually turns on. Rendered ahead of the Suspense
 *  boundary in `app/page.tsx` so it paints as soon as its own two fetches
 *  resolve, rather than waiting on eleven. */
export function HomeFast() {
  return (
    <div className="space-y-4">
      <MarketPulse />
      {/* RANKS THE PAGE, SO IT HAS TO BE AT THE TOP OF IT.
          Measured, this block is 137px and the ES card below it is 3,967px, so
          living in the swing island put the one component whose job is "where
          should I look" four screens down — you had to scroll past the largest
          card on the page to reach the thing that tells you whether scrolling
          is worth it.
          It can live here because it no longer depends on the swing island:
          its queries are enabled and carry each shadowed card's own cadence, so
          it stays live even when a horizon band is collapsed and its cards
          unmount. The cost is four extra client requests on first load, all of
          them server-cached and pre-warmed reads. */}
      <UnusualToday />
      <EsBriefing />
    </div>
  );
}

/** Everything on a swing horizon or slower, streamed in behind the fast half. */
export function HomeSwing() {
  const driverQ = useMarketDriver();

  return (
    <div className="space-y-5">
      {/* One interpretation for the whole page. It reads every card's cached
          data, so it lives in THIS island rather than the fast one — firing it
          before these boards hydrate would have it synthesise a page it cannot
          see. It also now names the blocks it could not read. */}
      <PageInterpretation />

      <HorizonBand id="today" label="Today" hint="moves through the session, resets overnight">
        <MarketDriverCard />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <SectorRelative />
          <VolLandscapeSnapshot />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <NewsPanel citations={driverQ.data?.citations} />
          <TweetWatch />
        </div>
      </HorizonBand>

      <HorizonBand
        id="swing"
        label="Weeks to months"
        hint="unchanged since this morning — worth re-reading when the week turns"
      >
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <SectorRrgCard />
          <CtaFlows />
        </div>
        {/* Rate pricing sits beside the macro scorecard because they answer the
            same swing-horizon question from opposite ends: the scorecard reads
            the z-score of recent CHANGE in financial conditions, this reads the
            LEVEL the market expects policy to settle at. */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <MacroPressure />
          <FedProbabilitiesCard />
        </div>
        <SpValuationStrip />
      </HorizonBand>

      <HorizonBand
        id="book"
        label="The book"
        hint="a system already committed to — rebalances monthly, issues no signal"
      >
        <TsmomBookCard />
      </HorizonBand>

      <HorizonBand id="ahead" label="Scheduled ahead" hint="known dates, measured magnitudes">
        <MacroCalendar />
      </HorizonBand>
    </div>
  );
}

/** Kept so any caller still importing the page as one unit renders the same
 *  thing, in the same order, without the streaming split. */
export default function HomeClient() {
  return (
    <div className="space-y-5">
      <HomeFast />
      <HomeSwing />
    </div>
  );
}
