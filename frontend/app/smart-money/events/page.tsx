"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import {
  fetch8KEvents,
  fetchGuidanceHistory,
  fetchTranscriptUrls,
  fetchTranscriptGuidance,
  fetchEdgarEarningsCalendar,
  type EightKEvent,
  type GuidanceRow,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { fmtBn, shortDate, EIGHT_K_ITEM_NAMES } from "../_shared/utils";
import { ErrorBanner } from "../_shared/error-banner";


export default function MaterialEventsPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [ticker, setTicker] = useState("");
  const [days, setDays] = useState(30);
  const [quarters, setQuarters] = useState(6);
  const [transcriptUrls, setTranscriptUrls] = useState("");
  const [calDays, setCalDays] = useState(7);

  const eightK = useMutation({
    mutationFn: () => fetch8KEvents(ticker.toUpperCase(), days),
  });

  const guidance = useMutation({
    mutationFn: async () => {
      const tk = ticker.trim().toUpperCase();
      if (!tk) throw new Error("Enter a ticker");

      // Transcript URL discovery scrapes Motley Fool and is flaky — it
      // should NEVER block the press-release guidance path. Catch any
      // failure (timeout, 429, 500) and fall back to zero transcripts
      // so the user still gets the 8-K guidance they're waiting on.
      const urlsP: Promise<{ urls: string[] }> = transcriptUrls.trim()
        ? Promise.resolve({
            urls: transcriptUrls.split("\n").map((s) => s.trim()).filter((u) => u.startsWith("http")),
          })
        : fetchTranscriptUrls(tk, 4)
            .then((r) => ({ urls: r.urls }))
            .catch((e) => {
              console.warn("transcript URL discovery failed — skipping transcripts", e);
              return { urls: [] as string[] };
            });

      const [pressRes, discovered] = await Promise.all([
        fetchGuidanceHistory(tk, quarters),
        urlsP,
      ]);

      let callRows: GuidanceRow[] = [];
      let transcriptError: string | null = null;
      if (discovered.urls.length > 0) {
        try {
          const res = await fetchTranscriptGuidance(tk, discovered.urls);
          callRows = res.data;
        } catch (e) {
          transcriptError = (e as Error)?.message ?? "transcript parse failed";
          console.warn("transcript guidance failed", e);
        }
      }
      return {
        ticker: tk,
        press: pressRes.data.map((r) => ({ ...r, source: "8-K Press Release" as const })),
        call: callRows.map((r) => ({ ...r, source: "Earnings Call" as const })),
        discoveredUrls: discovered.urls,
        transcriptError,
      };
    },
  });

  const calQ = useQuery({
    queryKey: ["events-earnings-cal", calDays],
    queryFn: () => fetchEdgarEarningsCalendar(calDays),
    staleTime: 10 * 60 * 1000,
  });

  const events: EightKEvent[] = eightK.data?.data ?? [];

  const combined = useMemo(() => {
    if (!guidance.data) return [];
    const all = [...guidance.data.press, ...guidance.data.call];
    return all.sort((a, b) => (a.filed ?? "").localeCompare(b.filed ?? ""));
  }, [guidance.data]);

  const revTrend = combined.filter((r) => r.revenue != null);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Material Events</h1>
        <p className="text-text-secondary text-sm mt-1">
          8-K filings and forward guidance from press releases + earnings calls. Filed within days of the event.
        </p>
      </div>

      {/* Lookup */}
      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="metric-label">Ticker</label>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && ticker && eightK.mutate()}
              placeholder="AAPL"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <div className="min-w-[180px]">
            <label className="metric-label">Lookback: {days} days</label>
            <input
              type="range"
              min={7}
              max={365}
              value={days}
              onChange={(e) => setDays(parseInt(e.target.value))}
              className="w-full mt-1"
            />
          </div>
          <div>
            <label className="metric-label">Guidance quarters</label>
            <select
              value={quarters}
              onChange={(e) => setQuarters(parseInt(e.target.value))}
              className="mt-0.5 px-2 py-1.5 border border-border rounded text-sm bg-surface"
            >
              {[4, 6, 8, 10].map((q) => (
                <option key={q} value={q}>
                  {q}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => {
              if (!ticker) return;
              eightK.mutate();
              guidance.mutate();
            }}
            disabled={!ticker || eightK.isPending || guidance.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {eightK.isPending || guidance.isPending ? "Searching…" : "Search 8-K + Guidance"}
          </button>
        </div>
        <div className="mt-3">
          <label className="metric-label">
            Motley Fool transcript URLs (optional — one per line; auto-discovered if blank)
          </label>
          <textarea
            value={transcriptUrls}
            onChange={(e) => setTranscriptUrls(e.target.value)}
            rows={2}
            placeholder="https://www.fool.com/earnings/call-transcripts/…"
            className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-xs bg-surface font-data"
          />
        </div>
      </div>

      {/* 8-K events */}
      {eightK.isError && (
        <ErrorBanner title="8-K lookup failed" error={eightK.error} onRetry={() => ticker && eightK.mutate()} />
      )}

      {eightK.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-3">Loading 8-K filings…</div>
        </div>
      )}

      {eightK.isSuccess && events.length === 0 && (
        <div className="card text-sm text-text-muted py-6 px-5">
          No 8-K filings found for &apos;{ticker}&apos; in the last {days} days.
        </div>
      )}

      {events.length > 0 && (
        <div className="card">
          <div className="text-sm mb-3">
            Found <b>{events.length}</b> 8-K filings for <b>{ticker}</b>.
          </div>
          <div className="space-y-1.5 max-h-[420px] overflow-y-auto">
            {events.map((evt, i) => {
              const itemsList = evt.items ? evt.items.split(",").map((s) => s.trim()).filter(Boolean) : [];
              const desc = itemsList
                .filter((code) => code !== "9.01")
                .map((code) => EIGHT_K_ITEM_NAMES[code] ?? code)
                .join(", ");
              return (
                <div
                  key={i}
                  className="p-2 rounded"
                  style={{
                    borderLeft: `2px solid ${t.accent}`,
                    background: "rgba(255,255,255,0.02)",
                  }}
                >
                  <span className="text-xs text-text-muted font-data">{evt.filed}</span>
                  <span className="mx-2 text-xs font-bold" style={{ color: t.accent }}>
                    8-K
                  </span>
                  {desc && <span className="text-xs">{desc}</span>}
                  {evt.url && (
                    <a
                      href={evt.url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-2 text-xs text-accent hover:underline"
                    >
                      Filing →
                    </a>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Guidance */}
      {guidance.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-3">Parsing guidance from 8-Ks and earnings calls…</div>
        </div>
      )}

      {guidance.isError && (
        <div className="card text-sm text-loss py-4 px-5 border-l-4 border-l-loss">
          Guidance lookup failed: {(guidance.error as Error)?.message ?? "unknown error"}. 8-K filings above are
          unaffected.
        </div>
      )}

      {guidance.data && combined.length === 0 && (
        <div className="card text-sm text-text-muted py-4 px-5">
          No structured guidance parsed from {guidance.data.ticker} press releases.
          {guidance.data.discoveredUrls.length === 0 && (
            <> To add earnings-call data, paste Motley Fool transcript URLs above and re-run.</>
          )}
        </div>
      )}

      {guidance.data && guidance.data.transcriptError && (
        <div className="card text-xs text-text-muted py-2 px-4 border-l-2 border-l-warn">
          <strong className="text-warn">Transcript parse skipped:</strong> {guidance.data.transcriptError}.
          {" "}8-K data above is complete — earnings-call guidance requires Motley Fool transcripts which
          didn&apos;t load in time. Paste URLs manually above to retry just the transcript step.
        </div>
      )}

      {combined.length > 0 && guidance.data && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Ticker" value={guidance.data.ticker} />
              <Metric label="From 8-K" value={String(guidance.data.press.length)} />
              <Metric label="From Earnings Calls" value={String(guidance.data.call.length)} />
            </div>
          </div>

          {revTrend.length > 0 && (
            <div className="card">
              <Plot
                data={(["8-K Press Release", "Earnings Call"] as const).flatMap((src, si) => {
                  const sub = revTrend.filter((r) => (r as GuidanceRow & { source: string }).source === src);
                  if (sub.length === 0) return [];
                  const color = si === 0 ? t.accent : t.spot;
                  const xs = sub.map((r) => r.quarter ?? shortDate(r.filed));
                  return [
                    {
                      x: xs,
                      y: sub.map((r) => (r.revenue ?? 0) / 1e9),
                      type: "scatter" as const,
                      mode: "lines+markers" as const,
                      name: `Revenue (${src})`,
                      line: { color, width: 3, dash: si === 0 ? ("solid" as const) : ("dash" as const) },
                      marker: { size: 10 },
                    },
                  ];
                })}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.normal,
                  title: {
                    text: `${guidance.data.ticker} — Revenue Guidance Trend ($B)`,
                    font: { size: 14, color: t.text },
                  },
                  yaxis: { title: { text: "Revenue ($B)" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  legend: { orientation: "h" as const, y: -0.18 },
                  margin: { l: 60, r: 20, t: 40, b: 60 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          <div className="card">
            <div className="font-semibold text-sm mb-2">Guidance History</div>
            <div className="overflow-x-auto max-h-[420px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    {["Filed", "Source", "Quarter", "Revenue", "Gross Margin", "EPS", "OpEx"].map((h) => (
                      <th key={h} className="text-left py-1.5 px-2">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {combined.map((r, i) => {
                    const rowWithSrc = r as GuidanceRow & { source: string };
                    const rev =
                      r.revenue != null
                        ? r.revenue_high != null && r.revenue_high !== r.revenue
                          ? `${fmtBn(r.revenue)} – ${fmtBn(r.revenue_high)}`
                          : fmtBn(r.revenue)
                        : r.revenue_growth_low != null
                          ? `+${r.revenue_growth_low.toFixed(0)}% – +${(r.revenue_growth_high ?? 0).toFixed(0)}% YoY`
                          : "";
                    return (
                      <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                        <td className="py-1 px-2">{shortDate(r.filed)}</td>
                        <td className="py-1 px-2">{rowWithSrc.source}</td>
                        <td className="py-1 px-2">{r.quarter ?? ""}</td>
                        <td className="py-1 px-2">{rev}</td>
                        <td className="py-1 px-2">
                          {r.gross_margin != null ? `${r.gross_margin.toFixed(1)}%` : ""}
                        </td>
                        <td className="py-1 px-2">{r.eps != null ? `$${r.eps.toFixed(2)}` : ""}</td>
                        <td className="py-1 px-2">{r.opex != null ? fmtBn(r.opex) : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <details className="card">
            <summary className="cursor-pointer text-sm font-semibold">Raw Outlook Text</summary>
            <div className="mt-3 space-y-3">
              {combined.map((r, i) => {
                if (!r.outlook) return null;
                const src = (r as GuidanceRow & { source: string }).source === "8-K Press Release" ? "8-K" : "Call";
                return (
                  <div key={i} className="border-b border-border/50 pb-3 last:border-0">
                    <div className="text-xs font-semibold mb-1">
                      [{src}] {r.quarter ?? shortDate(r.filed)}
                    </div>
                    <div className="text-xs text-text-muted whitespace-pre-wrap">{r.outlook.slice(0, 600)}</div>
                  </div>
                );
              })}
            </div>
          </details>
        </>
      )}

      {/* Earnings calendar standalone */}
      <div className="card">
        <div className="flex items-baseline justify-between flex-wrap gap-2 mb-2">
          <div className="font-semibold text-sm">Recent Earnings Releases (market-wide)</div>
          <select
            value={calDays}
            onChange={(e) => setCalDays(parseInt(e.target.value))}
            className="px-2 py-1 border border-border rounded text-xs bg-surface"
          >
            {[3, 7, 14, 30].map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </select>
        </div>
        {calQ.isPending ? (
          <div className="text-xs text-text-muted">Loading…</div>
        ) : calQ.data && calQ.data.count > 0 ? (
          <>
            <div className="text-xs text-text-muted mb-2">
              {calQ.data.count} earnings releases in the last {calDays} days.
            </div>
            <div className="overflow-x-auto max-h-[350px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">Filed</th>
                    <th className="text-left py-1.5 px-2">Ticker</th>
                    <th className="text-left py-1.5 px-2">Company</th>
                  </tr>
                </thead>
                <tbody>
                  {calQ.data.data.map((row, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2">{shortDate(row.filed)}</td>
                      <td className="py-1 px-2 font-bold">{row.ticker}</td>
                      <td className="py-1 px-2">{row.company}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div className="text-xs text-text-muted">No earnings releases found in the last {calDays} days.</div>
        )}
      </div>
    </div>
  );
}
