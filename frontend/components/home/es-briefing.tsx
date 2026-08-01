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
import { AIInterpretation } from "@/components/ai-interpretation";
import {
  fetchEsBrief,
  type EsBrief,
  type EsImpact,
  type EsLevel,
  type EsScheduleItem,
} from "@/lib/api";

/* ─── formatting ──────────────────────────────────────────────── */

/** No thousands separator: ES is quoted 7503.50 on every ladder and DOM, and
 *  the grouping comma misaligns a column of tabular numbers. */
const fmtPx = (n: number) => n.toFixed(2);

/** ES trades in quarter-points; "handles" is the trader's word for index points. */
const fmtHandles = (n: number) => `${n > 0 ? "+" : ""}${n.toFixed(2)}`;

function fmtCountdown(mins: number): string {
  const a = Math.abs(mins);
  const h = Math.floor(a / 60);
  const m = a % 60;
  const body = h > 0 ? `${h}h ${m}m` : `${m}m`;
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

/** Minutes since ET midnight, read from the browser clock in exchange time.
 *  Reading the wall clock in `America/New_York` keeps this correct for a user
 *  in any timezone, and correct across DST without a lookup table. */
function etNowMinutes(): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date());
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  return h * 60 + m;
}

/** The ET clock as an external store, so countdowns stay live between the
 *  3-minute server refreshes without a setState-in-effect cascade. The server
 *  snapshot is null, which makes the card fall back to the server-computed
 *  `minutes_away` for SSR and hydration — no timestamp mismatch to reconcile. */
const ET_CLOCK = {
  subscribe(onChange: () => void) {
    const t = setInterval(onChange, 30_000);
    return () => clearInterval(t);
  },
  // A primitive that only changes on a minute boundary, so React's identity
  // check settles immediately rather than looping.
  getSnapshot: (): number | null => etNowMinutes(),
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
  clock: string | null;
  lean: string | null;
}

function buildRead(d: EsBrief, liveMins: (e: EsScheduleItem) => number): Read | null {
  const lv = d.levels;
  if (!lv?.available || !lv.levels?.length) return null;

  const by = new Map<string, EsLevel>(lv.levels.map((l) => [l.key, l]));
  const last = lv.last;
  const g = (k: string) => by.get(k);

  /* 1. Location — where price sits relative to value and VWAP. This single
     fact does more to set trend-vs-rotation expectations than anything else
     on the card, so it leads. */
  const vah = g("vah");
  const val = g("val");
  const vwap = g("vwap");
  const parts: string[] = [];

  if (vah && val) {
    if (last > vah.value) {
      parts.push(
        `Price is above the value area high (${fmtPx(vah.value)}), in upside discovery — ` +
          `outside value, sessions tend to trend rather than rotate`
      );
    } else if (last < val.value) {
      parts.push(
        `Price is below the value area low (${fmtPx(val.value)}), in downside discovery — ` +
          `outside value, sessions tend to trend rather than rotate`
      );
    } else {
      parts.push(
        `Price is inside the value area (${fmtPx(val.value)}–${fmtPx(vah.value)}), ` +
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

  /* 2. Structure — how much of a normal day's range is already spent, and how
     the session opened against yesterday. Both are range-budget questions. */
  const structBits: string[] = [];
  const th = g("today_high");
  const tl = g("today_low");
  const ph = g("py_high");
  const pl = g("py_low");
  if (th && tl) {
    const range = th.value - tl.value;
    if (ph && pl) {
      const prior = ph.value - pl.value;
      const pct = prior > 0 ? (range / prior) * 100 : null;
      structBits.push(
        `The developing range is ${range.toFixed(2)} handles` +
          (pct != null
            ? `, ${Math.round(pct)}% of the prior session's ${prior.toFixed(2)}` +
              (pct < 50 ? " — room left before the day is stretched" : pct > 100 ? " — already an outsized day" : "")
            : "")
      );
    } else {
      structBits.push(`The developing range is ${range.toFixed(2)} handles`);
    }
  }
  const open = g("today_open");
  const pc = g("py_close");
  if (open && pc) {
    const gap = open.value - pc.value;
    if (Math.abs(gap) >= 0.25) {
      structBits.push(
        `it opened ${Math.abs(gap).toFixed(2)} ${gap > 0 ? "above" : "below"} the prior close ` +
          `(${fmtPx(pc.value)})`
      );
    } else {
      structBits.push(`it opened flat to the prior close (${fmtPx(pc.value)})`);
    }
  }
  const structure = structBits.length ? `${structBits.join(", and ")}.` : null;

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
    clock = "Everything scheduled for today has already printed; the rest of the session is left to positioning and the levels.";
  } else {
    clock = "Nothing scheduled on the macro calendar today — no timed catalyst to trade around.";
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

  return { location, structure, clock, lean };
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
    ET_CLOCK.subscribe,
    ET_CLOCK.getSnapshot,
    ET_CLOCK.getServerSnapshot
  );

  const liveMins = useMemo(() => {
    return (e: EsScheduleItem): number => {
      if (nowMin == null) return e.minutes_away;
      const [hh, mm] = e.time_et.split(":").map(Number);
      if (!Number.isFinite(hh) || !Number.isFinite(mm)) return e.minutes_away;
      return hh * 60 + mm - nowMin;
    };
  }, [nowMin]);

  const read = useMemo(() => (d ? buildRead(d, liveMins) : null), [d, liveMins]);

  const lv = d?.levels;
  const session = d?.session;

  // The "Today" levels describe the most recent RTH session, which overnight
  // and at the weekend is NOT the current calendar date. Say which one.
  const sessionDateLabel = useMemo(() => {
    if (!lv?.session_date || !session?.now) return null;
    if (session.now.slice(0, 10) === lv.session_date) return null;
    const [, m, day] = lv.session_date.split("-");
    return `${m}/${day}`;
  }, [lv, session]);

  // Ladder rows: every level plus the last price, sorted high to low, so the
  // card reads the way a price ladder does.
  const ladder = useMemo(() => {
    if (!lv?.available || !lv.levels?.length) return [];
    const rows: Array<{ kind: "level"; l: EsLevel } | { kind: "last"; value: number }> =
      lv.levels.map((l) => ({ kind: "level" as const, l }));
    rows.push({ kind: "last" as const, value: lv.last });
    return rows.sort((a, b) => (b.kind === "last" ? b.value : b.l.value) - (a.kind === "last" ? a.value : a.l.value));
  }, [lv]);

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
          <span className="text-[0.6rem] text-text-muted">
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
          {/* the read */}
          {read && (
            <div className="border-l-2 border-l-accent bg-accent/5 px-3 py-2 rounded-r text-[0.7rem] leading-relaxed space-y-1">
              {read.location && <p className="text-text">{read.location}</p>}
              {read.structure && <p className="text-text">{read.structure}</p>}
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
                {sessionDateLabel && (
                  <span className="text-[0.55rem] text-text-muted" title="No RTH session today yet — these describe the most recent one.">
                    session of {sessionDateLabel}
                  </span>
                )}
              </div>

              {ladder.length === 0 ? (
                <p className="text-xs text-text-muted">Intraday levels unavailable.</p>
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
                        className={`flex items-center gap-2 py-1 pl-2 border-l-2 ${
                          GROUP_CLASS[row.l.group] ?? "border-l-border"
                        } border-b border-b-border/30 last:border-b-0`}
                        title={row.l.note}
                      >
                        <span className="tabular-nums text-text w-[4.5rem]">{fmtPx(row.l.value)}</span>
                        <span className="text-text-muted truncate flex-1 min-w-0">{row.l.label}</span>
                        <span
                          className={`tabular-nums shrink-0 ${
                            row.l.side === "above" ? "text-gain" : "text-loss"
                          }`}
                          title={`Price is ${Math.abs(row.l.distance).toFixed(2)} handles ${row.l.side} this level`}
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
                volume and break more easily than RTH levels made on size.
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
                  Scheduled risk — today
                </h3>
                {(d.schedule ?? []).length === 0 ? (
                  <p className="text-[0.65rem] text-text-muted">
                    Nothing on the macro calendar today.
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
                        <div className="text-text font-semibold capitalize">{d.macro.net_label ?? "—"}</div>
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
                  <ul className="space-y-1">
                    {(d.news ?? []).slice(0, 8).map((n, i) => (
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

          <AIInterpretation
            page="home_es_briefing"
            buttonLabel="Interpret this session"
            data={{
              session: d.session,
              levels: lv?.available
                ? {
                    last: lv.last,
                    asof: lv.asof,
                    session_date: lv.session_date,
                    rth_open_bars: lv.rth_open_bars,
                    nearest: lv.nearest,
                    levels: lv.levels?.map((l) => ({
                      key: l.key, label: l.label, group: l.group,
                      value: l.value, distance: l.distance, side: l.side,
                    })),
                  }
                : null,
              schedule: (d.schedule ?? []).map((e) => ({ ...e, minutes_away: liveMins(e) })),
              next_event: upcoming[0] ?? null,
              cta: d.cta,
              macro: d.macro,
              news: (d.news ?? []).slice(0, 8).map((n) => ({ source: n.source, title: n.title })),
            }}
          />
        </>
      )}
    </div>
  );
}
