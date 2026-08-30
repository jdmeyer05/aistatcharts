"use client";

/**
 * Shared furniture for the home cards.
 *
 * Three things were being re-implemented per card, slightly differently each
 * time, and each difference was a small dishonesty:
 *
 *   1. THE TAKEAWAY. The CTA board had a derived read ("so the card is never
 *      just numbers without a takeaway") and nothing else did. A card that
 *      renders eight measured numbers and no reading is asking the reader to
 *      do the synthesis, which is how a dashboard becomes wallpaper.
 *
 *   2. THE HEADER'S HONESTY ABOUT AGE. Only the pulse strip and the ES card
 *      showed how old their data was. Five boards refresh on a 30-60 minute
 *      cadence against a Supabase-cached backend and showed no timestamp at
 *      all — so a cache outage renders identically to a live read, which this
 *      project has already been bitten by once.
 *
 *   3. GROUPING BY HOW FAST THINGS MOVE. Eleven cards all wore the same live
 *      styling while roughly two of them changed intraday. S&P valuation sat
 *      on a 60-minute refetch describing something that moves on a quarterly
 *      earnings cycle. Reading the page gave no way to tell what was worth
 *      re-reading and what was the same as an hour ago.
 *
 * TAKEAWAYS ARE DESCRIPTIVE, NEVER DIRECTIVE. Every string a card passes here
 * says what is PRICED or what was MEASURED. None of them says what to do. That
 * is a house rule, not a style preference: the moment a panel says "fade this"
 * it has made a claim it cannot settle, and nothing on this page keeps score of
 * instructions.
 */

import { useCallback, useSyncExternalStore } from "react";
import Link from "next/link";

/* ─── persisted UI state ──────────────────────────────────────── */

/** Local pub/sub so a write in one component re-renders every reader of the
 *  same key in this tab. The native `storage` event fires only in OTHER tabs,
 *  so on its own it would leave the component that just wrote stale. */
const listeners = new Set<() => void>();

/** In-memory fallback for contexts where `localStorage` throws (private
 *  windows, embedded views, browsers set to block site data). Without it the
 *  toggle would be inert there — a write would fail, the re-read would return
 *  the default, and the band would snap straight back open. It does not
 *  survive a reload, which is the honest limit; it does make the control work. */
const memory = new Map<string, boolean>();

function notify() {
  for (const fn of listeners) fn();
}

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  // Cross-tab: collapsing a band in one tab should collapse it in the other.
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

/**
 * A boolean remembered in localStorage, safe under SSR and hydration.
 *
 * `useSyncExternalStore` rather than an effect that calls setState. localStorage
 * IS an external store, and this is what the hook exists for: the server render
 * uses `getServerSnapshot`, the client reads the real value after hydration, and
 * React reconciles the two without a mismatch warning and without the cascading
 * render an effect-plus-setState produces. The clock in the ES briefing uses the
 * same pattern for the same reason.
 *
 * Every access is wrapped. `localStorage` THROWS outright in some embedded and
 * privacy-restricted contexts rather than returning null, and a card that cannot
 * remember whether it was collapsed still has to render.
 */
export function useStickyBoolean(key: string, fallback: boolean) {
  const getSnapshot = useCallback(() => {
    try {
      const raw = window.localStorage.getItem(key);
      // Only the two values this hook writes are honoured. Anything else is
      // someone else's key or a corrupted entry, and the default is safer than
      // a coerced truthiness test.
      if (raw === "0") return false;
      if (raw === "1") return true;
    } catch {
      /* storage unavailable — fall through to the in-memory value */
    }
    return memory.get(key) ?? fallback;
  }, [key, fallback]);

  // Booleans compare by value, so a fresh read on every call is stable as far
  // as useSyncExternalStore is concerned — no snapshot caching needed.
  const value = useSyncExternalStore(subscribe, getSnapshot, () => fallback);

  const update = useCallback((next: boolean) => {
    // Written to memory first and unconditionally, so the control still
    // responds when persistence is unavailable.
    memory.set(key, next);
    try {
      window.localStorage.setItem(key, next ? "1" : "0");
    } catch {
      /* not persisted; the in-memory value above still drives this session */
    }
    notify();
  }, [key]);

  return [value, update] as const;
}

/* ─── a clock you can depend on ───────────────────────────────── */

/** Wall clock in whole minutes, as an external store.
 *
 *  WHY THIS EXISTS RATHER THAN A `Date.now()` CALL. Any "how old is this"
 *  figure computed inside a `useMemo` freezes: the memo only recomputes when
 *  its dependencies change, and time is not one of them. A board that last
 *  refreshed thirty minutes ago and has had no new data since would keep
 *  rendering the age it had when the data arrived — reporting "just now"
 *  indefinitely, which is worse than showing nothing, because the whole point
 *  of the figure is catching a card that stopped updating.
 *
 *  Depending on this value instead makes those memos pure and makes them tick.
 *  Same construction as the ES briefing's countdown clock, for the same reason.
 */
const clockListeners = new Set<() => void>();
let clockTimer: ReturnType<typeof setInterval> | null = null;

const CLOCK = {
  subscribe(onChange: () => void) {
    clockListeners.add(onChange);
    // ONE interval for every subscriber. Every card header on this page reads
    // the clock, and giving each its own timer would put a dozen 30-second
    // wake-ups on a page that needs one.
    if (clockTimer == null) {
      clockTimer = setInterval(() => {
        for (const fn of clockListeners) fn();
      }, 30_000);
    }
    // Nudge once on subscribe so the first real value lands as early as
    // possible rather than on the first 30-second tick.
    //
    // NOT a correctness fix — React does re-read `getSnapshot` after hydration
    // on its own, which is verifiable in production where this hook already
    // renders "oldest input 3m old" without it. It matters because
    // `getServerSnapshot` is null by design (nothing time-dependent may be
    // baked into cached HTML) and `fmtAgo` deliberately has no fallback, so
    // until the clock produces a number every relative age on the page is
    // blank. This closes that gap to one frame instead of leaving it to timing.
    const t = setTimeout(onChange, 0);
    return () => {
      clearTimeout(t);
      clockListeners.delete(onChange);
      if (clockListeners.size === 0 && clockTimer != null) {
        clearInterval(clockTimer);
        clockTimer = null;
      }
    };
  },
  getSnapshot: (): number => Math.floor(Date.now() / 60_000),
  // Null on the server: there is no "now" both renders agree on, and guessing
  // one is a hydration mismatch.
  getServerSnapshot: (): number | null => null,
};

export function useMinuteClock(): number | null {
  return useSyncExternalStore(CLOCK.subscribe, CLOCK.getSnapshot, CLOCK.getServerSnapshot);
}

/** Whole minutes between an epoch-ms stamp and `nowMin` (from `useMinuteClock`).
 *  Returns null before the clock has a value or when the stamp is unusable. */
export function minutesSince(updatedAt: number | undefined, nowMin: number | null): number | null {
  if (!updatedAt || nowMin == null) return null;
  const age = nowMin - Math.floor(updatedAt / 60_000);
  // A negative age is clock skew, not a stamp from the future.
  return age < 0 ? 0 : age;
}

/**
 * "3m ago" from an ISO instant — the one canonical implementation.
 *
 * WHY IT TAKES A CLOCK INSTEAD OF CALLING `Date.now()`. A RELATIVE time
 * rendered on the server is wrong by construction on this page: `revalidate`
 * makes the HTML edge-cacheable, so the document a browser hydrates was
 * rendered seconds — under stale-while-revalidate, much longer — before the
 * client recomputes the same string. The server said "12h ago" where the client
 * said "10h ago", which is React error #418 on every load, and a stale age
 * shown to the reader until the re-render lands.
 *
 * Returning "" while `nowMin` is null means the SERVER RENDERS NOTHING and the
 * browser fills every age in after hydration. There is then no server text to
 * disagree with. That is also why this cannot be "fixed" by formatting more
 * carefully — an absolute timestamp would still be formatted in the renderer's
 * timezone. The only thing that works is not rendering time on the server.
 *
 * The regex is load-bearing: the Trump monitor's timestamp is model-written and
 * routinely carries a trailing gloss ("2026-08-01T12:42:00Z (approx 3 hours ago
 * ET)") that `Date` rejects outright. An unparseable date does not THROW, it
 * yields NaN, and every comparison below would fall through to the last line —
 * which is where a literal "NaNd ago" on the card came from.
 */
export function fmtAgo(iso: string | null | undefined, nowMin: number | null): string {
  if (!iso || nowMin == null) return "";
  const m = String(iso).match(
    /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/
  );
  const t = new Date(m ? m[0] : iso).getTime();
  if (!Number.isFinite(t)) return "";
  const min = nowMin - Math.floor(t / 60_000);
  // A future stamp means clock skew or a bad parse, not a negative age.
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

/* ─── takeaway ────────────────────────────────────────────────── */

export type TakeawayTone = "neutral" | "warn" | "alert";

const TONE_CLASS: Record<TakeawayTone, string> = {
  neutral: "border-l-accent bg-accent/5",
  warn: "border-l-amber-400 bg-amber-500/10",
  alert: "border-l-loss bg-loss/10",
};

/**
 * The one-line reading a card owes its numbers.
 *
 * `headline` is the reading; `detail` is the arithmetic behind it. Both are
 * rendered — the detail is not a tooltip, because a caveat that only appears on
 * hover is a caveat the reader will act without.
 *
 * `absent` is the case that matters most and is easiest to get wrong: when
 * there is no reading to give, say THAT, in numbers. A takeaway block that
 * silently disappears when the data is thin looks exactly like a card whose
 * numbers were unremarkable.
 */
export function Takeaway({
  headline, detail, tone = "neutral", label = "Takeaway",
}: {
  headline: string;
  detail?: string | null;
  tone?: TakeawayTone;
  label?: string;
}) {
  return (
    <div className={`border-l-2 px-3 py-2 rounded-r ${TONE_CLASS[tone]}`}>
      <div className="text-[0.5rem] font-bold uppercase tracking-wider text-text-muted mb-0.5">
        {label}
      </div>
      <p className="text-xs text-text font-semibold leading-snug">{headline}</p>
      {detail && (
        <p className="text-[0.65rem] text-text-muted leading-snug mt-1">{detail}</p>
      )}
    </div>
  );
}

/* ─── card header with an honest age ──────────────────────────── */

/** `nowMin` comes from `useMinuteClock` rather than `Date.now()` so the age
 *  actually ticks. A card whose data has not changed does not re-render, so an
 *  age computed from a bare `Date.now()` at render time freezes at whatever it
 *  was when the data last arrived — and reads "just now" forever, on precisely
 *  the card that has stopped updating. */
function ago(
  ts: number | string | null | undefined,
  nowMin: number | null,
): { text: string; min: number } | null {
  if (ts == null || nowMin == null) return null;
  const t = typeof ts === "number" ? ts : Date.parse(String(ts));
  if (!Number.isFinite(t)) return null;
  const min = nowMin - Math.floor(t / 60_000);
  // A future stamp is clock skew or a bad parse, not a negative age.
  if (min <= 0) return { text: "just now", min: 0 };
  if (min < 60) return { text: `${min}m ago`, min };
  const hr = Math.floor(min / 60);
  if (hr < 24) return { text: `${hr}h ago`, min };
  return { text: `${Math.floor(hr / 24)}d ago`, min };
}

/**
 * Title, optional deep link, and how old the data is.
 *
 * `staleAfterMin` is per-card because "old" is not one number: a 45-minute-old
 * sector heatmap is stale, a 45-minute-old CAPE reading is brand new. Pass the
 * card's own refresh cadence and the header will flag anything materially past
 * it.
 */
export function CardHeader({
  title, href, hrefLabel = "Full →", asOf, staleAfterMin, right, size = "sm",
}: {
  title: string;
  href?: string;
  hrefLabel?: string;
  /** Epoch ms (react-query's `dataUpdatedAt`) or an ISO string from the payload.
   *  Prefer the payload's own `asof` where it exists — `dataUpdatedAt` is when
   *  WE fetched, which on a server-cached endpoint can be far younger than the
   *  data it returned. */
  asOf?: number | string | null;
  staleAfterMin?: number;
  right?: React.ReactNode;
  size?: "sm" | "md";
}) {
  const nowMin = useMinuteClock();
  const a = ago(asOf, nowMin);
  const stale = a != null && staleAfterMin != null && a.min > staleAfterMin;
  const Title = size === "md" ? "h2" : "h3";
  return (
    <div className="flex items-center justify-between gap-2 flex-wrap">
      <div className="flex items-baseline gap-2 min-w-0">
        <Title className={`${size === "md" ? "text-sm" : "text-xs"} font-bold uppercase tracking-wider text-accent`}>
          {title}
        </Title>
        {a && (
          <span
            className={`text-[0.55rem] tabular-nums ${stale ? "text-amber-400 font-semibold" : "text-text-muted"}`}
            title={
              stale
                ? `This card refreshes about every ${staleAfterMin} minutes and its data is ${a.min} minutes old — the upstream cache may not be updating.`
                : "Age of the data behind this card."
            }
          >
            {a.text}{stale ? " · stale" : ""}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {right}
        {href && (
          <Link href={href} className="text-[0.6rem] text-text-muted hover:text-accent">
            {hrefLabel}
          </Link>
        )}
      </div>
    </div>
  );
}

/* ─── a collapsible section INSIDE a card ─────────────────────── */

/**
 * One foldable block within a card, remembered per reader.
 *
 * WHY THE ES CARD NEEDED THIS. Measured, that card is 3,967px — 37% of the
 * whole page and about four screens — across eleven sections, of which two
 * were foldable. The horizon bands were applied to the page and never to the
 * single largest thing on it, so the reader got navigation everywhere except
 * where the scrolling actually is.
 *
 * Open by default, like the bands, for the same reason: folding work away by
 * default hides it. What this buys is that a reader who never looks at the
 * base-rate tables can fold them once and keep the card at a usable height.
 */
export function CardSection({
  id, title, subtitle, children, defaultOpen = true,
}: {
  id: string;
  title: string;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useStickyBoolean(`home.essec.${id}`, defaultOpen);
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex items-baseline gap-1.5 group text-left"
        >
          <span className={`text-[0.55rem] text-text-muted transition-transform ${open ? "rotate-90" : ""}`}>
            ▸
          </span>
          <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted group-hover:text-accent">
            {title}
          </h3>
          {!open && <span className="text-[0.55rem] text-text-muted/70">show</span>}
        </button>
        {open && subtitle}
      </div>
      {open && children}
    </div>
  );
}

/* ─── horizon band ────────────────────────────────────────────── */

/**
 * A collapsible group of cards that share an information half-life.
 *
 * The label is the point. "Weeks to months" over the RRG and the CTA board
 * tells a reader that nothing in there has changed since this morning, which
 * is the single most useful thing the page can say about a card it is asking
 * them to scroll past.
 *
 * Open by default, always. Collapsing is the reader's call and it is
 * remembered; defaulting a band shut would hide work behind a chevron.
 */
export function HorizonBand({
  id, label, hint, children, defaultOpen = true,
}: {
  id: string;
  label: string;
  hint: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useStickyBoolean(`home.band.${id}`, defaultOpen);
  return (
    <section className="space-y-3">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="w-full flex items-baseline gap-2 group text-left"
      >
        <span className={`text-[0.6rem] text-text-muted transition-transform ${open ? "rotate-90" : ""}`}>
          ▸
        </span>
        <span className="text-[0.6rem] font-bold uppercase tracking-[0.15em] text-text-muted group-hover:text-accent">
          {label}
        </span>
        <span className="text-[0.58rem] text-text-muted/70 truncate">{hint}</span>
        <span className="flex-1 border-t border-border ml-1" />
        {!open && (
          <span className="text-[0.55rem] text-text-muted/70 shrink-0">show</span>
        )}
      </button>
      {open && <div className="space-y-4">{children}</div>}
    </section>
  );
}
