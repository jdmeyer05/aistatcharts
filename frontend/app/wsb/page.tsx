"use client";

/**
 * WallStreetBets — ticker-mention surface.
 *
 * Backed by `/api/wsb/mentions` which scrapes r/wallstreetbets + r/options +
 * r/stocks using Reddit's public JSON endpoints. 15-min server cache means
 * the first cold visit waits ~15-25s; subsequent loads are instant.
 *
 * Tabs:
 *   Pulse         — top tickers by upvote-weighted mentions
 *   Momentum      — placeholder until we have day-over-day snapshot history
 *   Options Talk  — calls-vs-puts lean per ticker
 *   DD            — tickers with substantial DD posts, top linked
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchWsb, type WsbTicker } from "@/lib/api";
import { AIInterpretation } from "@/components/ai-interpretation";

type Tab = "Pulse" | "Options Talk" | "DD" | "Momentum";
const TABS: Tab[] = ["Pulse", "Options Talk", "DD", "Momentum"];

function sentimentBadge(score: number): { label: string; cls: string } {
  if (score >= 0.3) return { label: "Bullish", cls: "bg-gain/15 text-gain" };
  if (score <= -0.3) return { label: "Bearish", cls: "bg-loss/15 text-loss" };
  return { label: "Mixed", cls: "bg-surface-alt text-text-muted" };
}

function optionsBadge(lean: WsbTicker["options_lean"]): { label: string; cls: string } {
  switch (lean) {
    case "calls":
      return { label: "Calls lean", cls: "bg-gain/15 text-gain" };
    case "puts":
      return { label: "Puts lean", cls: "bg-loss/15 text-loss" };
    case "mixed":
      return { label: "Mixed flow", cls: "bg-accent/15 text-accent" };
    default:
      return { label: "No options", cls: "bg-surface-alt text-text-muted" };
  }
}

/* ─── Row rendering ───────────────────────────────────────────── */

function TickerRow({ t }: { t: WsbTicker }) {
  const s = sentimentBadge(t.sentiment);
  const o = optionsBadge(t.options_lean);
  const total = t.bull_score + t.bear_score;
  const bullPct = total > 0 ? (t.bull_score / total) * 100 : 50;

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <a
            href={`https://finance.yahoo.com/quote/${t.ticker}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-lg font-bold tabular-nums text-text hover:text-accent"
          >
            {t.ticker}
          </a>
          <span className={`text-[0.6rem] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded ${s.cls}`}>
            {s.label}
          </span>
          <span className={`text-[0.6rem] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded ${o.cls}`}>
            {o.label}
          </span>
          {t.dd_posts > 0 && (
            <span className="text-[0.6rem] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-spot/15 text-spot">
              {t.dd_posts} DD
            </span>
          )}
        </div>
        <div className="text-xs tabular-nums text-text-muted shrink-0">
          {t.mentions} mentions · {t.upvote_weighted.toLocaleString()} weighted
        </div>
      </div>

      {/* Bull/bear bar */}
      <div className="flex items-center gap-2 text-[0.6rem] tabular-nums">
        <span className="text-gain">{t.bull_score}</span>
        <div className="flex-1 h-1.5 bg-loss/40 rounded-full overflow-hidden">
          <div className="h-full bg-gain" style={{ width: `${bullPct}%` }} />
        </div>
        <span className="text-loss">{t.bear_score}</span>
      </div>

      {/* Options split */}
      {(t.calls_mentions > 0 || t.puts_mentions > 0) && (
        <div className="text-[0.65rem] text-text-muted tabular-nums">
          calls {t.calls_mentions} · puts {t.puts_mentions}
        </div>
      )}

      {t.top_post && (
        <a
          href={t.top_post.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block text-xs text-text-muted hover:text-accent border-t border-border pt-1.5 truncate"
          title={t.top_post.title}
        >
          r/{t.top_post.subreddit}
          {t.top_post.flair ? ` · ${t.top_post.flair}` : ""}
          {" · "}
          {t.top_post.ups.toLocaleString()}↑
          {" — "}
          {t.top_post.title}
        </a>
      )}
    </div>
  );
}

/* ─── Page ────────────────────────────────────────────────────── */

export default function WsbPage() {
  const [tab, setTab] = useState<Tab>("Pulse");

  const q = useQuery({
    queryKey: ["wsb-mentions"],
    queryFn: () => fetchWsb(false),
    staleTime: 10 * 60_000,
    refetchInterval: 15 * 60_000,
  });
  const data = q.data;
  const tickers = data?.tickers ?? [];

  const filtered = useMemo(() => {
    if (tab === "Pulse") {
      return tickers;
    }
    if (tab === "Options Talk") {
      return tickers
        .filter((t) => t.calls_mentions + t.puts_mentions > 0)
        .sort((a, b) => {
          // prioritize strong directional lean
          const aLean = Math.abs(a.calls_mentions - a.puts_mentions);
          const bLean = Math.abs(b.calls_mentions - b.puts_mentions);
          return bLean - aLean;
        });
    }
    if (tab === "DD") {
      return tickers
        .filter((t) => t.dd_posts > 0)
        .sort((a, b) => b.dd_posts - a.dd_posts);
    }
    return [];   // Momentum needs historical snapshots we don't have yet
  }, [tickers, tab]);

  const asOf = data?.as_of_utc ? new Date(data.as_of_utc).toLocaleString() : "";

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">WallStreetBets</h1>
        <p className="text-text-secondary text-sm mt-1">
          Ticker-mention surface for r/wallstreetbets + r/options + r/stocks.
          Ranked by upvote-weighted mentions with bull/bear sentiment, options
          lean, and DD post counts. 15-minute server cache.
        </p>
      </div>

      {/* AI interp — uses the shared /api/ai/interpret with a wsb payload.
          Falls back gracefully if PAGE_CONTEXT doesn't have a wsb entry yet. */}
      {tickers.length > 0 && (
        <AIInterpretation
          page="wsb"
          subject="r/wallstreetbets ticker pulse"
          data={{
            subreddits_scanned: data?.subreddits_scanned ?? [],
            post_count: data?.post_count ?? 0,
            top_tickers: tickers.slice(0, 15).map((t) => ({
              ticker: t.ticker,
              mentions: t.mentions,
              sentiment: t.sentiment,
              options_lean: t.options_lean,
              calls: t.calls_mentions,
              puts: t.puts_mentions,
              dd_posts: t.dd_posts,
            })),
          }}
        />
      )}

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {TABS.map((t) => {
          const active = tab === t;
          const count =
            t === "Pulse" ? tickers.length :
            t === "Options Talk" ? tickers.filter((x) => x.calls_mentions + x.puts_mentions > 0).length :
            t === "DD" ? tickers.filter((x) => x.dd_posts > 0).length :
            0;
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors rounded-t ${
                active ? "border-b-2 border-accent text-accent" : "text-text-muted hover:text-text"
              }`}
            >
              {t}
              {count > 0 && <span className="ml-1.5 text-[0.6rem] font-normal text-text-muted">{count}</span>}
            </button>
          );
        })}
        <div className="ml-auto text-[0.6rem] text-text-muted">
          {data?.post_count ? `scanned ${data.post_count} posts` : ""}
          {asOf ? ` · ${asOf}` : ""}
          {data?.cache_hit ? " · cached" : ""}
        </div>
      </div>

      {q.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm flex items-center justify-between">
          <span>WSB scan failed: {(q.error as Error)?.message ?? "unknown"}.</span>
          <button
            onClick={() => q.refetch()}
            className="px-3 py-1 text-xs rounded border border-loss hover:bg-loss/10"
          >
            Retry
          </button>
        </div>
      )}
      {q.isLoading && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Scanning Reddit (cold = ~20s)…</p>
        </div>
      )}

      {tab === "Momentum" && (
        <div className="card text-center py-10 text-sm text-text-muted">
          Momentum compares today&apos;s mention count against the 7-day average.
          Requires daily snapshots — this tab will populate after the WSB worker
          has logged ~7 days of history.
        </div>
      )}

      {!q.isLoading && tab !== "Momentum" && filtered.length === 0 && (
        <div className="card text-center py-10 text-sm text-text-muted">
          No tickers match this tab right now.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.slice(0, 30).map((t) => (
          <TickerRow key={t.ticker} t={t} />
        ))}
      </div>
    </div>
  );
}
