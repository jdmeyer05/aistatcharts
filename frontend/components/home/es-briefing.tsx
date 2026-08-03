"use client";

/**
 * ES session briefing — the top-of-page synthesis for trading the E-mini intraday.
 *
 * This card answers "what should I expect from this session", for someone who
 * is in and out the same day and reloads the page through it. Everything is
 * phrased relative to the current moment.
 *
 * THE READ IS DERIVED, NOT WRITTEN. Every clause in the lead paragraph comes
 * from an arithmetic comparison against a level that is also displayed in the
 * ladder below it, so any statement can be checked against the number it came
 * from. Nothing here is a judgement call, and nothing is an entry signal —
 * it describes conditions and leaves the trade to the trader.
 *
 * HORIZONS ARE KEPT SEPARATE. Location, levels and the clock are intraday.
 * CTA flow and the macro scorecard are swing-horizon inputs and are labelled
 * as directional lean, never as intraday triggers — collapsing the two is the
 * easiest way to make this card actively misleading.
 */

import { useMemo, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchEsBrief,
  type EsBrief,
  type EsImpact,
  type EsLevel,
  type EsScheduleItem,
} from "@/lib/api";
import CandleContextBlock from "@/components/home/candle-context";
import OvernightRead from "@/components/home/overnight-read";

/* ─── formatting ──────────────────────────────────────────────── */

/** No thousands separator: ES is quoted 7503.50 on every ladder and DOM, and
 *  the grouping comma misaligns a column of tabular numbers. */
const fmtPx = (n: number) => n.toFixed(2);

/** ES trades in quarter-points; "handles" is the trader's word for index points. */
const fmtHandles = (n: number) => `${n > 0 ? "+" : ""}${n.toFixed(2)}`;

const pctClass = (n: number | null | undefined) =>
  n == null || n === 0 ? "text-text-muted" : n > 0 ? "text-gain" : "text-loss";

function fmtCountdown(mins: number): string {
  const a = Math.abs(mins);
  const d = Math.floor(a / 1440);
  const h = Math.floor((a % 1440) / 60);
  const m = a % 60;
  // Days matter now: in the evening the schedule is the NEXT session's, so a
  // countdown can legitimately run past a day.
  const body = d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
  return mins >= 0 ? `in ${body}` : `${body} ago`;
}

function fmtAgo(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return "";
  const min = Math.floor(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return hr < 24 ? `${hr}h ago` : `${Math.floor(hr / 24)}d ago`;
}


/** Wall clock as an external store, so countdowns stay live between server
 *  refreshes without a setState-in-effect cascade. Quantised to the minute so
 *  React's identity check settles instead of changing on every render. The
 *  server snapshot is null, so SSR and hydration use the server-computed
 *  `minutes_away` and there is no timestamp mismatch to reconcile. */
const CLOCK = {
  subscribe(onChange: () => void) {
    const t = setInterval(onChange, 30_000);
    return () => clearInterval(t);
  },
  getSnapshot: (): number | null => Math.floor(Date.now() / 60_000),
  getServerSnapshot: (): number | null => null,
};

function impactClass(i: EsImpact): string {
  if (i === "high") return "bg-loss/15 text-loss";
  if (i === "medium") return "bg-amber-500/15 text-amber-400";
  return "bg-surface-alt text-text-muted";
}

const PHASE_CLASS: Record<string, string> = {
  rth_open: "bg-gain/15 text-gain",
  rth_midday: "bg-accent/15 text-accent",
  rth_close: "bg-amber-500/15 text-amber-400",
  premarket: "bg-spot/15 text-spot",
  closed: "bg-surface-alt text-text-muted",
};

/** Group → ladder accent. Overnight is deliberately dimmer than the RTH
 *  groups: those levels are made on thin volume and break more easily. */
const CONDITION_CLASS: Record<string, string> = {
  favourable: "border-gain/40 bg-gain/5",
  workable: "border-border bg-surface-alt/30",
  poor: "border-amber-400/40 bg-amber-500/5",
  "stand aside": "border-loss/40 bg-loss/5",
  "market closed": "border-border bg-surface-alt/30",
};

const GROUP_CLASS: Record<string, string> = {
  Today: "border-l-accent",
  "Prior session": "border-l-spot",
  Overnight: "border-l-text-muted/40",
  "Volume profile": "border-l-violet-400",
};

/* ─── the derived read ────────────────────────────────────────── */

interface Read {
  location: string;
  structure: string | null;
  room: string | null;
  regime: string | null;
  clock: string | null;
  lean: string | null;
}

function buildRead(d: EsBrief, liveMins: (e: EsScheduleItem) => number): Read | null {
  const lv = d.levels;
  if (!lv?.available || !lv.levels?.length) return null;

  const by = new Map<string, EsLevel>(lv.levels.map((l) => [l.key, l]));
  const last = lv.last;
  const g = (k: string) => by.get(k);

  // Pre-open there is no session yet, so the frame is the overnight range and
  // the gap. Saying "developing" about a session that has either finished or
  // not begun is how a briefing quietly lies about what it is showing.
  // `mode` absent means an older API build (the two deploy independently), so
  // neither "developing" nor "final" can be asserted — fall back to neutral
  // wording rather than guessing, since guessing wrong misdescribes the frame.
  const knowsMode = Boolean(lv.mode);
  const isPre = lv.mode === "premarket";
  const isDone = lv.mode === "last_session" || lv.rth_complete === true;

  /* 1. Location — where price sits relative to value and VWAP. This single
     fact does more to set trend-vs-rotation expectations than anything else
     on the card, so it leads. */
  const vah = g("vah");
  const val = g("val");
  const vwap = g("vwap");
  const parts: string[] = [];
  // Follows where the profile actually came from — early in a session the
  // developing one has too few bars to mean anything, so the payload serves
  // the prior session's and the sentence has to say so.
  const whose = lv.profile_is_prior_session || isPre
    ? "the prior session's value area"
    : "the value area";

  if (vah && val) {
    if (last > vah.value) {
      parts.push(
        `Price is above ${whose} high (${fmtPx(vah.value)}), in upside discovery — ` +
          `outside value, sessions tend to trend rather than rotate`
      );
    } else if (last < val.value) {
      parts.push(
        `Price is below ${whose} low (${fmtPx(val.value)}), in downside discovery — ` +
          `outside value, sessions tend to trend rather than rotate`
      );
    } else {
      parts.push(
        `Price is inside ${whose} (${fmtPx(val.value)}–${fmtPx(vah.value)}), ` +
          `where rotation is more likely than trend`
      );
    }
  }
  if (vwap) {
    const dist = last - vwap.value;
    parts.push(
      `${Math.abs(dist).toFixed(2)} handles ${dist >= 0 ? "above" : "below"} session VWAP ` +
        `(${fmtPx(vwap.value)})`
    );
  }
  const location = parts.length ? `${parts.join(", and ")}.` : "";

  /* 2. Structure — how much range is spent and how price is positioned against
     the prior close. Pre-open that means the overnight range and the gap;
     during or after the session it means the RTH range. */
  const structBits: string[] = [];
  const th = g("today_high");
  const tl = g("today_low");
  const ph = g("py_high");
  const pl = g("py_low");
  const onH = g("on_high");
  const onL = g("on_low");
  const pc = g("py_close");

  if (isPre && onH && onL) {
    const onRange = onH.value - onL.value;
    const pos = onRange > 0 ? ((last - onL.value) / onRange) * 100 : null;
    structBits.push(
      `Overnight has covered ${onRange.toFixed(2)} handles ` +
        `(${fmtPx(onL.value)}–${fmtPx(onH.value)})` +
        (pos != null ? `, with price ${Math.round(pos)}% up that range` : "")
    );
  } else if (th && tl) {
    const range = th.value - tl.value;
    const verb = !knowsMode
      ? "The session range is"
      : isDone
        ? "The session's final range was"
        : "The developing range is";
    if (ph && pl) {
      const prior = ph.value - pl.value;
      const pct = prior > 0 ? (range / prior) * 100 : null;
      structBits.push(
        `${verb} ${range.toFixed(2)} handles` +
          (pct != null
            ? `, ${Math.round(pct)}% of the prior session's ${prior.toFixed(2)}` +
              (isDone ? "" : pct < 50 ? " — room left before the day is stretched" : pct > 100 ? " — already an outsized day" : "")
            : "")
      );
    } else {
      structBits.push(`${verb} ${range.toFixed(2)} handles`);
    }
  }

  // Pre-open the gap is measured from the live price; once the bell has gone
  // it is measured from where the session actually opened.
  const open = g("today_open");
  const gapFrom = isPre ? last : open?.value;
  if (gapFrom != null && pc) {
    const gap = gapFrom - pc.value;
    const verb = isPre ? "it is trading" : "it opened";
    if (Math.abs(gap) >= 0.25) {
      structBits.push(
        `${verb} ${Math.abs(gap).toFixed(2)} ${gap > 0 ? "above" : "below"} the prior close ` +
          `(${fmtPx(pc.value)})` + (isPre ? " — the gap as it stands into the open" : "")
      );
    } else {
      structBits.push(`${verb} flat to the prior close (${fmtPx(pc.value)})`);
    }
  }
  const structure = structBits.length ? `${structBits.join(", and ")}.` : null;

  /* 2b. Room — how much of the day's expected range is left. This is the
     sizing clause: a level three-quarters of an expected range away is not a
     realistic target for a session that has already spent most of it. */
  let room: string | null = null;
  const em = d.expected_move;
  if (em?.available && em.expected_range) {
    // The estimate always describes the session AHEAD, so say so when that
    // isn't today — otherwise a weekend read sounds like a live one.
    const when = lv.mode === "last_session" ? "for the next session" : "today";
    const bits: string[] = [
      `The market is pricing a ${em.expected_range.toFixed(0)}-handle range ${when} ` +
        `(${em.headline?.source ?? "implied"})`,
    ];
    if (em.consumed) {
      bits.push(
        `${em.consumed.pct.toFixed(0)}% of it is already spent` +
          (em.consumed.pct >= 100
            ? " — continuation from here is the tail, not the base case"
            : em.consumed.pct <= 40
              ? " — the day is still coiled"
              : "")
      );
    } else if (em.overnight) {
      bits.push(`the overnight range alone has used ${em.overnight.pct_of_expected.toFixed(0)}% of it`);
    }
    if (em.vol_regime && em.vol_regime.label !== "in line") {
      bits.push(
        em.vol_regime.label === "implied rich"
          ? "options are pricing more movement than ES has been delivering"
          : "ES has been moving more than options are pricing"
      );
    }
    room = `${bits.join(", and ")}.`;
  }

  /* 2c. Regime — which way dealer hedging cuts. This decides whether the
     location read above argues for fading or for following. */
  let regime: string | null = null;
  const gam = d.gamma;
  if (gam?.available && gam.regime) {
    const side = gam.above_flip ? "above" : "below";
    regime =
      `Dealers are net ${gam.regime} gamma` +
      (gam.flip_es != null ? `, with price ${side} the gamma flip at ${fmtPx(gam.flip_es)}` : "") +
      (gam.regime === "long"
        ? " — hedging leans against moves, so breakouts struggle and rotation is the base case."
        : " — hedging amplifies moves, so fading extremes is the losing side here.") +
      (gam.call_wall_es != null && gam.put_wall_es != null
        ? ` Heaviest strikes sit at ${fmtPx(gam.put_wall_es)} and ${fmtPx(gam.call_wall_es)}.`
        : "");
  }

  /* 3. The clock. */
  let clock: string | null = null;
  const upcoming = (d.schedule ?? []).filter((e) => liveMins(e) > 0);
  const next = upcoming.length
    ? upcoming.reduce((a, b) => (liveMins(a) <= liveMins(b) ? a : b))
    : null;
  if (next) {
    const mins = liveMins(next);
    const hedge = next.derived ? " (scheduled time derived from the usual release rule)" : "";
    clock =
      `Next on the clock: ${next.name} at ${next.time_et} ET, ${fmtCountdown(mins)}` +
      ` — ${next.impact} impact${hedge}.` +
      (mins <= 30 && next.impact !== "low"
        ? " Liquidity thins and spreads widen into a print this close."
        : next.before_open && next.impact === "high"
          ? " A pre-open print of this size makes the overnight range an unreliable guide."
          : "");
  } else if ((d.schedule ?? []).length) {
    clock = "Everything scheduled has already printed; the rest of the session is left to positioning and the levels.";
  } else {
    clock = "Nothing on the macro calendar for this session — no timed catalyst to trade around.";
  }

  /* 4. Directional lean — swing horizon, explicitly not an intraday trigger. */
  const leanBits: string[] = [];
  if (d.cta?.bias_1w) {
    leanBits.push(
      `CTA flow is ${d.cta.bias_1w.replace(/_/g, " ")}` +
        (d.cta.current_exposure != null
          ? ` with exposure at ${d.cta.current_exposure > 0 ? "+" : ""}${d.cta.current_exposure.toFixed(0)}`
          : "")
    );
  }
  if (d.macro?.net_label) leanBits.push(`the macro backdrop reads ${d.macro.net_label}`);
  const lean = leanBits.length
    ? `Swing-horizon lean: ${leanBits.join(", ")}. Context for which way to lean, not an intraday trigger.`
    : null;

  return { location, structure, room, regime, clock, lean };
}

/* ─── card ────────────────────────────────────────────────────── */

export default function EsBriefing() {
  const q = useQuery<EsBrief>({
    // Levels DEVELOP intraday — session high/low, VWAP and the profile all
    // move — so this refreshes far more often than the 30-45min swing boards.
    queryKey: ["es-brief"],
    queryFn: fetchEsBrief,
    refetchInterval: 3 * 60_000,
    staleTime: 2 * 60_000,
  });

  const d = q.data;

  const nowMin = useSyncExternalStore(
    CLOCK.subscribe,
    CLOCK.getSnapshot,
    CLOCK.getServerSnapshot
  );

  // Counts down from the release's absolute instant. Falls back to the
  // server's figure before mount, or if an older API build sent no `when`.
  const liveMins = useMemo(() => {
    return (e: EsScheduleItem): number => {
      if (nowMin == null || !e.when) return e.minutes_away;
      const t = Date.parse(e.when);
      if (!Number.isFinite(t)) return e.minutes_away;
      return Math.round(t / 60_000) - nowMin;
    };
  }, [nowMin]);

  const read = useMemo(() => (d ? buildRead(d, liveMins) : null), [d, liveMins]);

  const lv = d?.levels;
  const session = d?.session;

  // Quote age is derived from the absolute `asof`, never from the server's
  // `bar_age_min`. The API serves a slightly stale payload while it rebuilds
  // behind the request, and a baked-in age would then understate how old the
  // price is — the one number that must never read younger than it is, since
  // every distance on this card is measured from that price.
  const { barAgeMin, isStale } = useMemo(() => {
    const fallback = { barAgeMin: lv?.bar_age_min ?? null, isStale: Boolean(lv?.stale) };
    if (!lv?.asof || nowMin == null) return fallback;
    const t = Date.parse(lv.asof);
    if (!Number.isFinite(t)) return fallback;
    const age = nowMin - Math.round(t / 60_000);
    // Only a session that is actually trading can have a stale feed — a
    // four-hour-old bar on a Saturday is simply the last print.
    const trading = lv.mode === "rth" || lv.mode === "premarket";
    return { barAgeMin: age, isStale: trading && age > 15 };
  }, [lv, nowMin]);

  // Say plainly which frame the ladder describes. Driven off the payload's
  // session mode rather than a date comparison, because the misleading case
  // (pre-open, and the completed Friday session) shares today's date.
  const frameLabel = useMemo(() => {
    if (!lv?.available) return null;
    // Fail safe on an older payload. The frontend and the API deploy
    // independently, so a frontend-first rollout can see a response with no
    // `mode`; asserting "developing" there would be the most misleading of the
    // three states. Say nothing rather than something wrong.
    if (!lv.mode) return null;
    const md = (lv.session_date ?? "").split("-");
    const stamp = md.length === 3 ? `${md[1]}/${md[2]}` : "";
    if (lv.mode === "premarket") {
      return { text: "pre-open · no RTH session yet", title: "The cash session hasn't opened. Session levels are omitted rather than filled in from yesterday; the overnight range is the developing one." };
    }
    if (lv.mode === "last_session" || lv.rth_complete) {
      return { text: `session of ${stamp} · complete`, title: "The cash session has closed. These are its final values, not a developing range." };
    }
    return { text: `session of ${stamp} · developing`, title: "The cash session is open; session high, low and VWAP are still moving." };
  }, [lv]);

  // Ladder rows: every level plus the last price, sorted high to low, so the
  // card reads the way a price ladder does.
  const ladder = useMemo(() => {
    if (!lv?.available || !lv.levels?.length) return [];
    const rows: Array<{ kind: "level"; l: EsLevel } | { kind: "last"; value: number }> =
      lv.levels.map((l) => ({ kind: "level" as const, l }));
    rows.push({ kind: "last" as const, value: lv.last });
    return rows.sort((a, b) => (b.kind === "last" ? b.value : b.l.value) - (a.kind === "last" ? a.value : a.l.value));
  }, [lv]);

  // After 18:00 ET the schedule belongs to the next session, so the heading
  // has to say which one — "today" beside tomorrow's payrolls is a lie.
  const scheduleScope = useMemo(() => {
    if (d?.schedule_is_today === false && d?.session_day) {
      const [, m, day] = d.session_day.split("-");
      return { label: `next session (${m}/${day})`, next: true };
    }
    return { label: "today", next: false };
  }, [d]);

  const upcoming = useMemo(
    () => (d?.schedule ?? []).filter((e) => liveMins(e) > 0).sort((a, b) => liveMins(a) - liveMins(b)),
    [d?.schedule, liveMins]
  );

  return (
    <div className="card space-y-3">
      {/* header */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-sm font-bold uppercase tracking-wider text-accent">ES Session Briefing</h2>
            {session && (
              <span
                className={`px-2 py-0.5 rounded text-[0.6rem] font-bold uppercase tracking-wider ${
                  PHASE_CLASS[session.phase] ?? "bg-surface-alt text-text-muted"
                }`}
                title={session.note}
              >
                {session.label}
              </span>
            )}
            {lv?.available && (
              <span className="text-sm font-semibold tabular-nums">
                {fmtPx(lv.last)}
                <span className="ml-1 text-[0.6rem] font-normal text-text-muted">ES</span>
              </span>
            )}
          </div>
          <div className="text-[0.6rem] text-text-muted mt-0.5">
            {session?.note ?? "E-mini S&P — intraday session context, levels and scheduled risk"}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span
            className={`text-[0.6rem] ${isStale ? "text-loss font-semibold" : "text-text-muted"}`}
            title={
              isStale
                ? `The feed is ${barAgeMin} minutes behind a session that is trading. Every level and distance on this card is computed off that stale price — check a live quote before acting.`
                : "Age of the last 5-minute bar."
            }
          >
            {lv?.asof ? `bars ${fmtAgo(lv.asof)}` : d?.asof ? fmtAgo(d.asof) : ""}
          </span>
          <button
            type="button"
            onClick={() => q.refetch()}
            disabled={q.isFetching}
            className="text-[0.65rem] px-2 py-1 rounded border border-border hover:bg-surface-alt disabled:opacity-50"
            title="Recompute the briefing"
          >
            {q.isFetching ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {q.isLoading && (
        <div className="py-10 text-center">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-xs text-text-muted mt-2">Reading the session…</p>
        </div>
      )}

      {!q.isLoading && !d?.available && (
        <div className="py-4 flex items-baseline gap-2 flex-wrap">
          <p className="text-xs text-text-muted">
            {q.isError ? "Couldn't load the session briefing." : "Session briefing unavailable."}
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

      {d?.available && (
        <>
          {/* A stale feed makes every distance on this card wrong by the same
              amount, so it outranks the read rather than sitting beside it. */}
          {/* Three different things add to this number and only one of them is
              the vendor. Saying "the feed is behind" blames a delay the tier
              guarantees for staleness our own cache created, and leaves a
              reader with nothing to do about it. */}
          {isStale && (
            <div className="border-l-2 border-l-loss bg-loss/10 px-3 py-2 rounded-r text-[0.68rem] leading-snug">
              <span className="font-semibold text-loss">Quote is {barAgeMin} minutes old.</span>{" "}
              <span className="text-text">
                Every level distance below is measured from it. Check a live quote before acting.
              </span>
              <span className="text-text-muted">
                {lv?.quote_delayed
                  ? " The futures feed is a delayed tier (~10 min by measurement), which is the floor"
                  : " Part of this is the feed"}
                {typeof lv?.bar_age_min === "number" && typeof lv?.quote_age_min === "number"
                  ? `; the quote itself was ${lv.quote_age_min}m old when this card was built and the last 5-minute bar ${lv.bar_age_min}m`
                  : ""}
                . The rest is this page holding a cached payload — reload to clear that part.
              </span>
            </div>
          )}

          {/* Across a quarterly roll the continuous series steps by the roll
              spread, so cross-session distances can be wrong by tens of
              handles. Nothing else on the card would reveal it. */}
          {lv?.contract_roll_risk && (
            <div className="border-l-2 border-l-amber-400 bg-amber-500/10 px-3 py-2 rounded-r text-[0.68rem] leading-snug">
              <span className="font-semibold text-amber-400">Quarterly roll window.</span>{" "}
              <span className="text-text">
                ES=F is a continuous front-month series, so prior-session levels may come from the
                expiring contract while the last price is the new one — distances across sessions can
                be off by the roll spread. Verify against your platform&apos;s contract before sizing.
              </span>
            </div>
          )}

          {session?.holiday && (
            <div className="border-l-2 border-l-spot bg-spot/10 px-3 py-2 rounded-r text-[0.68rem] leading-snug text-text">
              <span className="font-semibold">Holiday schedule.</span> {session.holiday}
            </div>
          )}

          {/* Conditions gate. Leads because "should I engage with this session
              at all" precedes every other question on the card, and standing
              aside is the decision that saves the most money. */}
          {d.conditions?.available && (
            <div className={`border rounded px-3 py-2 ${CONDITION_CLASS[d.conditions.verdict] ?? "border-border"}`}>
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  Conditions
                </span>
                <span className="text-xs font-bold uppercase tracking-wide">
                  {d.conditions.verdict}
                </span>
                {d.conditions.score != null && (
                  <span className="text-[0.6rem] tabular-nums text-text-muted">
                    score {d.conditions.score > 0 ? "+" : ""}{d.conditions.score}
                  </span>
                )}
                <span className="text-[0.65rem] text-text">{d.conditions.note}</span>
              </div>
              {d.conditions.reasons.length > 0 && (
                <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1.5">
                  {d.conditions.reasons.map((r, i) => (
                    <span key={i} className="text-[0.6rem] whitespace-nowrap" title={r.why}>
                      <span
                        className={`tabular-nums font-semibold ${
                          r.effect > 0 ? "text-gain" : r.effect < 0 ? "text-loss" : "text-text-muted"
                        }`}
                      >
                        {r.effect > 0 ? "+" : ""}{r.effect}
                      </span>{" "}
                      <span className="text-text-muted">{r.factor}</span>
                    </span>
                  ))}
                </div>
              )}
              {/* An abstention that moved the verdict is readable on the card.
                  A factor scoring 0 because two estimators disagree is a caveat,
                  and caveats belong in text where they are read, not in a title
                  attribute that a trader never hovers. */}
              {d.conditions.reasons.filter((r) => r.surface).map((r, i) => (
                <p key={i} className="text-[0.6rem] text-text-muted mt-1 leading-snug">
                  {r.why}
                </p>
              ))}
              <p className="text-[0.55rem] text-text-muted mt-1">{d.conditions.disclaimer}</p>
            </div>
          )}

          {/* the read */}
          {read && (
            <div className="border-l-2 border-l-accent bg-accent/5 px-3 py-2 rounded-r text-[0.7rem] leading-relaxed space-y-1">
              {read.location && <p className="text-text">{read.location}</p>}
              {read.structure && <p className="text-text">{read.structure}</p>}
              {read.room && <p className="text-text">{read.room}</p>}
              {read.regime && <p className="text-text">{read.regime}</p>}
              {read.clock && <p className="text-text">{read.clock}</p>}
              {read.lean && <p className="text-text-muted">{read.lean}</p>}
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] gap-4">
            {/* ── price ladder ── */}
            <div className="space-y-1.5">
              <div className="flex items-baseline justify-between">
                <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  Reference levels
                </h3>
                {frameLabel && (
                  <span className="text-[0.55rem] text-text-muted" title={frameLabel.title}>
                    {frameLabel.text}
                  </span>
                )}
              </div>

              {ladder.length === 0 ? (
                <p className="text-xs text-text-muted">
                  Intraday levels unavailable.
                  {d.levels_reason && (
                    <span className="text-text-muted/70"> {d.levels_reason}.</span>
                  )}
                </p>
              ) : (
                <div className="text-[0.65rem]">
                  {ladder.map((row, i) =>
                    row.kind === "last" ? (
                      <div
                        key={`last-${i}`}
                        className="flex items-center gap-2 py-1 pl-2 border-l-2 border-l-accent bg-accent/10 rounded-r"
                      >
                        <span className="tabular-nums font-bold text-accent w-[4.5rem]">{fmtPx(row.value)}</span>
                        <span className="font-bold uppercase tracking-wider text-accent text-[0.58rem]">
                          ◀ Last
                        </span>
                      </div>
                    ) : (
                      <div
                        key={row.l.key}
                        /* Levels price cannot plausibly reach in one session are
                           dimmed rather than hidden. They still frame the market,
                           but planning a day around them is the mistake, and
                           making that visible costs nothing. */
                        className={`flex items-center gap-2 py-1 pl-2 border-l-2 ${
                          GROUP_CLASS[row.l.group] ?? "border-l-border"
                        } border-b border-b-border/30 last:border-b-0 ${
                          row.l.reach === "beyond a typical session" ? "opacity-45" : ""
                        }`}
                        title={
                          row.l.reach
                            ? `${row.l.note} — ${row.l.pct_of_expected_range}% of the expected session range away (${row.l.reach}).`
                            : row.l.note
                        }
                      >
                        <span className="tabular-nums text-text w-[4.5rem]">{fmtPx(row.l.value)}</span>
                        <span className="text-text-muted truncate flex-1 min-w-0">
                          {row.l.label}
                          {lv?.nearest?.key === row.l.key && (
                            <span
                              className="ml-1.5 text-[0.52rem] uppercase tracking-wide text-accent font-semibold"
                              title="Closest reference level to the last price — the first one price has to resolve."
                            >
                              nearest
                            </span>
                          )}
                        </span>
                        <span
                          className={`tabular-nums shrink-0 ${
                            row.l.side === "above" ? "text-gain" : "text-loss"
                          }`}
                          title={`Price is ${Math.abs(row.l.distance).toFixed(2)} handles ${row.l.side} this level.${
                            row.l.reach
                              ? ` That is ${row.l.pct_of_expected_range}% of the expected session range — ${row.l.reach}.`
                              : ""
                          }`}
                        >
                          {fmtHandles(row.l.distance)}
                        </span>
                      </div>
                    )
                  )}
                </div>
              )}
              <p className="text-[0.55rem] text-text-muted leading-snug pt-0.5">
                Distances in handles, signed from the last price. Overnight levels are made on thin Globex
                volume and break more easily than RTH levels made on size. Rows are dimmed when the level
                sits further away than a whole expected session&apos;s range — still context, but not a
                target for today. Hover any distance for its share of that range.
              </p>
            </div>

            {/* ── right column: clock + lean + headlines ──
                Column flexes and the headline list absorbs the slack, so this
                side matches the ladder's height instead of leaving a canyon
                under it on a day with nothing scheduled. */}
            <div className="flex flex-col gap-3">
              {/* scheduled risk */}
              <div className="space-y-1.5">
                <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  Scheduled risk — {scheduleScope.label}
                </h3>
                {(d.schedule ?? []).length === 0 ? (
                  <p className="text-[0.65rem] text-text-muted">
                    Nothing on the macro calendar for {scheduleScope.label}.
                  </p>
                ) : (
                  <div className="space-y-1">
                    {(d.schedule ?? []).map((e, i) => {
                      const mins = liveMins(e);
                      const done = mins <= 0;
                      return (
                        <div
                          key={`${e.name}-${i}`}
                          className={`flex items-center gap-2 text-[0.65rem] ${done ? "opacity-50" : ""}`}
                          title={e.note}
                        >
                          <span className="tabular-nums text-text-muted w-[3rem] shrink-0">{e.time_et}</span>
                          <span className="text-text truncate flex-1 min-w-0">
                            {e.name}
                            {e.derived && (
                              <span
                                className="ml-1 text-[0.52rem] uppercase text-text-muted/70"
                                title="Date derived from the usual release rule rather than a published calendar — it can slip a day."
                              >
                                est
                              </span>
                            )}
                          </span>
                          <span
                            className={`px-1.5 py-0.5 rounded text-[0.52rem] font-bold uppercase shrink-0 ${impactClass(
                              e.impact
                            )}`}
                          >
                            {e.impact}
                          </span>
                          <span className="tabular-nums text-text-muted w-[4.5rem] text-right shrink-0">
                            {done ? "released" : fmtCountdown(mins)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
                {upcoming.length === 0 && (d.schedule ?? []).length > 0 && (
                  <p className="text-[0.55rem] text-text-muted">All of today&apos;s prints are out.</p>
                )}
              </div>

              {/* lean */}
              {(d.cta || d.macro) && (
                <div className="space-y-1.5">
                  <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                    Directional lean — swing horizon
                  </h3>
                  <div className="grid grid-cols-2 gap-2 text-[0.65rem]">
                    {d.cta && (
                      <div className="border border-border rounded px-2 py-1.5">
                        <div className="text-[0.55rem] uppercase tracking-wider text-text-muted">CTA flow (1w)</div>
                        <div className="text-text font-semibold capitalize">
                          {(d.cta.bias_1w ?? "—").replace(/_/g, " ")}
                        </div>
                        {d.cta.current_exposure != null && (
                          <div className="text-text-muted tabular-nums text-[0.6rem]" title="Model points, not dollars.">
                            exposure {d.cta.current_exposure > 0 ? "+" : ""}
                            {d.cta.current_exposure.toFixed(0)}
                          </div>
                        )}
                      </div>
                    )}
                    {d.macro && (
                      <div className="border border-border rounded px-2 py-1.5">
                        <div className="text-[0.55rem] uppercase tracking-wider text-text-muted">Macro backdrop</div>
                        <div className="text-text font-semibold capitalize">
                          {d.macro.net_label ?? "—"}
                          {/* Coverage, not decoration: this verdict looked the
                              same off 2 factors as off 12 during a FRED outage. */}
                          {(d.macro.factors_unavailable ?? 0) > 0 && (
                            <span className="ml-1.5 text-[0.55rem] uppercase tracking-wide text-spot font-semibold"
                                  title={`${d.macro.factors_unavailable} of ${(d.macro.factors_reporting ?? 0) + (d.macro.factors_unavailable ?? 0)} macro factors failed to load. This verdict is built on the rest — treat it as provisional.`}>
                              {d.macro.factors_reporting}/{(d.macro.factors_reporting ?? 0) + (d.macro.factors_unavailable ?? 0)}
                            </span>
                          )}
                        </div>
                        {d.macro.counts && (
                          <div className="text-text-muted tabular-nums text-[0.6rem]">
                            {d.macro.counts.supportive}↑ · {d.macro.counts.neutral}– · {d.macro.counts.headwind}↓
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  <p className="text-[0.55rem] text-text-muted leading-snug">
                    Both are multi-day signals. They set which way to lean, not when to enter.
                  </p>
                </div>
              )}

              {/* headlines */}
              {(d.news ?? []).length > 0 && (
                <div className="space-y-1.5 flex-1 min-h-0">
                  <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                    Macro headlines
                  </h3>
                  {/* Synthesis above, headlines under it. The digest is written
                      from the same ranked set rendered below, so a reader who
                      distrusts a line can check it against the list without
                      leaving the card. Absent when the model call fails — the
                      headlines are the artifact, this is only the shortcut. */}
                  {d.news_digest?.text && (
                    <div className="border-l-2 border-accent/40 pl-2 py-0.5 space-y-0.5">
                      <p className="text-[0.65rem] leading-snug text-text whitespace-pre-line">
                        {d.news_digest.text}
                      </p>
                      <p className="text-[0.5rem] text-text-muted">
                        synthesis of the {d.news_digest.n_headlines ?? (d.news ?? []).length} headlines below
                        {d.news_digest.model ? ` · ${d.news_digest.model}` : ""}
                      </p>
                    </div>
                  )}
                  <ul className="space-y-1">
                    {/* Render at least as many as the digest read. It cites
                        headlines by name, so a shorter list would leave claims
                        the reader cannot check — which is the whole point of
                        putting the list under the synthesis. */}
                    {(d.news ?? []).slice(0, Math.max(8, d.news_digest?.n_headlines ?? 0)).map((n, i) => (
                      <li key={i} className="text-[0.65rem] leading-snug">
                        {n.url ? (
                          <a
                            href={n.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-text hover:text-accent"
                          >
                            {n.title}
                          </a>
                        ) : (
                          <span className="text-text">{n.title}</span>
                        )}
                        <span className="text-text-muted ml-1 text-[0.55rem] whitespace-nowrap">
                          {n.source}
                          {n.published ? ` · ${fmtAgo(n.published)}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>

          {/* ── expected move · dealer gamma · session structure ── */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 border-t border-border pt-3">
            {/* expected move */}
            <div className="space-y-1.5">
              <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                Expected move — room to run
              </h3>
              {!d.expected_move?.available ? (
                <p className="text-[0.65rem] text-text-muted">Expected move unavailable.</p>
              ) : (
                <>
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-sm font-bold tabular-nums">
                      ±{d.expected_move.expected_handles?.toFixed(0)}
                    </span>
                    <span className="text-[0.6rem] text-text-muted">
                      1σ · range ~{d.expected_move.expected_range?.toFixed(0)} handles
                    </span>
                  </div>
                  {d.expected_move.lower != null && d.expected_move.upper != null && (
                    <div className="text-[0.62rem] tabular-nums text-text-muted">
                      {fmtPx(d.expected_move.lower)} – {fmtPx(d.expected_move.upper)}
                    </div>
                  )}
                  {d.expected_move.consumed && (
                    <div className="space-y-0.5">
                      <div className="h-1.5 bg-surface-alt rounded overflow-hidden">
                        <div
                          className={`h-full ${
                            d.expected_move.consumed.pct >= 100 ? "bg-loss"
                              : d.expected_move.consumed.pct >= 75 ? "bg-amber-400" : "bg-gain"
                          }`}
                          style={{ width: `${Math.min(100, d.expected_move.consumed.pct)}%` }}
                        />
                      </div>
                      <div className="text-[0.6rem] text-text-muted tabular-nums">
                        {d.expected_move.consumed.pct.toFixed(0)}% of the expected range used
                        {" "}({d.expected_move.consumed.range.toFixed(0)} handles)
                      </div>
                    </div>
                  )}
                  <div className="space-y-0.5 pt-0.5">
                    {(d.expected_move.estimates ?? []).map((e, i) => (
                      <div key={i} className="flex items-baseline gap-1.5 text-[0.6rem]" title={e.detail}>
                        <span className="text-text-muted flex-1 min-w-0 truncate">{e.source}</span>
                        <span className="tabular-nums text-text">±{e.sigma_handles.toFixed(0)}</span>
                        {e.quote_source === "settled" && (
                          <span className="text-[0.5rem] uppercase text-text-muted/70" title="Market was closed — last settlement, not a live price.">
                            settled
                          </span>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* SESSION CHARACTER. Every estimate above is fixed at the
                      open and cannot move once an unscheduled event starts —
                      2026-08-03 ran 79 handles against a VIX1D-implied 54 with
                      nothing on the card able to say so mid-session. This one
                      is measured from the range actually delivered. It carries
                      its own out-of-sample error rather than a confidence word. */}
                  {d.regime?.path_implied?.available && (
                    <div className="pt-1.5 mt-1.5 border-t border-border space-y-0.5">
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                          Session character
                        </span>
                        <span
                          className={`text-[0.6rem] font-bold uppercase ${
                            d.regime.character === "wide" ? "text-loss"
                              : d.regime.character === "compressed" ? "text-accent" : "text-text"
                          }`}
                        >
                          {d.regime.character}
                        </span>
                        {d.regime.path_implied.multiplier != null && (
                          <span className="text-[0.6rem] tabular-nums text-text font-semibold">
                            {d.regime.path_implied.multiplier.toFixed(2)}× normal
                          </span>
                        )}
                      </div>
                      <div className="text-[0.6rem] text-text-muted tabular-nums">
                        {d.regime.path_implied.range_so_far?.toFixed(0)} handles in by{" "}
                        {d.regime.path_implied.slot} · a typical session has{" "}
                        {d.regime.path_implied.typical_pct_covered?.toFixed(0)}% of its range by
                        then · implies {d.regime.path_implied.implied_range?.toFixed(0)} for the
                        session against a normal {d.regime.path_implied.normal_range?.toFixed(0)}
                      </div>
                      <p className="text-[0.55rem] text-text-muted/80 leading-snug">
                        Measured from this session&apos;s own range, not priced at the open —
                        the estimates above cannot move once the day is underway. Median error
                        at this hour is {d.regime.path_implied.oos_mae_pct?.toFixed(0)}% out of
                        sample.
                      </p>
                    </div>
                  )}

                  {/* Dispersion speaks only before the first bucket closes —
                      the one window the path estimate cannot cover. Shown with
                      its own base rate attached because the lift is modest and
                      the sample is small; it never sets an expected range. */}
                  {d.regime?.dispersion?.available &&
                    !d.regime.path_implied?.available &&
                    (d.regime.dispersion.outliers?.length ?? 0) > 0 && (
                      <div className="pt-1.5 mt-1.5 border-t border-border space-y-0.5">
                        <div className="flex items-baseline gap-1.5">
                          <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                            Overnight dispersion
                          </span>
                          <span className="text-[0.6rem] font-bold uppercase text-text">
                            {d.regime.dispersion.band}
                          </span>
                        </div>
                        <div className="text-[0.6rem] text-text-muted">
                          {d.regime.dispersion.outliers?.slice(0, 3).map((o) => (
                            <span key={o.symbol} className="mr-2 tabular-nums">
                              {o.label} {o.z > 0 ? "+" : ""}
                              {o.z.toFixed(1)}σ
                            </span>
                          ))}
                        </div>
                        <p className="text-[0.55rem] text-text-muted/80 leading-snug">
                          {d.regime.dispersion.note} {d.regime.dispersion.caveat}
                        </p>
                      </div>
                    )}
                  {d.expected_move.vol_regime && (
                    <p className="text-[0.58rem] text-text-muted leading-snug" title={d.expected_move.vol_regime.note}>
                      IV {d.expected_move.vol_regime.implied.toFixed(1)} vs RV{" "}
                      {d.expected_move.vol_regime.realized.toFixed(1)} —{" "}
                      <span className="text-text">{d.expected_move.vol_regime.label}</span>
                    </p>
                  )}
                </>
              )}
            </div>

            {/* dealer gamma */}
            <div className="space-y-1.5">
              <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                Dealer gamma — SPX
              </h3>
              {!d.gamma?.available ? (
                <p className="text-[0.65rem] text-text-muted">
                  Gamma unavailable{d.gamma?.reason ? ` — ${d.gamma.reason}` : ""}.
                </p>
              ) : (
                <>
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[0.6rem] font-bold uppercase ${
                        d.gamma.regime === "short" ? "bg-loss/15 text-loss" : "bg-gain/15 text-gain"
                      }`}
                    >
                      {d.gamma.regime} gamma
                    </span>
                    {d.gamma.zero_dte_share != null && (
                      <span className="text-[0.58rem] text-text-muted" title="Share of total gamma expiring today. 0DTE gamma evaporates at the close.">
                        0DTE {d.gamma.zero_dte_share.toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <p className="text-[0.6rem] text-text leading-snug">{d.gamma.regime_note}</p>
                  {/* The ES levels below are SPX strikes plus a basis. While
                      cash is shut that basis is carried from the last close, so
                      say so — an undated conversion reads as live. */}
                  {d.gamma.es_basis != null && d.gamma.es_basis_is_live === false && (
                    <p className="text-[0.58rem] text-text-muted leading-snug">
                      SPX cash is shut
                      {d.gamma.spx_spot_asof
                        ? ` (last printed ${new Date(d.gamma.spx_spot_asof).toLocaleDateString("en-US", { month: "short", day: "numeric" })} at ${d.gamma.spx_spot?.toFixed(2)})`
                        : ""}
                      {d.gamma.spx_spot_effective != null && (
                        <> , so the book is read at <span className="text-text tabular-nums">
                          {d.gamma.spx_spot_effective.toFixed(2)}</span> — where ES is implying SPX
                          sits, on a {d.gamma.es_basis.toFixed(2)} basis carried from the last cash
                          close. Walls and the flip are relative to THAT, not to the frozen print</>
                      )}.
                    </p>
                  )}
                  <div className="space-y-0.5 text-[0.62rem] tabular-nums pt-0.5">
                    {d.gamma.call_wall_es != null && (
                      <div className="flex justify-between gap-2" title="Heaviest call gamma above spot — a magnet and a level price struggles through.">
                        <span className="text-text-muted">Call wall</span>
                        <span className="text-text">{fmtPx(d.gamma.call_wall_es)}</span>
                      </div>
                    )}
                    {d.gamma.flip_es != null && (
                      <div className="flex justify-between gap-2 font-semibold" title="Where aggregate dealer gamma crosses zero. Which side price is on decides whether hedging dampens or amplifies moves.">
                        <span className="text-accent">Gamma flip</span>
                        <span className="text-accent">{fmtPx(d.gamma.flip_es)}</span>
                      </div>
                    )}
                    {d.gamma.put_wall_es != null && (
                      <div className="flex justify-between gap-2" title="Heaviest put gamma below spot.">
                        <span className="text-text-muted">Put wall</span>
                        <span className="text-text">{fmtPx(d.gamma.put_wall_es)}</span>
                      </div>
                    )}
                  </div>
                  <p className="text-[0.55rem] text-text-muted leading-snug pt-0.5">
                    ES-converted from SPX using the {d.gamma.es_basis?.toFixed(2)} basis. Dealer
                    inventory is inferred from open interest, not observed — treat the flip level
                    and the shape as the signal, not the absolute number.
                  </p>
                </>
              )}
            </div>

            {/* session structure */}
            <div className="space-y-1.5">
              <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                Session structure
              </h3>
              {!d.intraday?.available ? (
                <p className="text-[0.65rem] text-text-muted">Structure unavailable.</p>
              ) : (
                <div className="space-y-1 text-[0.62rem]">
                  {d.intraday.day_type?.available && (
                    <div className="flex justify-between gap-2" title={d.intraday.day_type.note}>
                      <span className="text-text-muted">Day type</span>
                      <span className="text-text font-semibold capitalize">
                        {d.intraday.day_type.label}
                        {d.intraday.day_type.ib_multiple != null && (
                          <span className="ml-1 font-normal text-text-muted tabular-nums">
                            {d.intraday.day_type.ib_multiple.toFixed(2)}× IB
                          </span>
                        )}
                      </span>
                    </div>
                  )}
                  {d.intraday.opening_range?.ib && (
                    <div className="flex justify-between gap-2" title="First hour of the cash session — the day's reference frame.">
                      <span className="text-text-muted">Initial balance</span>
                      <span className="tabular-nums text-text">
                        {fmtPx(d.intraday.opening_range.ib.low)}–{fmtPx(d.intraday.opening_range.ib.high)}
                      </span>
                    </div>
                  )}
                  {d.intraday.opening_range?.or30 && (
                    <div className="flex justify-between gap-2" title="First 30 minutes.">
                      <span className="text-text-muted">Opening range 30m</span>
                      <span className="tabular-nums text-text">
                        {fmtPx(d.intraday.opening_range.or30.low)}–{fmtPx(d.intraday.opening_range.or30.high)}
                      </span>
                    </div>
                  )}
                  {d.intraday.relative_volume?.available && (
                    <div className="flex justify-between gap-2" title={d.intraday.relative_volume.note}>
                      <span className="text-text-muted">Participation</span>
                      <span className="text-text">
                        <span className="tabular-nums">{d.intraday.relative_volume.ratio?.toFixed(2)}×</span>{" "}
                        <span className="text-text-muted">{d.intraday.relative_volume.verdict}</span>
                      </span>
                    </div>
                  )}
                  {d.intraday.overnight_inventory?.available && (
                    <div className="flex justify-between gap-2" title={d.intraday.overnight_inventory.note}>
                      <span className="text-text-muted">ON inventory</span>
                      <span className="text-text capitalize">{d.intraday.overnight_inventory.skew}</span>
                    </div>
                  )}
                  {(d.intraday.naked_pocs ?? []).length > 0 && (
                    <div className="flex justify-between gap-2" title="Prior sessions' fairest prices that price hasn't returned to — they act as magnets.">
                      <span className="text-text-muted">Naked POC</span>
                      <span className="tabular-nums text-text">
                        {fmtPx(d.intraday.naked_pocs![0].value)}
                        <span className="text-text-muted ml-1">
                          ({fmtHandles(d.intraday.naked_pocs![0].distance)})
                        </span>
                      </span>
                    </div>
                  )}
                  {(d.intraday.unfilled_gaps ?? []).length > 0 && (
                    <div className="flex justify-between gap-2" title="Unfilled cash-session gap — price has not traded back through it.">
                      <span className="text-text-muted">Unfilled gap</span>
                      <span className="tabular-nums text-text">
                        {fmtPx(d.intraday.unfilled_gaps![0].from)}
                        <span className="text-text-muted ml-1">
                          ({fmtHandles(d.intraday.unfilled_gaps![0].distance)})
                        </span>
                      </span>
                    </div>
                  )}
                  {d.intraday.cross_asset?.available && (
                    <div className="pt-1 border-t border-border/40">
                      <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5">
                        Cross-asset
                      </div>
                      <div className="flex flex-wrap gap-x-2.5 gap-y-0.5">
                        {(d.intraday.cross_asset.rows ?? []).map((r) => (
                          <span key={r.symbol} className="whitespace-nowrap text-[0.6rem]" title={r.why}>
                            <span className="text-text-muted">{r.label.split(" ")[0]}</span>{" "}
                            <span className={`tabular-nums ${pctClass(r.change_pct)}`}>
                              {r.change_pct > 0 ? "+" : ""}{r.change_pct.toFixed(2)}%
                            </span>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          <CandleContextBlock d={d.candles} />

          {/* The Globex range against what the cash session has done with it.
              Computed and shipped in this payload since the study landed, but
              never rendered — and it is the strongest measured relationship in
              the cockpit: where the session OPENS inside the overnight range
              predicts which side breaks. */}
          <OvernightRead d={d.overnight} />

          {/* ── breadth: how many stocks are going with the index ── */}
          {d.breadth?.available && (
            <div className="border-t border-border pt-3 space-y-2">
              <div className="flex items-baseline justify-between gap-2 flex-wrap">
                <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  Breadth
                </h3>
                <span className="text-[0.55rem] text-text-muted" title={d.breadth.universe?.note}>
                  {d.breadth.universe?.n?.toLocaleString()}
                  {d.breadth.universe?.eligible_n
                    ? ` of ${d.breadth.universe.eligible_n.toLocaleString()}`
                    : ""}{" "}
                  names · {d.breadth.live ? "live" : `last session ${d.breadth.session ?? ""}`}
                </span>
              </div>

              {/* Fires before the cash open, when only a fraction of the universe
                  has printed and the counts are a thin sample. */}
              {d.breadth.live &&
                (d.breadth.universe?.eligible_n ?? 0) > 0 &&
                (d.breadth.universe?.n ?? 0) / (d.breadth.universe?.eligible_n ?? 1) < 0.8 && (
                  <p className="text-[0.6rem] text-amber-400 border-l-2 border-l-amber-400/50 pl-2">
                    {d.breadth.asof_note}
                  </p>
                )}

              {d.breadth.divergence && (
                <p
                  className={`text-[0.65rem] text-text border-l-2 pl-2 ${
                    d.breadth.divergence.label === "divergent"
                      ? "border-l-amber-400"
                      : "border-l-accent"
                  }`}
                >
                  {d.breadth.divergence.note}
                </p>
              )}

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 text-[0.6rem]">
                <div title="Advancing minus declining names, as a share of the universe. Self-normalising, so it is comparable across sessions.">
                  <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5">
                    Net advancers
                  </div>
                  <div
                    className={`text-[0.95rem] font-semibold tabular-nums ${
                      (d.breadth.net_advancers_pct ?? 0) >= 0 ? "text-gain" : "text-loss"
                    }`}
                  >
                    {(d.breadth.net_advancers_pct ?? 0) > 0 ? "+" : ""}
                    {d.breadth.net_advancers_pct?.toFixed(0)}%
                  </div>
                  <div className="text-[0.55rem] text-text-muted tabular-nums">
                    {d.breadth.advancers?.toLocaleString()} up ·{" "}
                    {d.breadth.decliners?.toLocaleString()} down
                  </div>
                </div>

                <div title="Advancing names divided by declining names.">
                  <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5">
                    A/D ratio
                  </div>
                  <div className="text-[0.95rem] font-semibold tabular-nums text-text">
                    {d.breadth.ad_ratio?.toFixed(2) ?? "—"}
                  </div>
                  <div className="text-[0.55rem] text-text-muted tabular-nums">
                    {d.breadth.up_volume_pct?.toFixed(0)}% of volume up
                  </div>
                </div>

                <div title={d.breadth.trin_band?.why}>
                  <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5">
                    TRIN
                  </div>
                  <div className="text-[0.95rem] font-semibold tabular-nums text-text">
                    {d.breadth.trin?.toFixed(2) ?? "—"}
                  </div>
                  <div className="text-[0.55rem] text-text-muted truncate">
                    {d.breadth.trin_band?.label ?? "—"}
                  </div>
                </div>

                {d.breadth.equal_vs_cap?.available && (
                  <div title={`${d.breadth.equal_vs_cap.note ?? ""}${
                    d.breadth.equal_vs_cap.source
                      ? ` Source: ${d.breadth.equal_vs_cap.source}.`
                      : ""
                  }`}>
                    <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5">
                      Equal vs cap
                    </div>
                    <div
                      className={`text-[0.95rem] font-semibold tabular-nums ${
                        (d.breadth.equal_vs_cap.spread_pct ?? 0) >= 0 ? "text-gain" : "text-loss"
                      }`}
                    >
                      {(d.breadth.equal_vs_cap.spread_pct ?? 0) > 0 ? "+" : ""}
                      {d.breadth.equal_vs_cap.spread_pct?.toFixed(2)}%
                    </div>
                    <div className="text-[0.55rem] text-text-muted truncate">
                      {d.breadth.equal_vs_cap.label} · RSP{" "}
                      {d.breadth.equal_vs_cap.equal_weight?.toFixed(2)}% vs SPY{" "}
                      {d.breadth.equal_vs_cap.cap_weight?.toFixed(2)}%
                    </div>
                  </div>
                )}
              </div>

              <p className="text-[0.52rem] text-text-muted leading-snug">
                {d.breadth.reconstruction}
                {d.breadth.tick && !d.breadth.tick.available && ` NYSE TICK is not shown: ${d.breadth.tick.reason}`}
              </p>
            </div>
          )}

          {/* ── measured base rates ── */}
          {d.base_rates?.available && (
            <div className="border-t border-border pt-3 space-y-1.5">
              <div className="flex items-baseline justify-between gap-2 flex-wrap">
                <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  Measured base rates
                </h3>
                {/* This header describes the DAILY study. The intraday path
                    rates below run on SPY 5-minute bars over a shorter window,
                    and the overnight study elsewhere on this card is ES futures
                    over two years — three instruments, three windows. Any one of
                    them unlabelled and a reader merges them into one number. */}
                <span className="text-[0.55rem] text-text-muted">
                  {d.base_rates.instrument ?? d.base_rates.source} ·{" "}
                  {d.base_rates.sessions?.toLocaleString()} sessions ·{" "}
                  {d.base_rates.from} to {d.base_rates.to}
                </span>
              </div>
              {d.base_rates.path?.available && d.base_rates.path.instrument && (
                <p className="text-[0.55rem] text-text-muted leading-snug">
                  Intraday path rates below are measured on{" "}
                  <span className="text-text">{d.base_rates.path.instrument}</span>,{" "}
                  {d.base_rates.path.sessions?.toLocaleString()} sessions
                  {d.base_rates.path.from ? ` (${d.base_rates.path.from} to ${d.base_rates.path.to})` : ""}
                  {d.base_rates.path.instrument_note ? ` — ${d.base_rates.path.instrument_note}` : "."}
                </p>
              )}

              {d.base_rates.gaps?.today && (
                <p className="text-[0.65rem] text-text border-l-2 border-l-accent pl-2">
                  {d.base_rates.gaps.today.note}
                </p>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-x-5 gap-y-2 text-[0.6rem]">
                <div>
                  <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5">
                    Gap fill, same session
                  </div>
                  {(d.base_rates.gaps?.buckets ?? []).map((b) => (
                    <div key={b.bucket} className="flex justify-between gap-2 tabular-nums">
                      <span className="text-text-muted truncate">{b.bucket}</span>
                      <span className="text-text">{b.fill_rate.toFixed(0)}%</span>
                    </div>
                  ))}
                </div>

                <div>
                  <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5">
                    Typical session
                  </div>
                  {d.base_rates.range?.available && (
                    <>
                      <div className="flex justify-between gap-2 tabular-nums">
                        <span className="text-text-muted">Median range</span>
                        <span className="text-text">{d.base_rates.range.median_range_handles?.toFixed(0)}</span>
                      </div>
                      <div className="flex justify-between gap-2 tabular-nums">
                        <span className="text-text-muted">90th pct</span>
                        <span className="text-text">{d.base_rates.range.p90_handles?.toFixed(0)}</span>
                      </div>
                      <div className="flex justify-between gap-2 tabular-nums" title="How often a session trades above the prior day's high.">
                        <span className="text-text-muted">Takes prior high</span>
                        <span className="text-text">{d.base_rates.range.took_prior_high_pct?.toFixed(0)}%</span>
                      </div>
                      <div className="flex justify-between gap-2 tabular-nums" title="How often a session trades below the prior day's low.">
                        <span className="text-text-muted">Takes prior low</span>
                        <span className="text-text">{d.base_rates.range.took_prior_low_pct?.toFixed(0)}%</span>
                      </div>
                      <div className="flex justify-between gap-2 tabular-nums" title="Most of the range is directional body rather than rotation.">
                        <span className="text-text-muted">Trend day</span>
                        <span className="text-text">{d.base_rates.range.trend_day_pct?.toFixed(0)}%</span>
                      </div>
                    </>
                  )}
                </div>

                <div>
                  <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5">
                    Release days vs normal
                  </div>
                  {(d.base_rates.events?.events ?? []).map((e) => (
                    <div key={e.name} className="flex justify-between gap-2 tabular-nums"
                         title={`n=${e.n}. Median session range ${e.median_range_pct.toFixed(2)}% vs a ${d.base_rates?.events?.baseline_range_pct?.toFixed(2)}% baseline.`}>
                      <span className="text-text-muted truncate">{e.name}</span>
                      <span className="text-text">{e.range_vs_normal?.toFixed(2)}×</span>
                    </div>
                  ))}
                  {d.base_rates.events?.note && (
                    <p className="text-[0.52rem] text-text-muted mt-1 leading-snug">
                      {d.base_rates.events.note}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ── intraday path: WHEN the session gets there ── */}
          {d.base_rates?.path?.available && (
            <div className="border-t border-border pt-3 space-y-2">
              <div className="flex items-baseline justify-between gap-2 flex-wrap">
                <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  Session path
                </h3>
                {/* Its own window — shorter than the daily study above, and
                    labelling it with those sessions would overstate it. */}
                <span className="text-[0.55rem] text-text-muted">
                  {d.base_rates.path.source} · {d.base_rates.path.sessions?.toLocaleString()} sessions ·{" "}
                  {d.base_rates.path.from} to {d.base_rates.path.to}
                </span>
              </div>

              {d.base_rates.path.live && (
                <p className="text-[0.65rem] text-text border-l-2 border-l-accent pl-2">
                  {d.base_rates.path.live.note}
                </p>
              )}

              {/* Hourly strip. Reads left to right as the session does. */}
              <div className="overflow-x-auto">
                <table className="w-full min-w-[26rem] text-[0.6rem] tabular-nums border-collapse">
                  <thead>
                    <tr className="text-text-muted">
                      <th className="text-left font-normal text-[0.55rem] uppercase tracking-wider pb-1">
                        By hour
                      </th>
                      {(d.base_rates.path.extremes ?? []).map((e) => (
                        <th
                          key={e.slot}
                          className={`text-right font-normal pb-1 px-1 ${
                            d.base_rates?.path?.live?.slot === e.slot ? "text-accent font-semibold" : ""
                          }`}
                          title={e.minutes === 30
                            ? "15:30–16:00 is a half-width bucket — its share understates the closing drive minute for minute."
                            : `${e.slot}–${String(Number(e.slot.slice(0, 2)) + 1).padStart(2, "0")}:30 ET, 60 minutes.`}
                        >
                          {e.slot}
                          {e.minutes === 30 && <span className="text-text-muted">*</span>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-t border-border" title="Share of sessions whose HIGH printed inside this hour.">
                      <td className="text-text-muted py-0.5">High prints here</td>
                      {(d.base_rates.path.extremes ?? []).map((e) => (
                        <td key={e.slot} className="text-right px-1 text-text">{e.high_pct.toFixed(0)}%</td>
                      ))}
                    </tr>
                    <tr className="border-t border-border" title="Share of sessions whose LOW printed inside this hour.">
                      <td className="text-text-muted py-0.5">Low prints here</td>
                      {(d.base_rates.path.extremes ?? []).map((e) => (
                        <td key={e.slot} className="text-right px-1 text-text">{e.low_pct.toFixed(0)}%</td>
                      ))}
                    </tr>
                    <tr className="border-t border-border" title="Median share of the full session range already covered by the end of this hour.">
                      <td className="text-text-muted py-0.5">Range covered</td>
                      {(d.base_rates.path.progress ?? []).map((p) => (
                        <td key={p.slot} className="text-right px-1 text-text">{p.range_complete_pct.toFixed(0)}%</td>
                      ))}
                    </tr>
                    <tr className="border-t border-border" title="Share of sessions where BOTH the high and the low are already in by the end of this hour — the day's range is settled.">
                      <td className="text-text-muted py-0.5">Both extremes in</td>
                      {(d.base_rates.path.progress ?? []).map((p) => (
                        <td key={p.slot} className="text-right px-1 text-text">{p.both_in_pct.toFixed(0)}%</td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-5 gap-y-2 text-[0.6rem]">
                {d.base_rates.path.initial_balance && (
                  <div>
                    <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5"
                         title={d.base_rates.path.initial_balance.definition}>
                      First hour (initial balance)
                    </div>
                    <div className="flex justify-between gap-2 tabular-nums">
                      <span className="text-text-muted">Holds the day&apos;s high</span>
                      <span className="text-text">{d.base_rates.path.initial_balance.held_high_of_day_pct.toFixed(0)}%</span>
                    </div>
                    <div className="flex justify-between gap-2 tabular-nums">
                      <span className="text-text-muted">Holds the day&apos;s low</span>
                      <span className="text-text">{d.base_rates.path.initial_balance.held_low_of_day_pct.toFixed(0)}%</span>
                    </div>
                    <div className="flex justify-between gap-2 tabular-nums" title="Price leaves the first hour's range on one side only — the clean, tradeable case.">
                      <span className="text-text-muted">Extends one side</span>
                      <span className="text-text">{d.base_rates.path.initial_balance.one_sided_pct.toFixed(0)}%</span>
                    </div>
                    <div className="flex justify-between gap-2 tabular-nums" title="Price leaves the first hour's range on BOTH sides — the whipsaw case.">
                      <span className="text-text-muted">Extends both sides</span>
                      <span className="text-text">{d.base_rates.path.initial_balance.both_sides_pct.toFixed(0)}%</span>
                    </div>
                    <div className="flex justify-between gap-2 tabular-nums" title="Median first-hour range as a share of the full session range.">
                      <span className="text-text-muted">Share of day range</span>
                      <span className="text-text">{d.base_rates.path.initial_balance.share_of_day_range_pct.toFixed(0)}%</span>
                    </div>
                  </div>
                )}

                {(d.base_rates.path.ib_breaks ?? []).length > 0 && (
                  <div>
                    <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-0.5"
                         title="A break is not one event. How far past the first hour's edge price travels changes what it is worth.">
                      IB break held into the close
                    </div>
                    {(d.base_rates.path.ib_breaks ?? []).map((b) => (
                      <div key={b.buffer_pct_of_ib} className="flex justify-between gap-2 tabular-nums"
                           title={`Up-breaks n=${b.up_n}, closed above the IB high ${b.up_held_pct.toFixed(0)}% of the time. Down-breaks n=${b.down_n}, held ${b.down_held_pct.toFixed(0)}%. Both sides broken on ${b.both_sides_pct.toFixed(0)}% of all sessions at this threshold.`}>
                        <span className="text-text-muted truncate">
                          {b.buffer_pct_of_ib === 0
                            ? "Any break"
                            : `${b.buffer_pct_of_ib}% of IB beyond`}
                        </span>
                        <span className="text-text">
                          {b.up_held_pct.toFixed(0)}%
                          <span className="text-text-muted"> · both {b.both_sides_pct.toFixed(0)}%</span>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {d.base_rates.path.initial_balance?.note && (
                <p className="text-[0.52rem] text-text-muted leading-snug">
                  {d.base_rates.path.initial_balance.note}
                  {d.base_rates.path.extremes?.some((e) => e.minutes === 30) &&
                    " * 15:30 covers 30 minutes, half the width of the other buckets."}
                </p>
              )}
            </div>
          )}

          <details className="group">
            <summary className="text-[0.62rem] text-text-muted hover:text-accent cursor-pointer select-none list-none flex items-center gap-1">
              <span className="transition-transform group-open:rotate-90">▸</span>
              How to read this
            </summary>
            <div className="text-[0.65rem] text-text-muted leading-relaxed mt-2 space-y-1.5 pl-3 border-l border-border">
              <p>
                <span className="text-text font-semibold">The read is derived, not written.</span> Every
                clause above comes from comparing the last price to a level in the ladder beside it, so each
                statement can be checked against the number it came from. It describes conditions — it is
                not an entry signal and deliberately quotes no stops or targets.
              </p>
              <p>
                <span className="text-text font-semibold">Location first.</span> Inside the value area,
                price rotates and mean-reverts; outside it, price is in discovery and trends. That one
                distinction does more to set expectations for the session than anything else on the card.
                VWAP is the most-watched bias line, but it is <em>acceptance</em> above or below it that
                matters, not a single poke through.
              </p>
              <p>
                <span className="text-text font-semibold">RTH and Globex are kept apart.</span> The prior
                session&apos;s high and low were made on full cash-session volume; the overnight range was
                not. Treating an overnight high as equivalent to a prior-day RTH high is the most common way
                to misread a break.
              </p>
              <p>
                <span className="text-text font-semibold">Horizons don&apos;t mix.</span> The levels and the
                clock are intraday. CTA flow and the macro scorecard run over days to weeks — they belong to
                the lean, and turning either into an intraday entry is a category error.
              </p>
              <p>
                <span className="text-text font-semibold">Expected move is two numbers, not one.</span>{" "}
                The ±figure is a one-sigma close-to-close move; the range beside it is the expected
                high-low and is about 1.6× larger. The progress bar measures the session&apos;s range
                against the <em>range</em>, because measuring a high-low against a close-to-close
                sigma overstates it by roughly 60% and would flag an ordinary wide day as a historic
                extension. An estimate tagged <span className="uppercase text-[0.55rem]">settled</span>{" "}
                was priced with the market shut and is not used as the headline.
              </p>
              <p>
                <span className="text-text font-semibold">Gamma decides which playbook applies.</span>{" "}
                Long gamma means dealer hedging leans against moves — breakouts struggle, rotation is
                the base case, fading extremes works. Short gamma inverts all of it. Which side of the
                flip level price sits on matters more than the size of the number. Dealer inventory is
                <em> inferred</em> from open interest under the standard convention, never observed, so
                the flip and the shape are the signal and the absolute total is an index, not a
                quantity of anything.
              </p>
              <p>
                <span className="text-text font-semibold">Base rates are priors, not forecasts.</span>{" "}
                Unconditional frequencies measured on the cash index, with n shown. They take no
                account of the regime you are in, and a 70% rate still loses three times in ten. They
                exist so a claim carries a number — and occasionally to contradict one: CPI sessions
                measure at ~1.0× a normal range despite their reputation.
              </p>
              <p>
                <span className="text-text font-semibold">Limits.</span> The volume profile bins each bar&apos;s
                volume at its typical price rather than distributing it across the bar&apos;s range, which
                needs tick data — the POC lands in the same place but value-area edges can differ by a bin.
                Rows marked <span className="uppercase text-[0.55rem]">est</span> have a release date derived
                from the usual scheduling rule rather than a published calendar, so they can slip a day.
              </p>
            </div>
          </details>

          {(d.degraded ?? []).length > 0 && (
            <div className="text-[0.55rem] text-text-muted border-t border-border pt-2">
              Unavailable this cycle: {(d.degraded ?? []).join(", ")} — the rest of the briefing is unaffected.
            </div>
          )}

        </>
      )}
    </div>
  );
}
