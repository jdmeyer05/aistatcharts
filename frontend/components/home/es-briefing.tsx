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

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchEsBrief,
  fetchEsCardAudit,
  fetchEsTrackRecord,
  fetchEsAnalogs,
  fetchEsGateTrackRecord,
  type EsBrief,
  type EsCardAudit,
  type EsTrackRecord,
  type EsGateTrackRecord,
  type EsAnalogs,
  type EsImpact,
  type EsLevel,
  type EsScheduleItem,
} from "@/lib/api";
import CandleContextBlock from "@/components/home/candle-context";
import OvernightRead from "@/components/home/overnight-read";
import { CardSection, SplitBar, Takeaway, fmtAgo, useMinuteClock } from "@/components/home/primitives";
import { ordinal } from "@/lib/home-constants";

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

// `fmtAgo` lives in primitives and takes the minute clock. Calling `Date.now()`
// during render bakes a RELATIVE age into HTML that `revalidate` then serves
// from cache, so the client always recomputes a different string — React #418
// on every load. See the shared implementation for the full reasoning.


// The wall clock lives in primitives now. This file had its own copy of the
// same external store — two intervals, two implementations, and the fix for the
// stuck server snapshot would have had to be made twice.

/** One measured figure with its label. Deliberately plain — these are readings,
 *  not verdicts, and styling them like alerts would imply a call the numbers do
 *  not make. */
function Stat({ label, value, tone = "neutral" }: {
  label: string;
  value: string;
  tone?: "gain" | "loss" | "neutral";
}) {
  const color = tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-text";
  return (
    <div className="border border-border/60 rounded px-2 py-1">
      <div className="text-[0.5rem] uppercase tracking-wider text-text-muted leading-tight">
        {label}
      </div>
      <div className={`text-sm font-bold font-data tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

function impactClass(i: EsImpact): string {
  if (i === "high") return "bg-loss/15 text-loss";
  if (i === "medium") return "bg-amber-500/15 text-amber-400";
  return "bg-surface-alt text-text-muted";
}

function fmtCap(c?: number): string | null {
  if (!c) return null;
  return c >= 1e12 ? `$${(c / 1e12).toFixed(2)}T` : `$${Math.round(c / 1e9).toLocaleString("en-US")}B`;
}

/** One row of the scheduled-risk list.
 *
 *  Macro and earnings share it because they answer the same question — what is
 *  on the clock — but they carry different uncertainty and it shows in the row.
 *  A macro date can be rule-derived and slip a day (`est`); an earnings DATE is
 *  published but its TIME is a convention, since "after the close" is a
 *  half-hour window and no company promises a minute (`~`). Those are different
 *  failure modes and conflating them into one caveat would understate both. */
function ScheduleRow({
  e,
  mins,
  hideCountdown = false,
}: {
  e: EsScheduleItem;
  mins: number;
  hideCountdown?: boolean;
}) {
  const done = mins <= 0;
  const cap = fmtCap(e.market_cap);
  return (
    <div
      className={`flex items-center gap-2 text-[0.65rem] ${done && !hideCountdown ? "opacity-50" : ""}`}
      title={e.note}
    >
      <span className="tabular-nums text-text-muted w-[3rem] shrink-0">
        {e.time_approx ? "~" : ""}
        {e.time_et}
      </span>
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
        {cap && (
          <span
            className="ml-1 text-[0.52rem] text-text-muted/70 tabular-nums"
            title="Market cap — the criterion this name was selected on. NOT an index weight: no constituent feed is available here, so this ranks the name rather than pricing what it contributes to the index."
          >
            {cap}
          </span>
        )}
        {e.affects === "this_session_gap" && (
          <span
            className="ml-1 text-[0.52rem] uppercase text-amber-400/80"
            title={e.affects_label}
          >
            gap
          </span>
        )}
      </span>
      {/* Same badge, two different derivations — worth saying, because a
          trader reading "high" beside PCE and "high" beside NVDA would
          reasonably assume they were measured the same way. An earnings badge
          is size; the event's actual priced cost is the premium below.

          AND THIS BADGE IS NOT A RANGE FORECAST. Its tooltip used to say
          "typical ES range expansion for this release", which is the exact
          conflation the measured chip beside it exists to end: on 3,677
          sessions CPI is `high` here and 1.06x there. This is a TIMING label —
          a release lands at a known minute — and it says so now. */}
      <span
        className={`px-1.5 py-0.5 rounded text-[0.52rem] font-bold uppercase shrink-0 ${impactClass(
          e.impact
        )}`}
        title={
          e.kind === "earnings"
            ? "Ranked by market cap, not by measured range expansion — unlike the macro rows. What the event actually costs is the priced premium below."
            : "How much of a scheduled discontinuity this is — whether it lands at a known minute and deserves attention on the clock. Not a range forecast: the measured multiplier beside it is that, and the two routinely disagree."
        }
      >
        {e.impact}
      </span>
      {/* The measured half. Only ever rendered for macro rows: single-name
          earnings were not in the event study, and quoting a macro multiplier
          next to NVDA would be inventing one. */}
      {e.kind !== "earnings" && (
        e.measured ? (
          <span
            className={`px-1 py-0.5 rounded text-[0.52rem] font-bold tabular-nums shrink-0 ${
              e.measured.band === "established" ? "bg-loss/15 text-loss"
                : e.measured.band === "unconfirmed" ? "bg-amber-500/15 text-amber-400"
                  : "bg-surface-alt text-text-muted"
            }`}
            title={
              `${e.measured.headline}` +
              ` 95% CI [${e.measured.ci95[0].toFixed(2)}, ${e.measured.ci95[1].toFixed(2)}].` +
              ` Next session ${e.measured.next_session.toFixed(2)}x — nothing carries past the print.` +
              (e.measured.caveat ? ` ${e.measured.caveat}` : "")
            }
          >
            {e.measured.multiplier.toFixed(2)}×
          </span>
        ) : (
          <span
            className="px-1 py-0.5 rounded text-[0.52rem] bg-surface-alt text-text-muted/60 shrink-0"
            title="Never in the event study's universe, so no measurement exists either way — which is a different statement from 'measured, and ordinary'."
          >
            —
          </span>
        )
      )}
      {!hideCountdown && (
        <span className="tabular-nums text-text-muted w-[4.5rem] text-right shrink-0">
          {done ? "released" : fmtCountdown(mins)}
        </span>
      )}
    </div>
  );
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

  /* 3. The clock. An after-the-bell report is on the schedule but is not risk to
     the session in front of you, so it is excluded from the countdown and gets
     its own sentence — pointing "next on the clock" at 16:15 would tell a trader
     to brace for something that cannot touch their range. */
  let clock: string | null = null;
  const intradaySched = (d.schedule ?? []).filter((e) => e.affects !== "next_session_gap");
  const upcoming = intradaySched.filter((e) => liveMins(e) > 0);
  const next = upcoming.length
    ? upcoming.reduce((a, b) => (liveMins(a) <= liveMins(b) ? a : b))
    : null;

  const afterClose = d.after_close ?? [];
  const prem = d.event_premium;
  const heldOvernight = afterClose.length
    ? ` After the bell: ${afterClose.map((e) => e.name).join(", ")}` +
      (prem?.available && prem.vs_session != null
        ? `. SPX prices the overnight that contains ${afterClose.length > 1 ? "them" : "it"} at ` +
          `${prem.vs_session.toFixed(2)}x an ordinary session (${prem.segment_handles} handles)` +
          `${prem.quote_source === "settled" ? ", off settled quotes" : ""} — that is the cost of ` +
          `carrying a position through, not a reason to change today's range.`
        : prem?.available
          ? `. SPX prices that overnight at ${prem.segment_handles} handles — the cost of ` +
            `carrying through, not a reason to change today's range.`
          : ` — gap risk for the next session, not this one's range.`)
    : "";

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
          : "") +
      heldOvernight;
  } else if (intradaySched.length) {
    clock =
      "Everything scheduled has already printed; the rest of the session is left to positioning and the levels." +
      heldOvernight;
  } else if (afterClose.length) {
    clock = `Nothing timed inside this session.${heldOvernight}`;
  } else {
    clock = "Nothing on the calendar for this session — no timed catalyst to trade around.";
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

  // Separate and slower on purpose: the auditor reads the FINISHED brief, so
  // it cannot be assembled alongside it, and a model call has no business on
  // the card's critical path.
  const auditQ = useQuery<EsCardAudit>({
    queryKey: ["es-card-audit"],
    queryFn: fetchEsCardAudit,
    refetchInterval: 10 * 60_000,
    staleTime: 9 * 60_000,
  });

  // Describes the MODULE rather than today, so it moves once a day at most and
  // is fetched on its own long cadence.
  const trackQ = useQuery<EsTrackRecord>({
    queryKey: ["es-track-record"],
    queryFn: fetchEsTrackRecord,
    staleTime: 6 * 60 * 60_000,
    refetchInterval: false,
  });

  // Lazy, like the track record: the match is a statement about history and
  // changes once a session, so it must never gate the brief on the first paint.
  const analogQ = useQuery<EsAnalogs>({
    queryKey: ["es-analogs"],
    queryFn: fetchEsAnalogs,
    staleTime: 60 * 60_000,
    refetchInterval: false,
  });

  // The conditions gate's own record. Lazy and long-lived for the same reason
  // as the two above — it is a statement about accumulated sessions, it moves
  // at most once a day, and it must never sit on the brief's critical path.
  //
  // It is fetched even while it is still refusing to report: the refusal
  // carries the session count, and rendering that is the point (see the block
  // under the gate).
  const gateQ = useQuery<EsGateTrackRecord>({
    queryKey: ["es-track-record-gate"],
    queryFn: fetchEsGateTrackRecord,
    staleTime: 6 * 60 * 60_000,
    refetchInterval: false,
  });

  const d = q.data;

  const nowMin = useMinuteClock();

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

  // SESSION PATH, IN ONE SENTENCE. The table answers WHEN, and the only row a
  // live reader needs is how much range is typically still ahead from this
  // hour — which is exactly the row that is hardest to find in a 7-column grid.
  //
  // `live` is present ONLY during a cash session and is null on weekends,
  // holidays and outside 09:30-16:00, because "30% of the range is still to
  // come" is a lie about a day that is not trading. The takeaway respects that
  // rather than falling back to the unconditional table and calling it now.
  const pathRead = useMemo(() => {
    const p = d?.base_rates?.path;
    if (!p?.available) return null;
    const live = p.live;
    const ib = p.initial_balance;

    if (live) {
      const left = 100 - live.range_complete_pct;
      return {
        tone: "neutral" as const,
        headline:
          `By ${live.slot} a typical session has made ${live.range_complete_pct.toFixed(0)}% of its ` +
          `range, so about ${left.toFixed(0)}% is usually still ahead — and the high is already in ` +
          `${live.high_in_pct.toFixed(0)}% of the time, the low ${live.low_in_pct.toFixed(0)}%.`,
        detail:
          `Measured on ${p.instrument ?? p.source} over ${p.sessions?.toLocaleString("en-US")} sessions. ` +
          `These say WHEN a session tends to get where it is going, never where — nothing in this ` +
          `table carries direction. The 15:30 bucket is half the width of the others, so its share ` +
          `of the extremes understates the closing drive minute for minute.`,
      };
    }

    return {
      tone: "neutral" as const,
      headline: ib
        ? `The first hour has made ${ib.share_of_day_range_pct.toFixed(0)}% of a typical session's ` +
          `whole range, and one side of it broke on ${ib.one_sided_pct.toFixed(0)}% of sessions ` +
          `against ${ib.both_sides_pct.toFixed(0)}% that broke both.`
        : `Hourly path rates over ${p.sessions?.toLocaleString("en-US")} sessions of ${p.instrument ?? p.source}.`,
      detail:
        `No live reading — this is outside a cash session, and "x% of the range is still to come" ` +
        `describes a day that is trading. The table stands as an unconditional prior; it says when ` +
        `a session tends to get where it is going, never where.`,
    };
  }, [d?.base_rates?.path]);

  // BASE RATES, IN ONE SENTENCE — and the one number here that is routinely
  // misread. The unconditional gap-fill rate describes a session BEFORE it
  // opens; the live one describes what is left of it, and by late morning the
  // two diverge by thirty points or more. Quoting the first as though it were
  // the second is the specific error this study was run to stop, so the
  // takeaway leads with the live figure whenever there is one.
  const baseRatesRead = useMemo(() => {
    const br = d?.base_rates;
    if (!br?.available) return null;
    const live = br.gap_fill_live?.available ? br.gap_fill_live : null;
    const rng = br.range;

    if (live && typeof live.fill_rate === "number") {
      const uncond = live.unconditional;
      const gap = typeof uncond === "number" ? live.fill_rate - uncond : null;
      return {
        tone: "neutral" as const,
        headline:
          live.state === "filled"
            ? `Today's gap has already filled — the rates below describe sessions that still had one open.`
            : `Still open at ${live.as_of ?? "now"}, this gap has closed ${live.fill_rate.toFixed(0)}% of the time from here (n=${live.n}), conditioned on ${live.conditioned_on ?? "the clock"}.`,
        detail:
          (gap != null
            ? `The unconditional whole-session rate is ${uncond!.toFixed(0)}% — ` +
              `${Math.abs(gap).toFixed(0)} percentage points ${gap < 0 ? "higher" : "lower"} than what is ` +
              `left from here. They are different questions and the gap between them widens through ` +
              `the morning; the live figure is the one that applies to a decision taken now. `
            : "") +
          `Measured on ${live.instrument ?? br.instrument ?? "SPY"} over ${live.sessions?.toLocaleString("en-US") ?? br.sessions?.toLocaleString("en-US")} sessions. ` +
          `Direction was tested on both windows and came back null, so this says whether, not which way.`,
      };
    }

    if (rng?.available) {
      return {
        tone: "neutral" as const,
        headline:
          `A typical session on this study runs ${rng.median_range_pct?.toFixed(2)}%` +
          (rng.median_range_handles != null ? ` (${rng.median_range_handles.toFixed(0)} handles)` : "") +
          `, took the prior high ${rng.took_prior_high_pct?.toFixed(0)}% of the time and the prior low ` +
          `${rng.took_prior_low_pct?.toFixed(0)}%.`,
        detail:
          `These are unconditional priors over ${br.sessions?.toLocaleString("en-US")} sessions of ` +
          `${br.instrument ?? br.source}, not forecasts for today — they are the number a claim has ` +
          `to beat, not a claim of their own. Three studies appear on this card on three different ` +
          `instruments and windows; each is labelled where it is used, and they must not be merged.`,
      };
    }
    return null;
  }, [d?.base_rates]);

  // BREADTH, IN ONE SENTENCE. Four stat tiles that each answer "how many stocks
  // are going with the index" in a slightly different unit, and the thing worth
  // knowing is whether they AGREE with the index — which the payload computes
  // as `divergence` and rendered as a note the eye slides past.
  const breadthRead = useMemo(() => {
    const b = d?.breadth;
    if (!b?.available) return null;
    const net = b.net_advancers_pct;
    const divergent = b.divergence?.label === "divergent";
    const cov =
      b.universe?.eligible_n && b.universe?.n
        ? b.universe.n / b.universe.eligible_n
        : null;
    const thinCoverage = b.live === true && cov != null && cov < 0.8;

    return {
      tone: (divergent || thinCoverage ? "warn" : "neutral") as "warn" | "neutral",
      headline:
        (typeof net === "number"
          ? `Net advancers ${net > 0 ? "+" : ""}${net.toFixed(0)}% of ${b.universe?.n?.toLocaleString("en-US") ?? "the"} names`
          : "Breadth") +
        (divergent
          ? " — and breadth is DIVERGING from the index, so the move is being carried by fewer names than the tape suggests."
          /* Name the direction. "Breadth is going with the index, so the move is
             broad" printed beside net advancers of −35% reads as a bullish
             sentence about a session where four names in five went down; the
             claim was right and the wording hid which way. */
          : typeof net === "number"
            ? ` — breadth confirms rather than diverges: the ${net >= 0 ? "advance" : "decline"} is broad, not carried by a handful of names.`
            : " — breadth confirms the index rather than diverging from it."),
      detail:
        (thinCoverage
          ? `Only ${(cov! * 100).toFixed(0)}% of the eligible universe has printed, so these counts are a thin sample of the session rather than a picture of it. `
          : "") +
        (b.trin_band?.label
          ? `TRIN ${b.trin?.toFixed(2)} is ${b.trin_band.label}. `
          : "") +
        /* The trend rows answer a different question from everything above, so
           the takeaway has to name which one it just answered. An index can
           close green on a day when most of its names sit below their 200-day. */
        (b.trend?.available && b.trend.pct_above_200dma != null
          ? `Separately from today: ${b.trend.pct_above_200dma.toFixed(0)}% of the ` +
            `${b.trend.universe?.n?.toLocaleString("en-US")} names are above their own 200-day and ` +
            `${b.trend.pct_above_50dma?.toFixed(0)}% above their 50-day — a statement about the trend ` +
            `the market is in, not about this session. ` +
            (b.trend.history?.pct_above_200dma?.pctile == null
              ? `No reference set for those yet (${b.trend.history?.pct_above_200dma?.n_history ?? 0} of 60 sessions recorded), so they are levels rather than readings. `
              : "")
          : "") +
        `Reconstructed from a grouped-daily endpoint, not a consolidated tape feed — the levels are ` +
        `comparable to themselves across sessions, not to a vendor's TRIN or A/D print.`,
    };
  }, [d?.breadth]);

  // SESSION STRUCTURE, IN ONE SENTENCE. Seven label/value rows and no reading.
  // Participation is the one that changes how the others should be taken —
  // under about 0.8x relative volume, breaks fail more often — and it was a
  // row of equal weight beside the naked POC.
  const structureRead = useMemo(() => {
    const it = d?.intraday;
    if (!it?.available) return null;
    const rv = it.relative_volume?.available ? it.relative_volume.ratio : null;
    const dayType = it.day_type?.available ? it.day_type.label : null;
    const inv = it.overnight_inventory?.available ? it.overnight_inventory.skew : null;
    const magnets: string[] = [];
    if ((it.naked_pocs ?? []).length > 0) {
      magnets.push(`a naked POC ${fmtHandles(it.naked_pocs![0].distance)} away`);
    }
    if ((it.unfilled_gaps ?? []).length > 0) {
      magnets.push(`an unfilled gap ${fmtHandles(it.unfilled_gaps![0].distance)} away`);
    }

    const thin = typeof rv === "number" && rv < 0.8;
    return {
      tone: (thin ? "warn" : "neutral") as "warn" | "neutral",
      headline:
        (dayType ? `A ${dayType} day so far` : "Structure so far") +
        (typeof rv === "number"
          ? ` on ${rv.toFixed(2)}× normal participation${thin ? " — thin, and breaks fail more often on thin volume" : ""}`
          : "") +
        (magnets.length > 0 ? `, with ${magnets.join(" and ")}.` : "."),
      detail:
        (inv
          ? `Overnight inventory is ${inv}; a lopsided Globex book often corrects in the first hour, which is a different thing from a trend. `
          : "") +
        `The initial balance is the frame the rest of the session is measured against, not a level ` +
        `in itself. Magnets describe where price has unfinished business, and say nothing about ` +
        `when or whether it goes there.`,
    };
  }, [d?.intraday]);

  // DEALER GAMMA, IN ONE SENTENCE. The regime decides which playbook is
  // correct and outranks nearly everything else on this card, but it was
  // rendered as a chip and three price rows — leaving the reader to work out
  // that the distance to the flip is what says how close the regime is to
  // inverting. That distance is the number, and it was never stated.
  const gammaRead = useMemo(() => {
    const g = d?.gamma;
    if (!g?.available) return null;
    const short = g.regime === "short";
    const dist = g.distance_to_flip;
    const em = d?.expected_move?.expected_range;
    const flipShare =
      typeof dist === "number" && typeof em === "number" && em > 0
        ? Math.abs(dist) / em
        : null;

    const proximity =
      typeof dist === "number"
        ? flipShare != null
          ? `The flip is ${Math.abs(dist).toFixed(0)} handles ${g.above_flip ? "below" : "above"} — ` +
            `${(flipShare * 100).toFixed(0)}% of the session's priced range, so ` +
            `${flipShare < 0.35 ? "an ordinary session can carry price through it and swap the playbook" : "reaching it would take an unusually large move"}.`
          : `The flip is ${Math.abs(dist).toFixed(0)} handles ${g.above_flip ? "below" : "above"}.`
        : "";

    return {
      tone: (short ? "warn" : "neutral") as "warn" | "neutral",
      headline:
        (short
          ? "Dealers are short gamma: hedging goes WITH the move, so trends extend and fading extremes is the expensive mistake. "
          : "Dealers are long gamma: hedging leans AGAINST moves, so breakouts fail more often and rotation is the base case. ") +
        proximity,
      detail:
        (g.zero_dte_share != null
          ? `${g.zero_dte_share.toFixed(0)}% of that gamma expires today and evaporates at the close, so the regime is less durable than the number looks. `
          : "") +
        `Dealer inventory is INFERRED from open interest under a standard convention, never ` +
        `observed — the flip level and the shape carry the information, the absolute total does not.`,
    };
  }, [d?.gamma, d?.expected_move?.expected_range]);

  // EXPECTED MOVE, IN ONE SENTENCE. The block carries a sigma, an expected
  // RANGE about 1.6x larger, a consumed percentage measured against the range,
  // a vol regime and up to three estimates with different quote sources. Every
  // one of those is a place to confuse two numbers — and the specific confusion
  // that costs money is reading the close-to-close sigma as the high-low range,
  // which understates the room by 60%.
  const emRead = useMemo(() => {
    const em = d?.expected_move;
    if (!em?.available) return null;
    const consumed = em.consumed?.pct;
    const settled = (em.estimates ?? []).some((x) => x.quote_source === "settled");
    const vr = em.vol_regime;

    const left =
      typeof consumed === "number"
        ? consumed >= 100
          ? `The session has already covered ${consumed.toFixed(0)}% of the range priced for it.`
          : `${(100 - consumed).toFixed(0)}% of the priced range is still unspent.`
        : "No range has been consumed yet.";

    return {
      tone: (typeof consumed === "number" && consumed >= 100 ? "warn" : "neutral") as
        "warn" | "neutral",
      headline:
        `Options price a ${em.expected_range?.toFixed(0) ?? "—"}-handle high-to-low range for this ` +
        `session (${em.expected_handles?.toFixed(0) ?? "—"} handles one sigma close-to-close). ${left}`,
      detail:
        `The two numbers are not interchangeable — the expected RANGE runs about 1.6× the sigma, ` +
        `and the consumed figure is measured against the range, not against the sigma. ` +
        (vr
          ? `Implied ${vr.implied.toFixed(1)} against realised ${vr.realized.toFixed(1)} is ${vr.label}: ` +
            `${vr.ratio >= 1 ? "the market is charging more than has recently been delivered" : "the market is charging less than has recently been delivered"}. `
          : "") +
        (settled
          ? `At least one estimate was priced off settlements with the book shut, so treat the level ` +
            `as indicative and the ratio as the sturdier number. `
          : "") +
        `A range is a magnitude and carries no direction.`,
    };
  }, [d?.expected_move]);

  // THE LADDER'S TAKEAWAY. A twelve-row price ladder is a reference, not a
  // reading: it says where everything is and nothing about which one is in
  // play. `reach` and `pct_of_expected_range` are computed per level and were
  // only ever exposed on hover, so the one fact that decides whether the ladder
  // matters today — how many of these levels price can plausibly get to — was
  // invisible unless you moused over each row.
  const ladderRead = useMemo(() => {
    if (!lv?.available || !lv.levels?.length) return null;
    const levels = lv.levels;
    const reachable = levels.filter((l) => l.reach && l.reach !== "beyond a typical session");
    const nearest = lv.nearest;
    const withReach = levels.filter((l) => l.reach);
    return {
      headline: nearest
        ? `Nearest reference is ${nearest.label} at ${fmtPx(nearest.value)}, ${fmtHandles(nearest.distance)} away — the first level price has to resolve.`
        : `${levels.length} reference levels on the ladder, and no nearest could be identified.`,
      detail:
        /* The zero case had to be written out. "12 of 12 sit inside a typical
           session's travel; the other 0 are dimmed" is what a template reads
           like when nobody checked the boundary — and the all-out-of-reach case
           is the one actually worth saying plainly. */
        (withReach.length === 0
          ? ""
          : reachable.length === levels.length
            ? `Every one of the ${levels.length} sits inside a typical session's travel. `
            : reachable.length === 0
              ? `NONE of the ${levels.length} sits inside a typical session's travel — every level on ` +
                `this ladder is further away than a whole expected session's range, which is why they ` +
                `are all dimmed. `
              : `${reachable.length} of ${levels.length} sit inside a typical session's travel; the ` +
                `other ${levels.length - reachable.length} are dimmed because planning a day around ` +
                `them is the mistake that shading exists to prevent. `) +
        `Distances are signed from the last price. Overnight levels are made on thin Globex volume ` +
        `and break more easily than RTH levels made on size, so equal distance does not mean equal ` +
        `resistance.`,
    };
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

  // Split by whether the event can touch THIS session's range. An after-the-bell
  // report belongs on the card but not in the countdown: it is drawn below, in
  // its own block, because it sizes the cost of holding rather than the range.
  const intradaySched = useMemo(
    () => (d?.schedule ?? []).filter((e) => e.affects !== "next_session_gap"),
    [d?.schedule]
  );
  const afterClose = useMemo(() => d?.after_close ?? [], [d?.after_close]);
  const prem = d?.event_premium;

  const upcoming = useMemo(
    () => intradaySched.filter((e) => liveMins(e) > 0).sort((a, b) => liveMins(a) - liveMins(b)),
    [intradaySched, liveMins]
  );

  // SCHEDULED RISK, ON BOTH AXES. The rows carry an `impact` word and a
  // countdown, and a reader sizing a session off "high impact" is answering the
  // sizing question with a timing label. Measured over 3,677 sessions the two
  // routinely disagree — CPI is a scheduled discontinuity AND runs 1.06x, 12th
  // of 23 — so this states which of today's prints has measured expansion
  // behind it and, when none does, says that in numbers rather than by
  // rendering nothing.
  const scheduleRead = useMemo(() => {
    // AN EMPTY CALENDAR IS A READING, not a reason to render nothing. This
    // returned null when nothing was scheduled, so the section said "Nothing
    // timed inside next session" and stopped — the one sentence a reader can
    // actually act on (there is no timed catalyst, so nothing here argues for
    // holding size back for a specific minute) never appeared. That is an
    // absence rendered as a calm, in the block whose job is scheduled risk.
    if (intradaySched.length === 0) {
      return {
        tone: "neutral" as const,
        headline: `Nothing is timed inside ${scheduleScope.label} — no release to plan an entry or a pause around.`,
        detail:
          (afterClose.length > 0
            ? `${afterClose.length} report${afterClose.length === 1 ? " lands" : "s land"} after the bell, which sizes the cost of carrying a position overnight rather than the range of the session in front of you. `
            : "") +
          `A quiet calendar is not the same as a quiet session: the measured base rates below describe ` +
          `sessions without a catalyst too, and they are the number any claim about today has to beat.`,
      };
    }
    const measured = intradaySched.filter((e) => e.measured != null);
    const established = measured.filter((e) => e.measured!.survives_fdr);
    const widest = [...measured].sort(
      (a, b) => b.measured!.multiplier - a.measured!.multiplier
    )[0];
    const timed = intradaySched.filter((e) => e.impact === "high");
    const next = upcoming[0];
    const mins = next ? liveMins(next) : null;

    const clock = next
      ? `Next on the clock is ${next.name}${mins != null ? ` in ${fmtCountdown(mins)}` : ""}. `
      : "Everything scheduled for this session has printed. ";

    if (established.length > 0) {
      const e = established[0];
      return {
        tone: "warn" as const,
        headline:
          `${clock}${e.name} is the one print today whose range expansion is established — ` +
          `${e.measured!.multiplier.toFixed(2)}× a normal session over ${e.measured!.n} prints, and the ` +
          `only event of ${e.measured!.of} tested that survives the correction.`,
        detail:
          `Magnitude only: the study measured |move| and carries no direction. ` +
          `${(e.measured!.share_over_1_5x * 100).toFixed(0)}% of these prints ran past 1.5×, and the ` +
          `next session came back to ${e.measured!.next_session.toFixed(2)}× — nothing carries past the print.`,
      };
    }

    return {
      tone: "neutral" as const,
      headline:
        `${clock}Nothing scheduled for this session has established range expansion behind it.` +
        (widest
          ? ` The widest measured is ${widest.name} at ${widest.measured!.multiplier.toFixed(2)}× a normal session (${ordinal(widest.measured!.rank)} of ${widest.measured!.of}), which does not survive correction.`
          : ""),
      detail:
        `${timed.length} of ${intradaySched.length} are flagged as scheduled discontinuities — worth ` +
        `being at the screen for, which is a different claim from a wider day. ` +
        `${measured.length} of ${intradaySched.length} carry any measurement at all; the rest are ` +
        `single-name earnings or releases that were never in the study's universe, and an absent ` +
        `measurement is not a small one.`,
    };
  }, [intradaySched, upcoming, liveMins, scheduleScope, afterClose]);

  // With no session running the card has far less to say, and it used to say it
  // at full size anyway — rendering "From here to the close" during the 17:00
  // maintenance break, four condition chips of which three scored 0, and a
  // caveat paragraph under every block. Density should track how much is
  // actually known.
  // Phases are rth_open / rth_midday / rth_close / premarket / overnight /
  // closed / weekend. Test for RTH POSITIVELY: `phase === "closed"` reads as
  // covering it but silently misses `weekend`, which would have put "From here
  // to the close" back on the card every Saturday and Sunday — the same bug
  // this guard exists to fix, just on the days nobody was looking.
  const inRth = (session?.phase ?? "").startsWith("rth");

  // The path-implied estimator divides the range so far by the fraction of its
  // range a typical session has covered by now. At 100% covered that divisor is
  // 1.0 and the "estimate" IS the delivered range — the session is a result, not
  // a forecast. Tested on the data rather than on the clock, because the two can
  // disagree: an early close covers its path before the phase flips.
  const sessionComplete =
    (d?.regime?.path_implied?.typical_pct_covered ?? 0) >= 95 || !inRth;

  // The shared epistemics collect into one disclosure instead of repeating
  // under each block. Caveats specific to TODAY's numbers (the `surface`
  // reasons on the gate, the unattributed-move note) stay inline where they
  // are read — those are findings, not standing methodology.
  //
  // Each entry is gated on the SAME condition as the block it explains, so this
  // never describes a block the reader cannot see. Explaining absent content is
  // the failure this whole pass is about.
  const howToRead = useMemo(() => {
    const out: { label: string; text: string }[] = [];
    const add = (label: string, text?: string | null) => {
      if (text) out.push({ label, text });
    };
    if (d?.conditions?.available) add("Conditions", d.conditions.disclaimer);
    if (inRth && d?.rest_of_session?.available) {
      add("From here to the close", d.rest_of_session.caveat);
    }
    if (d?.level_clusters?.available && (d.level_clusters.n_cross_method ?? 0) > 0) {
      add("Reference levels", d.level_clusters.caveat);
    }
    if (d?.attribution?.available && (d.attribution.moves?.length ?? 0) > 0) {
      add("What moved the tape", d.attribution.caveat);
    }
    return out;
  }, [d, inRth]);

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
            {/* The label sits INSIDE the conditional, not outside it. `fmtAgo`
                returns "" until the clock has a value, so `bars ${...}` would
                render a bare "bars" for the first frames — a label with nothing
                to label. */}
            {(() => {
              const age = lv?.asof ? fmtAgo(lv.asof, nowMin) : fmtAgo(d?.asof, nowMin);
              if (!age) return "";
              return lv?.asof ? `bars ${age}` : age;
            })()}
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
                {/* Not repeated when it only echoes the phase badge. On a shut
                    market "MARKET CLOSED" appeared three times in the first
                    screen — the badge beside the price, this verdict, and the
                    subtitle's "ES is closed for the weekend". Three restatements
                    of one fact crowd out the part that is actually specific to
                    the state, which is the note beside it. */}
                {d.conditions.verdict.toLowerCase() !== (session?.label ?? "").toLowerCase() && (
                  <span className="text-xs font-bold uppercase tracking-wide">
                    {d.conditions.verdict}
                  </span>
                )}
                {d.conditions.score != null && (
                  <span className="text-[0.6rem] tabular-nums text-text-muted">
                    score {d.conditions.score > 0 ? "+" : ""}{d.conditions.score}
                  </span>
                )}
                <span className="text-[0.65rem] text-text">{d.conditions.note}</span>
              </div>
              {/* A factor that scored 0 changed nothing about the verdict, and
                  four chips reading "-1 / 0 / 0 / 0" spend four slots to say
                  one thing. The scored factors keep their chip; the rest
                  collapse to a count that still names them on hover, so the
                  abstentions remain auditable without competing for the eye. */}
              {d.conditions.reasons.length > 0 && (() => {
                const scored = d.conditions.reasons.filter((r) => r.effect !== 0);
                const abstained = d.conditions.reasons.filter((r) => r.effect === 0);
                return (
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mt-1.5">
                    {scored.map((r, i) => (
                      <span key={i} className="text-[0.6rem] whitespace-nowrap" title={r.why}>
                        <span
                          className={`tabular-nums font-semibold ${
                            r.effect > 0 ? "text-gain" : "text-loss"
                          }`}
                        >
                          {r.effect > 0 ? "+" : ""}{r.effect}
                        </span>{" "}
                        <span className="text-text-muted">{r.factor}</span>
                      </span>
                    ))}
                    {abstained.length > 0 && (
                      <span
                        className="text-[0.6rem] text-text-muted/70 whitespace-nowrap"
                        title={abstained.map((r) => `${r.factor} — ${r.why}`).join("\n\n")}
                      >
                        {abstained.length} abstained
                      </span>
                    )}
                  </div>
                );
              })()}
              {/* An abstention that moved the verdict is readable on the card.
                  A factor scoring 0 because two estimators disagree is a caveat,
                  and caveats belong in text where they are read, not in a title
                  attribute that a trader never hovers. */}
              {d.conditions.reasons.filter((r) => r.surface).map((r, i) => (
                <p key={i} className="text-[0.6rem] text-text-muted mt-1 leading-snug">
                  {r.why}
                </p>
              ))}

              {/* HAS THIS GATE EVER BEEN RIGHT.
                  The gate leads this card on the argument that standing aside
                  saves the most money, and it was the one module on the page
                  with no visible score at all. A record has been accumulating
                  behind /es-track-record-gate since 2026-08-03 and nothing in
                  the frontend ever called it.

                  The REFUSING state is rendered on purpose. Below 30 completed
                  sessions the endpoint declines to quote a number, and that
                  refusal — carrying the count — is far more informative than
                  silence: silence reads as "scored, unremarkable", which is
                  exactly what an unscored module gets assumed to be. It also
                  puts the countdown somewhere it cannot be forgotten.

                  Why it cannot be replayed the way the character read was: the
                  gate reads dealer gamma and an options-implied range and
                  neither is retained. Four of its eight factors are replayable;
                  scoring those four and calling it the gate would be a track
                  record for a different, weaker instrument. */}
              {gateQ.data && (
                <div className="mt-2 pt-2 border-t border-border/60">
                  {gateQ.data.available ? (
                    <>
                      <div className="text-[0.55rem] font-bold uppercase tracking-wider text-text-muted">
                        How this verdict has actually done
                      </div>
                      <div className="mt-1 space-y-0.5">
                        {(gateQ.data.buckets ?? []).map((b) => (
                          <div
                            key={b.verdict}
                            className={`flex items-baseline gap-2 text-[0.62rem] tabular-nums ${
                              b.verdict === d.conditions!.verdict
                                ? "text-text font-semibold"
                                : "text-text-muted"
                            }`}
                          >
                            <span className="w-20 shrink-0 uppercase">{b.verdict}</span>
                            <span className="w-14 text-right" title="Median session range as a multiple of a normal one.">
                              {b.median_range_x.toFixed(2)}×
                            </span>
                            <span className="w-16 text-right" title="Share of those sessions that ran 1.3× or wider.">
                              {b.wide_pct.toFixed(0)}% wide
                            </span>
                            <span
                              className="w-14 text-right text-text-muted/70"
                              title="Share that closed up. The gate makes no directional claim, so this should sit at the base rate — showing it lets you confirm that rather than take the disclaimer on trust."
                            >
                              {b.closed_up_pct.toFixed(0)}% up
                            </span>
                            <span
                              className="text-text-muted/60"
                              title={`${b.n_sessions} distinct sessions. The verdict stood for ${b.n_marks} thirty-minute marks across them — persistence, not sample size. One session counts once.`}
                            >
                              n={b.n_sessions}
                            </span>
                          </div>
                        ))}
                      </div>
                      <p className="text-[0.55rem] text-text-muted mt-1 leading-snug">
                        Base rates over the same {gateQ.data.scored_sessions ?? "—"} scored sessions:{" "}
                        {gateQ.data.base_wide_pct?.toFixed(0)}% ran wide, {gateQ.data.base_up_pct?.toFixed(0)}%
                        closed up. A bucket that does not separate from those is a verdict not earning
                        its place.{gateQ.data.caveat ? ` ${gateQ.data.caveat}` : ""}
                      </p>
                    </>
                  ) : (
                    <p className="text-[0.55rem] text-text-muted leading-snug">
                      <span className="font-semibold">This verdict is not yet scored.</span>{" "}
                      {gateQ.data.sessions != null && gateQ.data.needed != null ? (
                        <>
                          Logging since {gateQ.data.logging_since ?? "—"} — {gateQ.data.sessions} completed
                          session{gateQ.data.sessions === 1 ? "" : "s"} of the {gateQ.data.needed} needed
                          before a record is worth quoting,{" "}
                          {Math.max(0, gateQ.data.needed - gateQ.data.sessions)} to go.
                        </>
                      ) : (
                        <>{gateQ.data.reason ?? "No record yet."}</>
                      )}{" "}
                      Unlike the session-character read further down, this gate cannot be replayed
                      backwards — it reads dealer gamma and an options-implied range, and neither is
                      retained — so the clock started when the logging did.
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* THE CARD, AUDITED AGAINST ITSELF. Every other AI block here
              narrates numbers the reader is going through anyway; this one does
              the thing nothing else does — notices when two parts of the page
              cannot both be right. Rendered only when something was found:
              silence is the common case and printing "no contradictions" on
              every load would train the reader to stop seeing the block. */}
          {auditQ.data?.available && (auditQ.data.findings?.length ?? 0) > 0 && (
            <div className="border border-amber-500/40 bg-amber-500/5 rounded px-3 py-2">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-[0.6rem] font-bold uppercase tracking-wider text-amber-400">
                  This card disagrees with itself
                </span>
                <span className="text-[0.55rem] text-text-muted">
                  {auditQ.data.n_rule ?? 0} checked · {auditQ.data.n_model ?? 0} read
                  {auditQ.data.model ? ` · ${auditQ.data.model}` : ""}
                </span>
              </div>
              <div className="mt-1.5 space-y-1">
                {auditQ.data.findings?.map((f, i) => (
                  <div key={i} className="text-[0.65rem] leading-snug">
                    <span
                      className={`font-semibold ${
                        f.severity === "high" ? "text-loss"
                          : f.severity === "medium" ? "text-amber-400" : "text-text-muted"
                      }`}
                    >
                      {f.where}
                    </span>
                    <span className="text-text-muted"> — {f.finding}</span>
                    {f.source === "model" && (
                      <span
                        className="ml-1 text-[0.5rem] uppercase text-text-muted/60"
                        title="Read from the payload by a model rather than checked by a rule — this one can be wrong."
                      >
                        read
                      </span>
                    )}
                  </div>
                ))}
              </div>
              <p className="text-[0.55rem] text-text-muted mt-1 leading-snug">
                {auditQ.data.caveat}
              </p>
            </div>
          )}

          {/* THE REST OF THE SESSION. The gate above answers "should I engage";
              this is the only block addressed to somebody already positioned.
              On 2026-08-03 the card shouted STAND ASIDE at a reader who was
              already long, while the number that spoke to holding sat collapsed
              in a table three screens down.

              Shown only during RTH: every number in it is conditioned on time
              remaining, so "takes the high 4%" with no close ahead is describing
              a session that already finished. The block answers a question
              nobody can act on at 17:14, on a Saturday, or before the open. */}
          {d.rest_of_session?.available && inRth && (
            <div className="border border-border rounded px-3 py-2">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  From here to the close
                </span>
                <span className="text-[0.6rem] text-text-muted">
                  {d.rest_of_session.mark} · {d.rest_of_session.band} of the range ·{" "}
                  {d.rest_of_session.regime} · n={d.rest_of_session.n}
                </span>
                {d.rest_of_session.exact_cell === false && (
                  <span
                    className="text-[0.5rem] uppercase text-amber-400"
                    title="The exact cell for this mark, position and regime was too thin to report, so the other regime at the same time of day is shown instead."
                  >
                    nearest cell
                  </span>
                )}
              </div>

              <div className="grid grid-cols-3 gap-2 mt-1.5">
                <Stat
                  label="Takes the high"
                  value={`${d.rest_of_session.p_new_high?.toFixed(0)}%`}
                  tone="gain"
                />
                <Stat
                  label="Takes the low"
                  value={`${d.rest_of_session.p_new_low?.toFixed(0)}%`}
                  tone="loss"
                />
                <Stat
                  label="Closes above here"
                  value={`${d.rest_of_session.p_close_above?.toFixed(0)}%`}
                />
              </div>

              <div className="text-[0.6rem] text-text-muted tabular-nums mt-1.5">
                Median further up{" "}
                <span className="text-gain">
                  +{d.rest_of_session.median_max_up_units?.toFixed(0)}
                </span>
                {" · "}median give-back{" "}
                <span className="text-loss">
                  −{d.rest_of_session.median_max_dn_units?.toFixed(0)}
                </span>
                {" · "}close lands between{" "}
                {d.rest_of_session.to_close?.p25_units?.toFixed(0)} and{" "}
                {d.rest_of_session.to_close?.p75_units != null &&
                d.rest_of_session.to_close.p75_units > 0
                  ? `+${d.rest_of_session.to_close.p75_units.toFixed(0)}`
                  : d.rest_of_session.to_close?.p75_units?.toFixed(0)}{" "}
                handles half the time
              </div>

            </div>
          )}

          {/* CO-LOCATED LEVELS. The ladder lists every level separately and
              sorts by distance, so a reader counting rows counts confirmations
              — which is how the call wall at 7619.28 and the chart resistance
              at 7620.00 were described here as "two independent methods
              agreeing" when they are two views of one strike concentration. */}
          {d.level_clusters?.available && (d.level_clusters.n_cross_method ?? 0) > 0 && (
            <div className="border border-border rounded px-3 py-2">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  One reference, several reasons
                </span>
                <span className="text-[0.55rem] text-text-muted">
                  within {d.level_clusters.tolerance?.toFixed(2)} handles ·{" "}
                  {d.level_clusters.tolerance_basis}
                </span>
              </div>
              <div className="mt-1.5 space-y-1">
                {d.level_clusters.clusters
                  ?.filter((c) => c.cross_method)
                  .map((c, i) => (
                    <div key={i} className="flex items-baseline gap-2 text-[0.65rem]">
                      <span className="tabular-nums font-semibold text-text w-[4.75rem] shrink-0">
                        {c.center.toFixed(2)}
                      </span>
                      <span className="tabular-nums text-text-muted w-[3.25rem] shrink-0">
                        ±{(c.span / 2).toFixed(2)}
                      </span>
                      <span className="flex-1 min-w-0 text-text-muted">
                        {c.members.map((m) => m.label).join(" · ")}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* SESSIONS THAT LOOKED LIKE THIS ONE. The similar-day method power
              traders use for load forecasting: rather than extrapolating, find
              the days whose conditions resembled today and read what they did.

              What each number may claim differs, and the block says so rather
              than presenting them at one confidence. The range multiplier
              validated out of sample — 8.4% better than the unconditional
              forecast, p=0.0005, 1.87x lift on wide calls over 744 sessions. The
              up/down split is a measured NULL (53.8% against a 53.4% base rate)
              and is printed anyway: omitting it invites the reader to assume
              nobody checked. */}
          {analogQ.data?.available && (analogQ.data.analogs?.length ?? 0) > 0 && (
            <div className="border border-border rounded px-3 py-2">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                  Sessions that looked like this one
                </span>
                <span className="text-[0.55rem] text-text-muted">
                  nearest {analogQ.data.k_scored} of{" "}
                  {analogQ.data.n_history?.toLocaleString("en-US")}
                  {analogQ.data.mode === "intraday blend"
                    ? ` · re-matched at ${analogQ.data.slot}`
                    : " · prior structure + shape + vol"}
                </span>
              </div>

              <div className="mt-1.5 space-y-1">
                {analogQ.data.analogs?.map((a) => (
                  <div key={a.date} className="flex items-baseline gap-2 text-[0.65rem]">
                    <span className="tabular-nums text-text w-[5rem] shrink-0">{a.date}</span>
                    <span className="tabular-nums text-text-muted w-[3.25rem] shrink-0 text-right">
                      {a.range_mult?.toFixed(2)}×
                    </span>
                    <span className={`tabular-nums w-[3.5rem] shrink-0 text-right ${pctClass(a.ret_oc)}`}>
                      {a.ret_oc != null
                        ? `${a.ret_oc > 0 ? "+" : ""}${a.ret_oc.toFixed(2)}%`
                        : "—"}
                    </span>
                    <span className="text-text-muted/70 flex-1 min-w-0 truncate">
                      closed {a.close_pos != null ? `${(a.close_pos * 100).toFixed(0)}%` : "—"} up
                      the range
                      {a.hi_slot && ` · high ${a.hi_slot}`}
                      {a.next?.range_mult != null &&
                        ` · next day ${a.next.range_mult.toFixed(2)}×`}
                    </span>
                  </div>
                ))}
              </div>

              <div className="mt-1.5 pt-1.5 border-t border-border text-[0.6rem] tabular-nums leading-snug">
                <span className="text-text-muted">These sessions imply </span>
                <span className="text-text font-semibold">
                  {analogQ.data.today?.implied_range_mult?.toFixed(2)}×
                </span>
                <span className="text-text-muted"> a normal range today</span>
                {analogQ.data.today?.p25 != null && analogQ.data.today?.p75 != null && (
                  <span className="text-text-muted/70">
                    {" "}
                    (middle half {analogQ.data.today.p25.toFixed(2)}–
                    {analogQ.data.today.p75.toFixed(2)}×)
                  </span>
                )}
                {analogQ.data.accuracy && (
                  <span className="text-text-muted/70">
                    {" — "}
                    {analogQ.data.accuracy.mae_gain_pct}% better than assuming a normal
                    day, on {analogQ.data.accuracy.n_out_of_sample} out-of-sample
                    sessions (p={analogQ.data.accuracy.p_value})
                  </span>
                )}
              </div>

              {/* When the blend is live, show both halves. One fused number hides
                  that the analogs and the session's own path can disagree, and
                  the disagreement is the interesting part. */}
              {analogQ.data.mode === "intraday blend" &&
                analogQ.data.today?.path_implied != null &&
                analogQ.data.today?.analog_only != null && (
                  <div className="text-[0.6rem] text-text-muted/80 tabular-nums leading-snug">
                    Blending the analogs ({analogQ.data.today.analog_only.toFixed(2)}×) with
                    this session&apos;s own path ({analogQ.data.today.path_implied.toFixed(2)}×).
                    Measured better than the path alone at 10:30 and 11:30, and worse
                    after — so it is switched off from 12:30.
                  </div>
                )}

              {/* Printed as the null it is, in the same shape the macro-setup
                  block uses, so a coin flip cannot be read as a lean. */}
              {analogQ.data.today?.share_up != null && analogQ.data.accuracy && (
                <div className="text-[0.6rem] text-text-muted/80 tabular-nums leading-snug">
                  {(analogQ.data.today.share_up * 100).toFixed(0)}% of them closed up —
                  which says nothing. Direction called this way runs{" "}
                  {analogQ.data.accuracy.direction_accuracy}% against a{" "}
                  {analogQ.data.accuracy.direction_base}% base rate.
                </div>
              )}

              {analogQ.data.next_session?.implied_range_mult != null && (
                <div className="text-[0.6rem] text-amber-400/80 tabular-nums leading-snug mt-1">
                  The sessions that FOLLOWED these ran{" "}
                  {analogQ.data.next_session.implied_range_mult.toFixed(2)}× — context
                  only, not a forecast: that horizon measured p=
                  {analogQ.data.accuracy?.next_day_p} and flipped sign between halves.
                </div>
              )}
            </div>
          )}

          {/* WHAT MOVED THE TAPE. Ranked from the tape and annotated from the
              feed, never the reverse — starting from the news means deciding in
              advance which stories matter, which is how the page once narrated a
              6.5% crude break as having "no matching headline" while the story
              sat two modules away. A move with nothing attached prints as such,
              because that is information about the day rather than a gap. */}
          {d.attribution?.available && (d.attribution.moves?.length ?? 0) > 0 && (
            <div className="border border-border rounded px-3 py-2">
              {/* During the session this is live context — what is driving the
                  tape right now. Once the session is over it is history, so it
                  collapses rather than occupying the same space as the levels
                  the next session will open against. */}
              <details open={inRth} className="group">
                <summary className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted cursor-pointer select-none list-none flex items-center gap-1">
                  <span className="transition-transform group-open:rotate-90">▸</span>
                  What moved the tape
                  {!inRth && (
                    <span className="font-normal normal-case tracking-normal text-text-muted/70">
                      · {d.attribution.moves?.length} move
                      {(d.attribution.moves?.length ?? 0) === 1 ? "" : "s"}, last session
                    </span>
                  )}
                </summary>

              <div className="mt-1.5 space-y-1">
                {d.attribution.moves?.map((m, i) => (
                  <div key={i} className="flex items-baseline gap-2 text-[0.65rem]">
                    <span className="tabular-nums text-text-muted w-[4.75rem] shrink-0">
                      {m.start}–{m.end}
                    </span>
                    <span className="tabular-nums font-semibold w-[3rem] shrink-0 text-right">
                      {m.range.toFixed(1)}
                    </span>
                    <span className="tabular-nums text-text-muted w-[2.75rem] shrink-0 text-right">
                      {m.x_normal_bar?.toFixed(1)}×
                    </span>
                    <span
                      className={`tabular-nums w-[3.5rem] shrink-0 text-right ${pctClass(m.net)}`}
                    >
                      {m.net > 0 ? "+" : ""}
                      {m.net.toFixed(2)}
                    </span>
                    <span className="flex-1 min-w-0 truncate">
                      {m.event ? (
                        <span className="text-text">{m.event.name}</span>
                      ) : m.headlines.length > 0 ? (
                        <span className="text-text-muted" title={m.headlines[0].title}>
                          {m.headlines[0].at} · {m.headlines[0].title}
                        </span>
                      ) : (
                        <span className="text-amber-400">nothing in either feed</span>
                      )}
                    </span>
                  </div>
                ))}
              </div>

              {/* The × column changed meaning on 2026-08-30 and an unlabelled
                  number that quietly means something new is worse than no
                  number. It is now measured against a normal bar FOR THAT TIME
                  OF DAY, not against one median for the whole session — the
                  09:30 bar runs 1.65× the scale of a midday bar, so a flat
                  yardstick was over-reporting the open and missing the middle
                  of the day. */}
              {(d.attribution.moves?.length ?? 0) > 0 && (
                <p className="text-[0.55rem] text-text-muted/80 mt-1 leading-snug">
                  × is against a normal bar for that time of day, not a flat session
                  median — the opening bar runs 1.65× the scale of a midday one.
                </p>
              )}

              {(d.attribution.event_impacts?.length ?? 0) > 0 && (
                <div className="mt-2 pt-1.5 border-t border-border space-y-0.5">
                  {d.attribution.event_impacts?.map((e, i) => (
                    <div key={i} className="flex items-baseline gap-2 text-[0.6rem]">
                      <span className="tabular-nums text-text-muted w-[3rem] shrink-0">
                        {e.at}
                      </span>
                      <span className="flex-1 min-w-0 truncate text-text">{e.name}</span>
                      <span className="tabular-nums text-text-muted shrink-0">
                        {e.range.toFixed(1)} in 30m ={" "}
                        <span className="text-text">{e.x_normal_window?.toFixed(2)}×</span> normal for that hour
                      </span>
                    </div>
                  ))}
                </div>
              )}

                {d.attribution.unattributed_note && (
                  <p className="text-[0.6rem] text-amber-400/90 mt-1.5 leading-snug">
                    {d.attribution.unattributed_note}
                  </p>
                )}
              </details>
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
              {ladderRead && (
                <Takeaway headline={ladderRead.headline} detail={ladderRead.detail} />
              )}
              <p className="text-[0.55rem] text-text-muted leading-snug pt-0.5">
                Distances in handles, signed from the last price. Rows are dimmed when the level sits
                further away than a whole expected session&apos;s range — still context, but not a
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
                {intradaySched.length === 0 ? (
                  <p className="text-[0.65rem] text-text-muted">
                    Nothing timed inside {scheduleScope.label}
                    {afterClose.length > 0 ? " — but see after the close, below." : "."}
                  </p>
                ) : (
                  <div className="space-y-1">
                    {intradaySched.map((e, i) => (
                      <ScheduleRow key={`${e.name}-${i}`} e={e} mins={liveMins(e)} />
                    ))}
                  </div>
                )}
                {upcoming.length === 0 && intradaySched.length > 0 && (
                  <p className="text-[0.55rem] text-text-muted">All of today&apos;s prints are out.</p>
                )}

                {scheduleRead && (
                  <Takeaway
                    headline={scheduleRead.headline}
                    detail={scheduleRead.detail}
                    tone={scheduleRead.tone}
                  />
                )}

                {/* ── after this close ──
                    Separated from the rows above because it answers a different
                    question. Everything above sizes THIS session's range; this
                    sizes the cost of carrying a position through the night, and
                    merging the two is how a 16:15 report gets read as a reason
                    to trade the 10:00 chop differently. */}
                {afterClose.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-border/60 space-y-1">
                    <h4 className="text-[0.55rem] font-bold uppercase tracking-wider text-text-muted">
                      After this close — next session&apos;s gap
                    </h4>
                    {afterClose.map((e, i) => (
                      <ScheduleRow key={`ac-${e.name}-${i}`} e={e} mins={liveMins(e)} hideCountdown />
                    ))}
                    {prem?.available ? (
                      <p className="text-[0.55rem] text-text-muted leading-snug">
                        SPX prices{" "}
                        <span className="font-semibold text-text tabular-nums">
                          {prem.segment_handles}
                        </span>{" "}
                        handles for the {prem.session_expiry} close → {prem.next_expiry} close
                        segment
                        {prem.vs_session != null ? (
                          <>
                            {" "}
                            —{" "}
                            <span className="font-semibold text-text tabular-nums">
                              {prem.vs_session.toFixed(2)}×
                            </span>{" "}
                            the {prem.this_session_straddle} it prices for the session itself. That
                            multiple is the market&apos;s own price for the event; it needs no index
                            weight, because it is read off two straddles rather than inferred from
                            the name&apos;s size.
                            {prem.quote_source === "settled" && (
                              <>
                                {" "}
                                Both straddles are settlement-based with the book shut — the ratio
                                survives that better than either level, but treat it as indicative.
                              </>
                            )}
                          </>
                        ) : (
                          <>
                            . No multiple while the session is running:{" "}
                            {prem.vs_session_withheld ??
                              "the baseline it would divide by has already decayed."}
                          </>
                        )}
                      </p>
                    ) : (
                      <p className="text-[0.55rem] text-text-muted leading-snug">
                        {prem?.reason
                          ? `No event premium measured: ${prem.reason}.`
                          : "Event premium not measured for this session."}{" "}
                        The report still lands after the bell; only its priced cost is missing.
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* THE SETUP. Sits in the swing-horizon section because that is the
                  one place on this card already labelled lean-not-trigger.
                  Mechanisms are named to explain what is MOVING and are checked
                  against the tape; the size expectation carries measured numbers;
                  and the direction is printed as an explicit null with its
                  p-value, because acting on a narrative that feels directional
                  when the data says it is only about size is the specific error
                  this block exists to prevent. */}
              {d.macro_setup?.available && (d.macro_setup.n_drivers ?? 0) > 0 && (
                <div className="space-y-1.5">
                  <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                    Today&apos;s setup
                  </h3>

                  <div className="space-y-1.5">
                    {d.macro_setup.drivers?.map((dr) => (
                      <div key={dr.key} className="border border-border rounded px-2 py-1.5">
                        <div className="flex items-baseline gap-2 flex-wrap">
                          <span className="text-[0.65rem] font-semibold text-text">{dr.label}</span>
                          <span className="text-[0.6rem] tabular-nums text-text-muted">
                            {dr.symbol} {dr.z > 0 ? "+" : ""}
                            {dr.z.toFixed(1)}σ overnight · {dr.day_pct > 0 ? "+" : ""}
                            {dr.day_pct.toFixed(2)}% today
                          </span>
                        </div>
                        <p className="text-[0.6rem] text-text-muted leading-snug mt-0.5">
                          {dr.mechanism}
                        </p>
                        <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1">
                          {dr.chain.map((c) => (
                            <span key={c.symbol} className="text-[0.6rem] tabular-nums">
                              <span className="text-text-muted">{c.symbol} </span>
                              <span
                                className={
                                  c.state === "confirms" ? "text-gain"
                                    : c.state === "contradicts" ? "text-loss" : "text-amber-400"
                                }
                              >
                                {c.state}
                              </span>
                              <span className="text-text-muted">
                                {" "}
                                ({c.actual_pct > 0 ? "+" : ""}
                                {c.actual_pct.toFixed(2)}%)
                              </span>
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>

                  {d.macro_setup.chain_note && (
                    <p className="text-[0.6rem] text-amber-400/90 leading-snug">
                      {d.macro_setup.chain_note}
                    </p>
                  )}

                  {d.macro_setup.size && (
                    <div className="border border-border rounded px-2 py-1.5">
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <span className="text-[0.55rem] uppercase tracking-wider text-text-muted">
                          Room this setup implies
                        </span>
                        <span className="text-[0.7rem] font-bold tabular-nums text-text">
                          {d.macro_setup.size.median_x.toFixed(2)}× normal
                        </span>
                        <span className="text-[0.6rem] tabular-nums text-text-muted">
                          wide {d.macro_setup.size.wide_pct.toFixed(0)}% vs{" "}
                          {d.macro_setup.size.base_wide_pct.toFixed(0)}% base ·{" "}
                          {d.macro_setup.size.lift.toFixed(2)}× lift
                        </span>
                      </div>
                      <p className="text-[0.55rem] text-text-muted leading-snug mt-0.5">
                        {d.macro_setup.size.combination_note}
                      </p>
                    </div>
                  )}

                  {/* The null, printed in full. This is the load-bearing part. */}
                  {d.macro_setup.direction && (
                    <div className="border border-border rounded px-2 py-1.5">
                      <div className="text-[0.55rem] uppercase tracking-wider text-text-muted">
                        Direction — not conditioned by the setup
                      </div>
                      <div className="mt-1 space-y-0.5">
                        {d.macro_setup.direction.tests.map((t, i) => (
                          <div key={i} className="flex items-baseline gap-2 text-[0.6rem]">
                            <span className="flex-1 min-w-0 truncate text-text-muted">
                              {t.label}
                            </span>
                            <span className="tabular-nums text-text">
                              {t.up_pct.toFixed(1)}%
                            </span>
                            <span className="tabular-nums text-text-muted w-[6.5rem] text-right">
                              [{t.ci[0].toFixed(0)}–{t.ci[1].toFixed(0)}] p={t.p.toFixed(2)}
                            </span>
                          </div>
                        ))}
                        <div className="flex items-baseline gap-2 text-[0.6rem] pt-0.5 border-t border-border/60">
                          <span className="flex-1 text-text-muted">base rate</span>
                          <span className="tabular-nums text-text">
                            {d.macro_setup.direction.base_up_pct.toFixed(1)}%
                          </span>
                          <span className="w-[6.5rem]" />
                        </div>
                      </div>
                      <p className="text-[0.55rem] text-text-muted leading-snug mt-1">
                        {d.macro_setup.direction.verdict}
                      </p>
                    </div>
                  )}

                  <p className="text-[0.55rem] text-text-muted leading-snug">
                    {d.macro_setup.caveat}
                  </p>
                </div>
              )}

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
                        {/* THE ORDER IS TIER FIRST, THEN RECENCY — deliberately,
                            so an FOMC line from yesterday outranks colour from
                            an hour ago. `tier` was computed server-side and
                            never rendered, so the only ordering cue on screen
                            was the age, and a correctly-ordered list read as a
                            broken one: 3h · 4h · 9h · 1d · 2d · 3d · 1h · 5h.
                            Marking the index-movers makes the sort legible
                            without re-sorting it. */}
                        {n.tier === 1 && (
                          <span
                            className="text-spot mr-1"
                            title="A headline type that has moved the index. The list is ordered by this first, then by recency — not by time alone."
                          >
                            •
                          </span>
                        )}
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
                          {/* Separator INSIDE the conditional. `fmtAgo` returns "" until the
                              clock has a value, so ` · ${...}` emitted a dangling "·"
                              on the server against "· 1h ago" on the client — which is
                              the text mismatch that was still firing React #418 after
                              every time string had already been removed from the SSR.
                              Same shape as the "bars" label; fixed there, missed here. */}
                          {(() => {
                            const age = n.published ? fmtAgo(n.published, nowMin) : "";
                            return age ? ` · ${age}` : "";
                          })()}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {/* Says the ordering rule out loud, because a list whose sort
                      key is invisible looks unsorted however correct it is. */}
                  <p className="text-[0.52rem] text-text-muted leading-snug pt-1">
                    <span className="text-spot">•</span> marks headline types that have moved the
                    index. Ordered by that first, then by recency — so an older policy line
                    outranks fresher market colour. Nothing older than five days is carried.
                  </p>
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

                  {/* SESSION CHARACTER. The estimates above are option prices
                      and trailing statistics: they say what a session is worth,
                      never what THIS one is delivering. 2026-08-03 ran 79
                      handles against a VIX1D-implied 54 with nothing on the card
                      able to say so mid-session. This one is measured from the
                      range actually delivered, and carries its own out-of-sample
                      error rather than a confidence word.

                      The old wording here said those estimates were "fixed at
                      the open and cannot move". They are not fixed: `_vix1d()`
                      re-reads `Close.iloc[-1]` on every request and `_atr_handles`
                      refetches too, so both drift through the day. The real
                      limitation is direction, not staleness — VIX1D prices the
                      session AHEAD, so after 09:30 it is decreasingly about the
                      session being measured. Saying "frozen" told the reader a
                      number was anchored when nothing anchors it. */}
                  {/* Gated on EITHER instrument. It used to require the path
                      estimate, which left this whole block blank from the
                      pre-open until the first bucket closed at 10:30 — the one
                      window a reader most wants a number, and the exact hole
                      the HAR prior was added to fill. */}
                  {(d.regime?.path_implied?.available || d.regime?.har?.available) && (
                    <div className="pt-1.5 mt-1.5 border-t border-border space-y-0.5">
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                          {sessionComplete ? "Session result" : "Session character"}
                        </span>
                        <span
                          className={`text-[0.6rem] font-bold uppercase ${
                            d.regime.character === "wide" ? "text-loss"
                              : d.regime.character === "compressed" ? "text-accent" : "text-text"
                          }`}
                        >
                          {d.regime.character}
                        </span>
                        {(d.regime.path_implied?.available
                          ? d.regime.path_implied.multiplier
                          : d.regime.har?.multiplier) != null && (
                          <span className="text-[0.6rem] tabular-nums text-text font-semibold">
                            {(d.regime.path_implied?.available
                              ? d.regime.path_implied.multiplier!
                              : d.regime.har!.multiplier!
                            ).toFixed(2)}× normal
                          </span>
                        )}
                        {!d.regime.path_implied?.available && (
                          <span className="text-[0.55rem] uppercase tracking-wider text-text-muted/80">
                            pre-open prior
                          </span>
                        )}
                      </div>

                      {/* THE SECOND AXIS. Everything above sizes the session;
                          this says how STRAIGHT it has been. They are close to
                          independent — corr(range, efficiency) = +0.37 on the
                          same sample — so a wide day can be pure rotation and a
                          narrow one can be a single drift, and until now the
                          card had no word for the difference.

                          "Likely" and "confident" are not adjectives chosen
                          here. They are the measured frequency with which a
                          reading in this percentile band, AT THIS MARK, belonged
                          to a session that finished in that class, and the
                          number itself prints beside the label so the word can
                          never outrun it. The two sides resolve at different
                          speeds — a trend banks a net move that is hard to undo,
                          a quiet morning can still break out — which is why
                          "confident choppy" does not appear before the
                          afternoon and "confident trendy" can appear by 11:30. */}
                      {/* Every number this block prints is required in the
                          gate rather than asserted at the point of use. A
                          missing efficiency rendered as "0.000 against a 0.000
                          median" is an absence wearing the clothes of a
                          measurement, which is the specific way this card has
                          gone wrong before. */}
                      {d.chop_trend?.available &&
                        typeof d.chop_trend.pctile === "number" &&
                        typeof d.chop_trend.efficiency === "number" &&
                        typeof d.chop_trend.median_at_mark === "number" && (
                        <div className="pt-1 mt-1 border-t border-border/60 space-y-0.5">
                          <div className="flex items-baseline gap-1.5 flex-wrap">
                            <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                              Shape
                            </span>
                            <span
                              className={`text-[0.6rem] font-bold uppercase ${
                                d.chop_trend.side === "choppy" ? "text-amber-400"
                                  : d.chop_trend.side === "trendy" ? "text-accent" : "text-text"
                              }`}
                            >
                              {d.chop_trend.label}
                            </span>
                            <span className="text-[0.6rem] tabular-nums text-text font-semibold">
                              {d.chop_trend.pctile.toFixed(0)}th pctile
                            </span>
                            <span className="text-[0.55rem] uppercase tracking-wider text-text-muted/80">
                              at {d.chop_trend.mark}
                            </span>
                            {/* The mark is the last 30-minute boundary the FEED
                                has bars for, which on this plan runs roughly
                                twenty minutes behind the wall clock. Without
                                this the card reads "at 11:30" at ten past noon
                                and looks frozen, when it is simply refusing to
                                score a mark whose bars have not arrived. */}
                            {d.chop_trend.last_bar && d.chop_trend.last_bar !== d.chop_trend.mark && (
                              <span className="text-[0.55rem] tabular-nums text-text-muted/80">
                                bars through {d.chop_trend.last_bar}
                              </span>
                            )}
                            {d.chop_trend.bars_stale && (
                              <span className="text-[0.55rem] uppercase tracking-wider text-amber-400">
                                cached bars
                              </span>
                            )}
                          </div>
                          {/* The efficiency ratio falls mechanically with bar
                              count, so the raw figure is meaningless without the
                              median it is being read against — printing 0.09 on
                              its own invites comparison with a 15:00 reading
                              that is not the same statement. */}
                          <div className="text-[0.6rem] text-text-muted tabular-nums">
                            Efficiency {d.chop_trend.efficiency.toFixed(3)} against a{" "}
                            {d.chop_trend.median_at_mark.toFixed(3)} median at this mark ·
                            sessions reading here finished{" "}
                            <span className="text-text">
                              {d.chop_trend.side === "trendy"
                                ? d.chop_trend.p_finish_trendy_pct?.toFixed(0)
                                : d.chop_trend.p_finish_choppy_pct?.toFixed(0)}
                              %
                            </span>{" "}
                            {d.chop_trend.side === "mixed" ? "in that class" : d.chop_trend.side}{" "}
                            against a 33% base (n={d.chop_trend.n_band})
                          </div>
                          {/* The null travels WITH the reading rather than in a
                              footnote. A label that says the morning was choppy
                              is read as a forecast unless the page says, in
                              numbers and in the same breath, that it is not one. */}
                          {d.chop_trend.forward && (
                            <p className="text-[0.55rem] text-text-muted/80 leading-snug">
                              Says nothing about the hours ahead: efficiency so far and
                              efficiency to come are correlated{" "}
                              <span className="tabular-nums">
                                {d.chop_trend.forward.corr >= 0 ? "+" : ""}
                                {d.chop_trend.forward.corr.toFixed(3)}
                              </span>{" "}
                              on bars that do not overlap, and this band was followed by a
                              choppy remainder{" "}
                              <span className="tabular-nums">
                                {d.chop_trend.forward.p_rest_choppy_pct.toFixed(0)}%
                              </span>{" "}
                              of the time against a{" "}
                              <span className="tabular-nums">
                                {d.chop_trend.forward.base_pct.toFixed(0)}%
                              </span>{" "}
                              base rate — a measured null. This describes the tape behind you.
                            </p>
                          )}
                          {d.chop_trend.band_widened && (
                            <p className="text-[0.55rem] text-text-muted/80 leading-snug">
                              The exact percentile band was too thin to quote, so the wider
                              tercile on this side is shown instead.
                            </p>
                          )}
                        </div>
                      )}
                      {/* Once the path is fully covered this stops being an
                          estimate — implied_range is range_so_far divided by
                          1.0, i.e. the delivered range. Saying "implies 44 for
                          the session" about a session that is over applies
                          forecast language to a measured fact, and quoting the
                          estimator's out-of-sample error alongside it is worse:
                          a measurement has no forecast error. */}
                      {!d.regime.path_implied?.available ? (
                        /* PRE-OPEN / FIRST HOUR. Deliberately worded as a prior
                           rather than a forecast of today: it is built only from
                           sessions that have already closed, so it cannot see a
                           catalyst that has not traded yet. */
                        <>
                          {/* `implied_range` is null whenever no trailing normal
                              range was passed in. Rendering it unguarded prints
                              "implies  handles against a normal" — an absence
                              shown as a calm, which is the failure mode this
                              card keeps hitting. Fall back to the multiplier,
                              which always exists when `available` is true. */}
                          <div className="text-[0.6rem] text-text-muted tabular-nums">
                            {/* Named from `asof`, never "last night" or "today".
                                This block is most visible exactly when the
                                market is SHUT, and on a Saturday the last
                                session was Friday and there is no today — so
                                both of those words are simply false. */}
                            {d.regime.har?.implied_range != null ? (
                              <>
                                Realised variance through{" "}
                                {d.regime.har.asof ?? "the last session"} implies{" "}
                                <span className="text-text">
                                  {d.regime.har.implied_range.toFixed(0)}
                                </span>{" "}
                                handles for the next session, against a normal{" "}
                                {d.regime.har.normal_range?.toFixed(0)}
                              </>
                            ) : (
                              <>
                                Realised variance through{" "}
                                {d.regime.har?.asof ?? "the last session"} implies{" "}
                                <span className="text-text">
                                  {d.regime.har?.multiplier?.toFixed(2)}×
                                </span>{" "}
                                a normal session for the next one
                              </>
                            )}
                          </div>
                          <p className="text-[0.55rem] text-text-muted/80 leading-snug">
                            A pre-open prior, not a read on the session itself. Realised variance on its own
                            1-, 5- and 22-day averages, measured over{" "}
                            {d.regime.har?.sessions?.toLocaleString()} sessions — median error{" "}
                            {d.regime.har?.oos_mae_pct?.toFixed(0)}% out of sample against{" "}
                            40% for the trailing median it replaces. It cannot see the coming session&apos;s
                            catalyst, and nothing fitted on closed sessions forecasts the widest days.
                            Once the first bucket closes at 10:30 the path estimate takes over.
                          </p>
                        </>
                      ) : sessionComplete ? (
                        <>
                          <div className="text-[0.6rem] text-text-muted tabular-nums">
                            Delivered {d.regime.path_implied.implied_range?.toFixed(0)} handles
                            against a normal{" "}
                            {d.regime.path_implied.normal_range?.toFixed(0)}
                          </div>
                          {/* Implied against delivered. This comparison used to
                              fire as an auditor contradiction ("options-implied
                              97 vs path-implied 44 differ by more than 50%"),
                              which was wrong — nothing contradicted, the day
                              simply came in quieter than options price a session.
                              Removing it from the auditor left the observation
                              homeless; it belongs here, beside the range it is
                              being compared against.
                              NOT worded as grading the open's forecast, which is
                              what I wrote first and could not support: nothing
                              anchors this estimate to the open. `_vix1d()` takes
                              `Close.iloc[-1]` on every call, so `expected_range`
                              is the CURRENT quote, not the one that stood at
                              09:30 — the open's number is never retained. This
                              says what options price now versus what today
                              delivered, which is what the data supports. */}
                          {d.expected_move?.expected_range != null &&
                            d.expected_move?.consumed?.pct != null && (
                              <div className="text-[0.6rem] text-text-muted tabular-nums">
                                {d.expected_move.headline?.source ?? "Options"} prices{" "}
                                <span className="text-text">
                                  {d.expected_move.expected_range.toFixed(0)}
                                </span>{" "}
                                for a session — today&apos;s range was{" "}
                                <span className="text-text">
                                  {d.expected_move.consumed.pct.toFixed(0)}%
                                </span>{" "}
                                of that
                              </div>
                            )}
                        </>
                      ) : (
                        <>
                          <div className="text-[0.6rem] text-text-muted tabular-nums">
                            {d.regime.path_implied.range_so_far?.toFixed(0)} handles in by{" "}
                            {d.regime.path_implied.slot} · a typical session has{" "}
                            {d.regime.path_implied.typical_pct_covered?.toFixed(0)}% of its range by
                            then · implies {d.regime.path_implied.implied_range?.toFixed(0)} for the
                            session against a normal {d.regime.path_implied.normal_range?.toFixed(0)}
                          </div>
                          <p className="text-[0.55rem] text-text-muted/80 leading-snug">
                            Measured from this session&apos;s own delivered range rather than from
                            an option price. The estimates above are re-read on every load and
                            price the session ahead, so neither they nor this one is anchored to
                            the open. Median error at this hour is{" "}
                            {d.regime.path_implied.oos_mae_pct?.toFixed(0)}% out of sample.
                          </p>
                        </>
                      )}

                      {/* THE SESSION AGAINST ITS OWN PRE-OPEN PRIOR. Only shown
                          once both instruments exist. A day running well past
                          what last night's volatility implied is the signature
                          this module was built for — an unscheduled catalyst has
                          no calendar slot, so nothing else on the card can say
                          it. Stated as what the tape has done, never as what it
                          will do next. */}
                      {d.regime?.divergence?.ratio != null &&
                        (d.regime.divergence.ratio >= 1.25 ||
                          d.regime.divergence.ratio <= 0.8) && (
                          <div className="text-[0.6rem] text-text-muted tabular-nums">
                            Running{" "}
                            <span
                              className={
                                d.regime.divergence.ratio >= 1.25 ? "text-loss" : "text-accent"
                              }
                            >
                              {d.regime.divergence.ratio.toFixed(2)}×
                            </span>{" "}
                            what last night&apos;s volatility implied (
                            {d.regime.divergence.har_multiplier?.toFixed(2)}× prior vs{" "}
                            {d.regime.divergence.path_multiplier?.toFixed(2)}× delivered)
                          </div>
                        )}

                      {/* HOW THIS READ HAS ACTUALLY DONE. Sits against the number
                          it scores rather than on a separate page — a track
                          record a reader has to go and find is a track record
                          nobody checks. Replayed over the full history, so it is
                          available today rather than accumulating forward. */}
                      {trackQ.data?.available && (
                        <div className="mt-1 pt-1 border-t border-border/60">
                          {(() => {
                            const band =
                              d.regime?.character === "compressed" ? "compressed"
                                : d.regime?.character === "wide" ? "wide" : "normal";
                            const b = trackQ.data.buckets?.find((x) => x.band === band);
                            if (!b) return null;
                            return (
                              <p className="text-[0.55rem] text-text-muted leading-snug">
                                <span className="uppercase tracking-wider">Its record: </span>
                                when this read said{" "}
                                <span className="text-text">{band}</span>, the session ran 1.3×
                                or wider{" "}
                                <span className="text-text tabular-nums">
                                  {b.delivered_wide_pct.toFixed(0)}%
                                </span>{" "}
                                of the time against a{" "}
                                {trackQ.data.base_wide_pct?.toFixed(0)}% base, and the forecast
                                ran{" "}
                                <span className={Math.abs(b.median_err_pct) >= 10 ? "text-amber-400" : "text-text"}>
                                  {Math.abs(b.median_err_pct).toFixed(0)}%{" "}
                                  {b.median_err_pct < 0 ? "low" : "high"}
                                </span>{" "}
                                (n={b.n}). It closed up {b.closed_up_pct.toFixed(0)}% against a{" "}
                                {trackQ.data.base_up_pct?.toFixed(0)}% base — this read carries
                                no directional information, and that is the number showing it.
                              </p>
                            );
                          })()}
                        </div>
                      )}
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

                  {emRead && (
                    <Takeaway headline={emRead.headline} detail={emRead.detail} tone={emRead.tone} />
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
                        ? ` (last printed ${new Date(d.gamma.spx_spot_asof).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })} at ${d.gamma.spx_spot?.toFixed(2)})`
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
                  {gammaRead && (
                    <Takeaway
                      headline={gammaRead.headline}
                      detail={gammaRead.detail}
                      tone={gammaRead.tone}
                    />
                  )}
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

                  {structureRead && (
                    <Takeaway
                      headline={structureRead.headline}
                      detail={structureRead.detail}
                      tone={structureRead.tone}
                    />
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
                  {d.breadth.universe?.n?.toLocaleString("en-US")}
                  {d.breadth.universe?.eligible_n
                    ? ` of ${d.breadth.universe.eligible_n.toLocaleString("en-US")}`
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

              {/* THE TWO-SIDED MEASURES, AS BARS. Advancing-vs-declining and
                  up-vs-down volume are each ONE fact about a split, and drawing
                  them as separate stat tiles makes the reader subtract to see
                  which side is winning and by how much. The 50/200-day rows
                  below answer a different question entirely — not "was today
                  broad" but "is the market broadly in an uptrend" — and those
                  two diverge exactly when an index is carried by a few names. */}
              <div className="space-y-1.5">
                <SplitBar
                  leftLabel="Advancing" rightLabel="Declining"
                  leftValue={d.breadth.advancers?.toLocaleString("en-US") ?? "—"}
                  rightValue={d.breadth.decliners?.toLocaleString("en-US") ?? "—"}
                  leftPct={
                    d.breadth.advancers != null && d.breadth.decliners != null &&
                    (d.breadth.advancers + d.breadth.decliners) > 0
                      ? (d.breadth.advancers / (d.breadth.advancers + d.breadth.decliners)) * 100
                      : null
                  }
                  title="Advancing vs declining names in the liquid universe. Unchanged names are excluded from the split rather than assigned a side."
                />
                <SplitBar
                  leftLabel="Up volume" rightLabel="Down volume"
                  leftValue={d.breadth.up_volume_pct != null ? `${d.breadth.up_volume_pct.toFixed(0)}%` : "—"}
                  rightValue={d.breadth.up_volume_pct != null ? `${(100 - d.breadth.up_volume_pct).toFixed(0)}%` : "—"}
                  leftPct={d.breadth.up_volume_pct ?? null}
                  title="Share of traded volume in advancing names. Volume disagrees with the count when a few heavily-traded names move against the majority."
                />
                {d.breadth.trend?.available && [50, 200].map((w) => {
                  const tr = d.breadth!.trend!;
                  const win = tr.windows?.[String(w)];
                  const h = tr.history?.[`pct_above_${w}dma`];
                  if (!win || win.pct_above == null) return null;
                  return (
                    <SplitBar
                      key={w}
                      leftLabel={`Above ${w}DMA`} rightLabel="Below"
                      leftValue={`${win.pct_above.toFixed(1)}% (${win.above.toLocaleString("en-US")})`}
                      rightValue={`${(100 - win.pct_above).toFixed(1)}% (${win.below.toLocaleString("en-US")})`}
                      leftPct={win.pct_above}
                      title={
                        `Share of the ${tr.universe?.n?.toLocaleString("en-US")}-name liquid universe closing above its own ${w}-day average, ` +
                        `built from ${tr.sessions_used} sessions back to ${tr.from}. ` +
                        `${win.excluded_short_history.toLocaleString("en-US")} names are excluded for not having ${w} sessions of history — ` +
                        `averaging a ${w}-day over fewer would be a different measure. ${tr.universe?.note ?? ""}`
                      }
                      right={
                        /* RELATIVE TO WHAT. "63% above the 200-day" is a level;
                           whether that is high or low needs its own history,
                           which starts accumulating today. The shortfall is
                           rendered as a count, never as a middle value. */
                        h?.pctile != null ? (
                          <span className="text-[0.55rem] text-text-muted">
                            {ordinal(Math.round(h.pctile))} of {h.n_history}
                          </span>
                        ) : (
                          <span
                            className="text-[0.55rem] text-text-muted/60"
                            title={`No reference set yet — ${h?.n_history ?? 0} sessions recorded and 60 are needed. Until then this is a level, not a reading: nothing here says whether it is high or low.`}
                          >
                            {h?.n_history ?? 0}/60
                          </span>
                        )
                      }
                    />
                  );
                })}
              </div>

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
                    {d.breadth.advancers?.toLocaleString("en-US")} up ·{" "}
                    {d.breadth.decliners?.toLocaleString("en-US")} down
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

              {breadthRead && (
                <Takeaway
                  headline={breadthRead.headline}
                  detail={breadthRead.detail}
                  tone={breadthRead.tone}
                />
              )}

              <p className="text-[0.52rem] text-text-muted leading-snug">
                {d.breadth.reconstruction}
                {d.breadth.tick && !d.breadth.tick.available && ` NYSE TICK is not shown: ${d.breadth.tick.reason}`}
              </p>
            </div>
          )}

          {/* ── measured base rates ── */}
          {d.base_rates?.available && (
            <div className="border-t border-border pt-3">
              {/* Foldable: this and the session-path table below are the two
                  largest blocks on a card measured at 3,967px, and they are
                  unconditional history rather than anything about today. */}
              <CardSection
                id="baserates"
                title="Measured base rates"
                subtitle={
                  /* This label describes the DAILY study. The intraday path
                     rates below run on SPY 5-minute bars over a shorter window,
                     and the overnight study elsewhere on this card is ES futures
                     over two years — three instruments, three windows. Any one
                     of them unlabelled and a reader merges them into one. */
                  <span className="text-[0.55rem] text-text-muted">
                    {d.base_rates.instrument ?? d.base_rates.source} ·{" "}
                    {d.base_rates.sessions?.toLocaleString("en-US")} sessions ·{" "}
                    {d.base_rates.from} to {d.base_rates.to}
                  </span>
                }
              >
              {d.base_rates.path?.available && d.base_rates.path.instrument && (
                <p className="text-[0.55rem] text-text-muted leading-snug">
                  Intraday path rates below are measured on{" "}
                  <span className="text-text">{d.base_rates.path.instrument}</span>,{" "}
                  {d.base_rates.path.sessions?.toLocaleString("en-US")} sessions
                  {d.base_rates.path.from ? ` (${d.base_rates.path.from} to ${d.base_rates.path.to})` : ""}
                  {d.base_rates.path.instrument_note ? ` — ${d.base_rates.path.instrument_note}` : "."}
                </p>
              )}

              {d.base_rates.gaps?.today && (
                <p className="text-[0.65rem] text-text border-l-2 border-l-accent pl-2">
                  {d.base_rates.gaps.today.note}
                </p>
              )}

              {/* The unconditional rate above describes the session before it
                  opens. This one describes what is left of it, and the two
                  diverge sharply by midday — so it sits directly beneath,
                  labelled with its own instrument and window. */}
              {d.base_rates.gap_fill_live?.available && (() => {
                const g = d.base_rates.gap_fill_live!;
                if (g.state === "filled") {
                  return (
                    <p className="text-[0.65rem] text-text-muted border-l-2 border-l-border pl-2">
                      Today&apos;s gap has already traded back to the prior close, so the rate
                      above no longer describes anything outstanding.
                    </p>
                  );
                }
                if (g.fill_rate == null) {
                  return g.reason ? (
                    <p className="text-[0.6rem] text-text-muted border-l-2 border-l-border pl-2">
                      Still-open gap-fill rate: {g.reason}.
                    </p>
                  ) : null;
                }
                return (
                  <div className="border-l-2 border-l-accent pl-2 space-y-0.5">
                    <p className="text-[0.65rem] text-text">
                      Still open at {g.as_of}
                      {g.distance === "holding"
                        ? " with price holding its distance from the prior close"
                        : g.distance === "retraced"
                          ? " with part of the gap already retraced"
                          : ""}
                      , a gap this size has gone on to fill by the close{" "}
                      <span className="text-accent font-medium tabular-nums">
                        {g.fill_rate.toFixed(0)}%
                      </span>{" "}
                      of {g.n} times — against{" "}
                      <span className="tabular-nums">{g.unconditional?.toFixed(0)}%</span>{" "}
                      measured over the whole session.
                    </p>
                    <p className="text-[0.55rem] text-text-muted leading-snug">
                      Conditioned on the {g.conditioned_on}. Measured on{" "}
                      <span className="text-text">{g.instrument}</span>,{" "}
                      {g.sessions?.toLocaleString("en-US")} sessions ({g.from} to {g.to}) — a
                      different series and a shorter window than the daily rates above.
                    </p>
                  </div>
                );
              })()}

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

              {baseRatesRead && (
                <Takeaway
                  headline={baseRatesRead.headline}
                  detail={baseRatesRead.detail}
                  tone={baseRatesRead.tone}
                />
              )}
              </CardSection>
            </div>
          )}

          {/* ── intraday path: WHEN the session gets there ── */}
          {d.base_rates?.path?.available && (
            <div className="border-t border-border pt-3">
              <CardSection
                id="sessionpath"
                title="Session path"
                subtitle={
                  /* Its own window — shorter than the daily study above, and
                     labelling it with those sessions would overstate it. */
                  <span className="text-[0.55rem] text-text-muted">
                    {d.base_rates.path.source} · {d.base_rates.path.sessions?.toLocaleString("en-US")} sessions ·{" "}
                    {d.base_rates.path.from} to {d.base_rates.path.to}
                  </span>
                }
              >

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

              {pathRead && (
                <Takeaway headline={pathRead.headline} detail={pathRead.detail} tone={pathRead.tone} />
              )}

              {d.base_rates.path.initial_balance?.note && (
                <p className="text-[0.52rem] text-text-muted leading-snug">
                  {d.base_rates.path.initial_balance.note}
                  {d.base_rates.path.extremes?.some((e) => e.minutes === 30) &&
                    " * 15:30 covers 30 minutes, half the width of the other buckets."}
                </p>
              )}
              </CardSection>
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
              {/* Each block used to print its own standing caveat, so the card
                  carried five methodology paragraphs stacked between the numbers
                  a reader came for. They are the same sentences every session —
                  standing methodology, which is what this disclosure is for.
                  Caveats about TODAY (a contested estimate, an unattributed
                  move) still print inline, because those are findings. */}
              {howToRead.length > 0 && (
                <div className="space-y-1.5 pt-1.5 border-t border-border">
                  {howToRead.map((c, i) => (
                    <p key={i}>
                      <span className="text-text font-semibold">{c.label}.</span>{" "}
                      {c.text}
                    </p>
                  ))}
                </div>
              )}
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
