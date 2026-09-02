"use client";

/**
 * Home — real-time market dashboard (client islands).
 *
 * SEVENTEEN TOP-LEVEL BLOCKS BECAME NINE. The page had grown by addition
 * without subtraction: four synthesis layers were stacked on top to say where
 * to look, and no board was ever removed from underneath, so the same reading
 * appeared two and three times at different resolutions. `UnusualToday` ranked
 * four boards that also rendered as full cards below it, and linked to other
 * pages rather than to the copy directly underneath.
 *
 * Nine cards were demoted. Seven are rows in `BoardRoster` — headline reading,
 * percentile where the board keeps history, age, and a link — expanding in
 * place to the original card, unchanged. The eighth, the news panel, moved
 * inside the driver card whose citations it was filtering. The ninth, tweet
 * watch, was deleted: not read, a model's own score with nothing to place it
 * against, and a 17.6s call on every load. Nothing else was deleted and nothing
 * is hidden: a row states what its board says.
 *
 * ORGANISED BY INFORMATION HALF-LIFE, which is this page's own idea and worth
 * restating because a first pass at the roster replaced it with percentile
 * ranking and was wrong to. Half-life answers "do I need to read this again
 * this morning" and applies to every board; a percentile answers a different
 * question and only three of them keep the history to answer it. The bands did
 * not disappear — they moved inside the roster, where they group rows instead
 * of cards.
 *
 * SPLIT INTO TWO ISLANDS. `HomeFast` is the pulse strip and the ES briefing —
 * the two things a session actually turns on. `HomeSwing` is everything else,
 * and `app/page.tsx` streams it behind a Suspense boundary so one slow upstream
 * cannot hold up the price at the top of the page.
 *
 *   HomeFast
 *     Market Pulse Strip                          (30s)
 *     THE BOARDS      seven rows in three half-life bands, each lazy-loading
 *                     its own card. Above the ES briefing because a thing that
 *                     indexes the page cannot sit four screens down it.
 *     ES Session Briefing                         (3 min — levels develop)
 *   HomeSwing
 *     One interpretation for the whole page
 *     Ask about this page
 *     What's Driving Markets (+ the headlines it cited)
 *     THE BOOK:       12-month trend book
 *     SCHEDULED AHEAD: macro calendar
 *
 * The two remaining top-level bands still collapse, and are still never
 * collapsed by default — hiding built work behind a bare chevron is not an
 * improvement, which is exactly why a demoted board got a line that speaks
 * rather than a chevron that does not.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchSnapshot,
  fetchMarketDriver,
  fetchEvents,
  type MarketDriverResponse,
  type CalendarEvent,
} from "@/lib/api";
import { PULSE_TICKERS, PULSE_LABELS, ordinal } from "@/lib/home-constants";
import EsBriefing from "@/components/home/es-briefing";
import PageInterpretation from "@/components/home/page-interpretation";
import HomeChat from "@/components/home/home-chat";
import TsmomBookCard from "@/components/home/tsmom-book";
import BoardRoster from "@/components/home/board-roster";
// THE NINE DEMOTED BOARDS ARE DELIBERATELY NOT IMPORTED HERE. They are reached
// only through `next/dynamic` inside `board-roster.tsx`, and that is the entire
// mechanism by which this change reduces the bundle: measured across four
// production builds, deleting the swing half's render AND its import from
// `app/page.tsx` moved the route's JavaScript by zero bytes, because every
// board was a static import of THIS module and tree-shaking does not cross the
// "use client" boundary. Adding one back here silently undoes that — the page
// would still look identical.
import { CardHeader, HorizonBand, Takeaway, fmtAgo, useMinuteClock } from "@/components/home/primitives";

// `fmtAgo` moved to primitives and now takes the minute clock. It used to call
// `Date.now()` during render, which on an ISR-cached page is wrong by
// construction — see the function's own comment.

/** `Number.isNaN` rather than falling through to the `n > 0 ? gain : loss`
 *  ternary: NaN fails every comparison, so an uncomputable change was reaching
 *  the false branch and painting RED — a missing number rendered as a loss, in
 *  the pulse strip. Zero and null were already handled; NaN was not. */
function pctClass(n: number | undefined | null): string {
  if (n == null || Number.isNaN(n) || n === 0) return "text-text-muted";
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
                {s.price ? s.price.toLocaleString("en-US", { maximumFractionDigits: 2 }) : "—"}
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

          {/* The headlines this synthesis actually drew on. Was its own card
              beside this one; it is a filter over this card's own citations, so
              it belongs under them. */}
          <NewsPanel citations={d.citations} />
        </>
      )}
    </div>
  );
}


/* ─── News (derived from driver citations) ─────────────────────── */

/** MOVED INSIDE THE DRIVER CARD rather than sitting beside it.
 *
 *  It was a card of its own in the Today band, which put two headline lists on
 *  one page — this one and the ES card's own `d.news` — from different sources,
 *  about a screen apart. And it never had data of its own: it is a filter over
 *  `market-driver`'s citations, so it was a separate card rendering a subset of
 *  the card above it.
 *
 *  Nesting it under the prose it cites makes "cited by the synthesis above"
 *  literally true for the first time, and removes a block from the page without
 *  removing a word of content. */
function NewsPanel({ citations }: { citations: MarketDriverResponse["citations"] | undefined }) {
  const newsItems = useMemo(
    () => (citations ?? []).filter((c) => c.source === "news" || c.source === "release").slice(0, 6),
    [citations]
  );
  return (
    <div className="space-y-2 pt-2 border-t border-border">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">Market-Moving News</h3>
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
      {/* INDEXES THE PAGE, SO IT HAS TO BE AT THE TOP OF IT — the rule
          `UnusualToday` established and then had to satisfy alone.
          Measured, that ribbon was 137px and the ES card below it is 3,967px,
          so anything living in the swing island sat four screens down: you had
          to scroll past the largest card on the page to reach the thing that
          tells you whether scrolling is worth it.
          `BoardRoster` replaces it here and does more — seven boards rather
          than four, grouped by how fast each moves, each expanding to its full
          card. `UnusualToday` is retired: it ranked four of these same boards
          off the same payloads, which made it a second ranking layer 4,000px
          from this one.
          It can live in the fast island for the reason the ribbon could: its
          queries are enabled and carry each board's own cadence, so it stays
          live independently of the swing half. Its values arrive from the swing
          island's dehydrated cache a beat later, and every row says "reading…"
          rather than a dash until they do. */}
      <BoardRoster />
      <EsBriefing />
    </div>
  );
}

/** Everything on a swing horizon or slower, streamed in behind the fast half.
 *
 *  NINE CARDS BECAME SEVEN LINES, IN THE FAST ISLAND. Sector relative, the vol
 *  landscape, sector rotation, CTA flows, macro pressure, the priced Fed path
 *  and valuation are rows in `BoardRoster`, which now sits above the ES card
 *  because indexing the page from four screens below it is not indexing. The
 *  eighth, the news panel, moved inside the driver card whose citations it was
 *  already filtering. The ninth, tweet watch, is gone: not read, no reference
 *  set, and a 17.6s call on every load.
 *
 *  This is subtraction, not hiding. The page had grown to seventeen top-level
 *  blocks by adding four synthesis layers on top and never removing a board
 *  from underneath, so the same information appeared two and three times at
 *  different resolutions. A row states what its board says; a reader who never
 *  opens one has still been told.
 *
 *  THE BANDS DID NOT GO AWAY — they moved inside the roster. Grouping by
 *  information half-life was this page's own idea and it is the right one: it
 *  answers "do I need to read this again this morning", and unlike a percentile
 *  it applies to every board rather than the three that keep history. */
export function HomeSwing() {
  return (
    <div className="space-y-5">
      {/* One interpretation for the whole page. It reads every card's cached
          data, so it lives in THIS island rather than the fast one — firing it
          before these boards hydrate would have it synthesise a page it cannot
          see. It also now names the blocks it could not read.
          Still fed after the demotion: the roster runs the same query keys at
          the same cadences the cards did, so every entry this panel subscribes
          to with `enabled: false` is still there and still current. */}
      <PageInterpretation />

      {/* Sits directly under the one-shot interpretation because they answer
          the same page from opposite ends: that panel says what today means
          without being asked, this one answers what it did not cover. Below it,
          not above, so the unprompted read is still what a reader meets first. */}
      <HomeChat />

      <MarketDriverCard />

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
