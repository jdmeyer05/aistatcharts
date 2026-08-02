"use client";

/**
 * Home — real-time market dashboard (client island).
 *
 * Layout (desktop, stacks on mobile):
 *   1. Market Pulse Strip       (30s refetch)
 *   2. ES Session Briefing      (3 min refetch — levels develop intraday)
 *   3. What's Driving Markets   (5 min refetch, backend caches 15 min)
 *   4. Sector Relative  |  Vol Landscape Snapshot     (60s / 5min)
 *   5. S&P Valuation strip                             (60 min refetch)
 *   6. Sector Rotation  |  CTA Positioning             (30 min / 30 min)
 *   7. Macro Pressure — equity-impact scorecard        (30 min refetch)
 *   8. News             |  Trump / Tweet Watch         (derived / 2min)
 *   9. Macro Calendar — next 14 days                   (10 min refetch)
 *
 * The briefing leads because this page is used as an intraday ES cockpit:
 * it is the only card scoped to the current session, and everything below it
 * runs on a swing horizon.
 *
 * The page shell is a Server Component (`app/page.tsx`) which prefetches
 * all eleven endpoints in parallel and ships dehydrated query state via
 * HydrationBoundary. This component picks up the cache instantly on
 * hydration — no fetch waterfall on first paint — then refetches on its
 * normal cadence in the background.
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
} from "@/lib/api";
import { PULSE_TICKERS, PULSE_LABELS } from "@/lib/home-constants";
import EsBriefing from "@/components/home/es-briefing";
import PageInterpretation from "@/components/home/page-interpretation";
import CtaFlows from "@/components/home/cta-flows";
import MacroPressure from "@/components/home/macro-pressure";
import SectorRrgCard from "@/components/home/sector-rrg";
import SpValuationStrip from "@/components/home/sp-valuation";

function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  // The try/catch here caught nothing useful: an unparseable date does not
  // THROW, it yields NaN, and every comparison below then falls through to the
  // last line — which rendered a literal "NaNd ago" on the card. The Trump
  // monitor's timestamp is model-written and routinely carries a trailing gloss
  // ("2026-08-01T12:42:00Z (approx 3 hours ago ET)") that Date rejects outright,
  // so pull the ISO prefix out when there is one, and refuse to print anything
  // when there genuinely is no valid instant.
  const m = String(iso).match(
    /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/
  );
  const t = new Date(m ? m[0] : iso).getTime();
  if (!Number.isFinite(t)) return "";
  const min = Math.floor((Date.now() - t) / 60000);
  // A future stamp means clock skew or a bad parse, not a negative age.
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

function pctClass(n: number | undefined | null): string {
  if (n == null || n === 0) return "text-text-muted";
  return n > 0 ? "text-gain" : "text-loss";
}

/* ─── Market Pulse Strip ──────────────────────────────────────── */

function MarketPulse() {
  const q = useQuery({
    queryKey: ["pulse", PULSE_TICKERS.join(",")],
    queryFn: () => fetchSnapshot([...PULSE_TICKERS]),
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
  // `change` is genuinely optional: when no prior close can be established the
  // backend omits it rather than publishing a confident 0.00%. The render below
  // already treats null as "no percentage to show".
  const data: Record<string, { price: number; change?: number; prev_close?: number }> = q.data ?? {};

  return (
    <div className="card card-compact">
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
        <div className="ml-auto text-[0.6rem] text-text-muted">
          {q.isFetching ? "updating…" : q.dataUpdatedAt ? `as of ${new Date(q.dataUpdatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : ""}
        </div>
      </div>
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
  const q = useQuery({
    queryKey: ["market-driver"],
    queryFn: fetchMarketDriver,
    refetchInterval: 5 * 60_000,
    staleTime: 3 * 60_000,
  });
  const d = q.data;
  const asOf = d?.as_of_utc ? fmtAgo(d.as_of_utc) : "";

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
            {d?.confidence != null ? `  ·  conf ${d.confidence}/10` : ""}
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

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Sector Relative</h3>
        <Link href="/sector-analysis" className="text-[0.6rem] text-text-muted hover:text-accent">Full →</Link>
      </div>
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
    </div>
  );
}

/* ─── Vol Landscape Snapshot ──────────────────────────────────── */

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

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Vol Landscape</h3>
        <Link href="/vol-landscape" className="text-[0.6rem] text-text-muted hover:text-accent">Full →</Link>
      </div>

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
              </span>
              <span title="Implied over realised. Above 1 means options are pricing more movement than has been delivered.">
                IV/HV <span className="text-text">{s.avg_ivhv?.toFixed(2)}</span>
              </span>
              <span title="Names whose term structure is inverted — front vol above back vol, which prices near-term event risk.">
                <span className="text-text">{s.n_inverted}</span> inverted
                <span className="text-text-muted/70"> of {s.n_tickers}</span>
              </span>
              {/* Separate denominators on purpose. Skew is counted only over
                  chains that pass put-call parity, so a shared "of 20" would
                  overstate it — the two numbers are no longer out of the same
                  pool and cannot share a label. */}
              <span title="Names with unusually steep put skew. Counted only over chains whose ATM put and ATM call agree to within put-call parity — a chain quoting stale wings gets no vote.">
                <span className="text-text">{s.n_steep_skew}</span> steep skew
                <span className="text-text-muted/70"> of {s.n_skew_rated ?? s.n_tickers}</span>
              </span>
            </div>
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
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Market-Moving News</h3>
      </div>
      {newsItems.length === 0 ? (
        <p className="text-xs text-text-muted">No market-moving headlines surfaced in this cycle.</p>
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
            <span>·</span>
            <span>{fmtAgo(latest.timestamp)}</span>
            <span className={`ml-auto ${sentimentColor(latest.sentiment)}`}>{latest.sentiment}</span>
          </div>
          <p className="text-sm text-text leading-snug line-clamp-4">{latest.text}</p>
          {latest.interpretation && (
            <p className="text-xs text-text-muted leading-snug border-t border-border pt-1.5">
              {latest.interpretation}
            </p>
          )}
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

function MacroCalendar() {
  const q = useQuery({
    queryKey: ["events-home"],
    queryFn: fetchEvents,
    refetchInterval: 10 * 60_000,
    staleTime: 9 * 60_000,
  });
  // Take EVERY high-impact print first, then fill with the nearest of the rest.
  // A plain .slice(0, 6) on a date-ordered list dropped Nonfarm payrolls and CPI
  // — the two widest-range sessions of the month — because they sit furthest
  // out, so a card titled "Next 2 Weeks" was omitting the only two that change
  // how you size. Order is restored by date for display.
  const events = useMemo(() => {
    const all = q.data?.events ?? [];
    const high = all.filter((e) => e.impact === "high");
    const rest = all.filter((e) => e.impact !== "high");
    return [...high, ...rest.slice(0, Math.max(0, 8 - high.length))]
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  }, [q.data?.events]);

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Next 2 Weeks — Macro Calendar</h3>
        <Link href="/economic-calendar" className="text-[0.6rem] text-text-muted hover:text-accent">Full →</Link>
      </div>
      {events.length === 0 ? (
        <p className="text-xs text-text-muted">No scheduled events in window.</p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
          {events.map((ev, i) => (
            <div key={i} className="flex flex-col text-xs">
              <span className="text-text-muted text-[0.6rem] tabular-nums">
                {ev.date} · {ev.days_away === 0 ? "today" : `+${ev.days_away}d`}
              </span>
              <span className={`font-semibold truncate ${ev.impact === "high" ? "text-text" : "text-text-muted"}`}
                    title={`${ev.name}${ev.impact ? ` — ${ev.impact} impact` : ""}`}>
                {ev.impact === "high" && <span className="text-spot mr-0.5">•</span>}
                {ev.name}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Page ────────────────────────────────────────────────────── */

export default function HomeClient() {
  const driverQ = useQuery({
    queryKey: ["market-driver"],
    queryFn: fetchMarketDriver,
    refetchInterval: 5 * 60_000,
    staleTime: 3 * 60_000,
  });

  return (
    <div className="space-y-4">
      <MarketPulse />
      <EsBriefing />
      {/* One interpretation for the whole page, sitting under the ES card
          because that is the page's spine. It reads every card's cached data,
          so it is placed high but stays collapsed until asked. */}
      <PageInterpretation />
      <MarketDriverCard />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectorRelative />
        <VolLandscapeSnapshot />
      </div>
      <SpValuationStrip />
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <SectorRrgCard />
        <CtaFlows />
      </div>
      <MacroPressure />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <NewsPanel citations={driverQ.data?.citations} />
        <TweetWatch />
      </div>
      <MacroCalendar />
    </div>
  );
}
