"use client";

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import { fetchInsiderTransactions } from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { fmtBn } from "../_shared/utils";
import { ErrorBanner } from "../_shared/error-banner";


interface InsiderRow {
  date: string;
  insider: string;
  position: string;
  transaction: string;
  shares: number;
  value: number;
  text: string;
  direction: "BUY" | "SELL" | "OTHER";
}

function classifyTransaction(txn: string, text: string): "BUY" | "SELL" | "OTHER" {
  // yfinance typically leaves Transaction blank and puts the action in Text
  // ("Sale at price X", "Purchase at price Y"). Combine both fields so we
  // classify consistently regardless of which one Yahoo populated.
  const haystack = `${txn} ${text}`.toLowerCase();
  if (!haystack.trim()) return "OTHER";
  if (haystack.includes("purchase") || haystack.includes(" buy") || haystack.startsWith("buy") || haystack.includes("acquire")) return "BUY";
  if (haystack.includes("sale") || haystack.includes(" sell") || haystack.startsWith("sell") || haystack.includes("dispose")) return "SELL";
  return "OTHER";
}

function toIso(d: string): string {
  if (!d || d === "NaT") return "";
  return d.slice(0, 10);
}

/** Cluster: 3+ distinct insiders with same direction within a 30-day window. */
function detectClusters(rows: InsiderRow[]): { start: string; end: string; direction: "BUY" | "SELL"; insiders: string[]; value: number }[] {
  if (rows.length === 0) return [];
  const sorted = [...rows]
    .filter((r) => r.date && (r.direction === "BUY" || r.direction === "SELL"))
    .sort((a, b) => a.date.localeCompare(b.date));

  const clusters: { start: string; end: string; direction: "BUY" | "SELL"; insiders: string[]; value: number }[] = [];
  for (const dir of ["BUY", "SELL"] as const) {
    const dirRows = sorted.filter((r) => r.direction === dir);
    if (dirRows.length < 3) continue;

    let i = 0;
    while (i < dirRows.length) {
      const windowStart = new Date(dirRows[i].date + "T00:00:00");
      const insiders = new Set<string>();
      let value = 0;
      let j = i;
      let lastDate = dirRows[i].date;
      while (j < dirRows.length) {
        const d = new Date(dirRows[j].date + "T00:00:00");
        const diffDays = (d.getTime() - windowStart.getTime()) / 86400000;
        if (diffDays > 30) break;
        insiders.add(dirRows[j].insider);
        value += dirRows[j].value || 0;
        lastDate = dirRows[j].date;
        j++;
      }
      if (insiders.size >= 3) {
        clusters.push({
          start: dirRows[i].date,
          end: lastDate,
          direction: dir,
          insiders: [...insiders],
          value,
        });
        i = j;
      } else {
        i++;
      }
    }
  }
  return clusters;
}

export default function InsidersPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [ticker, setTicker] = useState("");
  const load = useMutation({
    mutationFn: () => fetchInsiderTransactions(ticker.toUpperCase()),
  });

  const rows: InsiderRow[] = useMemo(() => {
    if (!load.data) return [];
    return load.data.data
      .map((raw) => {
        const r = raw as Record<string, unknown>;
        const txn = String(r.Transaction ?? "");
        const text = String(r.Text ?? "");
        return {
          date: toIso(String(r["Start Date"] ?? "")),
          insider: String(r.Insider ?? ""),
          position: String(r.Position ?? ""),
          transaction: txn || text,
          shares: Number(r.Shares ?? 0) || 0,
          value: Number(r.Value ?? 0) || 0,
          text,
          direction: classifyTransaction(txn, text),
        } satisfies InsiderRow;
      })
      .filter((r) => r.date);
  }, [load.data]);

  const { buys, sells, net, buyValue, sellValue, byInsider } = useMemo(() => {
    const buys = rows.filter((r) => r.direction === "BUY");
    const sells = rows.filter((r) => r.direction === "SELL");
    const buyValue = buys.reduce((s, r) => s + (r.value || 0), 0);
    const sellValue = sells.reduce((s, r) => s + (r.value || 0), 0);
    const net = buyValue - sellValue;

    const byInsider = new Map<string, { buys: number; sells: number; netValue: number; position: string }>();
    for (const r of rows) {
      const rec = byInsider.get(r.insider) ?? { buys: 0, sells: 0, netValue: 0, position: r.position };
      if (r.direction === "BUY") {
        rec.buys++;
        rec.netValue += r.value || 0;
      } else if (r.direction === "SELL") {
        rec.sells++;
        rec.netValue -= r.value || 0;
      }
      rec.position = rec.position || r.position;
      byInsider.set(r.insider, rec);
    }
    return { buys, sells, net, buyValue, sellValue, byInsider };
  }, [rows]);

  const clusters = useMemo(() => detectClusters(rows), [rows]);

  const timeline = useMemo(() => {
    // Bucket by week, separate buy/sell $ value.
    const m = new Map<string, { buy: number; sell: number }>();
    for (const r of rows) {
      const d = new Date(r.date + "T00:00:00");
      if (!Number.isFinite(d.getTime())) continue;
      const day = d.getUTCDay();
      const monday = new Date(d);
      monday.setUTCDate(d.getUTCDate() - (day === 0 ? 6 : day - 1));
      const key = monday.toISOString().slice(0, 10);
      const rec = m.get(key) ?? { buy: 0, sell: 0 };
      if (r.direction === "BUY") rec.buy += r.value || 0;
      if (r.direction === "SELL") rec.sell += r.value || 0;
      m.set(key, rec);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [rows]);

  const topInsiders = useMemo(() => {
    return [...byInsider.entries()]
      .filter(([, v]) => v.buys + v.sells > 0)
      .sort((a, b) => Math.abs(b[1].netValue) - Math.abs(a[1].netValue))
      .slice(0, 12);
  }, [byInsider]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Insider Activity (Form 4)</h1>
        <p className="text-text-secondary text-sm mt-1">
          Officers, directors, and 10% owners buying or selling their own stock. Cluster buys (3+ insiders in 30
          days) are one of the most-cited alpha signals in the literature.
        </p>
      </div>

      <div className="card border-l-4 border-l-warn">
        <div className="text-xs font-bold uppercase tracking-wider text-warn mb-1">How to read this</div>
        <p className="text-sm">
          <strong>Buys are more informative than sells.</strong> Insiders buy for one reason (they think the stock
          will go up). They sell for many — options exercises, 10b5-1 pre-scheduled plans, diversification, tax,
          estate planning. Weight cluster buys heavily; treat single-insider sells as mostly noise unless they break
          a long quiet period.
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[240px]">
            <label className="metric-label">Ticker</label>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && ticker && load.mutate()}
              placeholder="AAPL, NVDA, TSLA"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <button
            onClick={() => ticker && load.mutate()}
            disabled={!ticker || load.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {load.isPending ? "Loading…" : "Load Insider Activity"}
          </button>
        </div>
      </div>

      {load.isError && (
        <ErrorBanner title="Insider lookup failed" error={load.error} onRetry={() => ticker && load.mutate()} />
      )}

      {load.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {load.isSuccess && rows.length === 0 && (
        <div className="card text-sm text-text-muted py-6 px-5">
          No insider transactions found for &apos;{ticker}&apos;.
        </div>
      )}

      {rows.length > 0 && (
        <>
          <AIInterpretation
            page="insiders"
            subject={ticker.toUpperCase()}
            data={{
              ticker: ticker.toUpperCase(),
              window_days: 180,
              totals: {
                transactions: rows.length,
                buys: buys.length,
                sells: sells.length,
                buy_value: buyValue,
                sell_value: sellValue,
                net_value: net,
              },
              clusters: {
                buy_cluster_detected: clusters.some((c) => c.direction === "BUY"),
                sell_cluster_detected: clusters.some((c) => c.direction === "SELL"),
                clusters: clusters.map((c) => ({
                  direction: c.direction,
                  start: c.start,
                  end: c.end,
                  distinct_insiders: c.insiders.length,
                  total_value: c.value,
                })),
              },
              top_insiders_by_net: topInsiders.slice(0, 6).map(([name, v]) => ({
                name,
                position: v.position,
                net_value: v.netValue,
                buys: v.buys,
                sells: v.sells,
              })),
            }}
          />
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Total Transactions" value={String(rows.length)} />
              <Metric label="Buys" value={String(buys.length)} deltaType="gain" />
              <Metric label="Sells" value={String(sells.length)} deltaType="loss" />
              <Metric label="Buy Value" value={fmtBn(buyValue)} deltaType="gain" />
              <Metric label="Sell Value" value={fmtBn(sellValue)} deltaType="loss" />
              <Metric
                label="Net $ Flow"
                value={fmtBn(net)}
                deltaType={net > 0 ? "gain" : net < 0 ? "loss" : "neutral"}
              />
            </div>
          </div>

          {clusters.length > 0 && (
            <div className="card border-l-4 border-l-accent">
              <div className="text-sm font-bold mb-2">Cluster activity detected</div>
              <div className="text-xs text-text-muted mb-3">
                3+ distinct insiders acting in the same direction within a 30-day window.
              </div>
              <div className="space-y-2">
                {clusters.map((c, i) => (
                  <div
                    key={i}
                    className="p-2.5 rounded border border-border"
                    style={{
                      borderLeft: `3px solid ${c.direction === "BUY" ? t.gain : t.loss}`,
                      background: c.direction === "BUY" ? "rgba(63,185,80,0.05)" : "rgba(248,81,73,0.05)",
                    }}
                  >
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span
                        className="px-1.5 py-0.5 rounded text-xs font-bold"
                        style={{
                          background: c.direction === "BUY" ? t.gain : t.loss,
                          color: "#000",
                        }}
                      >
                        CLUSTER {c.direction}
                      </span>
                      <span className="text-sm font-semibold">
                        {c.insiders.length} insiders, {fmtBn(c.value)} total
                      </span>
                      <span className="text-xs text-text-muted">
                        {c.start} → {c.end}
                      </span>
                    </div>
                    <div className="text-xs text-text-muted mt-1">
                      {c.insiders.slice(0, 6).join(", ")}
                      {c.insiders.length > 6 ? ` + ${c.insiders.length - 6} more` : ""}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {timeline.length > 1 && (
            <div className="card">
              <Plot
                data={[
                  {
                    type: "bar" as const,
                    name: "Buys ($)",
                    x: timeline.map(([d]) => d),
                    y: timeline.map(([, v]) => v.buy / 1e6),
                    marker: { color: t.gain },
                  },
                  {
                    type: "bar" as const,
                    name: "Sells ($)",
                    x: timeline.map(([d]) => d),
                    y: timeline.map(([, v]) => -v.sell / 1e6),
                    marker: { color: t.loss },
                  },
                ]}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.normal,
                  title: { text: "Insider $ Flow by Week ($M, sells shown negative)", font: { size: 13, color: t.text } },
                  yaxis: { title: { text: "$M" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  barmode: "relative" as const,
                  legend: { orientation: "h" as const, y: -0.18 },
                  margin: { l: 50, r: 20, t: 40, b: 50 },
                  shapes: [
                    {
                      type: "line",
                      xref: "paper",
                      x0: 0,
                      x1: 1,
                      y0: 0,
                      y1: 0,
                      line: { color: t.muted, width: 1 },
                    },
                  ],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          {topInsiders.length > 0 && (
            <div className="card">
              <Plot
                data={[
                  {
                    type: "bar" as const,
                    orientation: "h" as const,
                    y: topInsiders.map(([name]) => name),
                    x: topInsiders.map(([, v]) => v.netValue / 1e6),
                    marker: {
                      color: topInsiders.map(([, v]) => (v.netValue >= 0 ? t.gain : t.loss)),
                    },
                    text: topInsiders.map(([, v]) => `${v.buys}B / ${v.sells}S`),
                    textposition: "outside" as const,
                  },
                ]}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.tall,
                  title: { text: "Top Insiders by Net $ Flow ($M)", font: { size: 13, color: t.text } },
                  xaxis: { title: { text: "Net $M (positive = net buying)" }, gridcolor: t.grid },
                  yaxis: { gridcolor: t.grid, autorange: "reversed" },
                  margin: { l: 200, r: 60, t: 40, b: 40 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          <div className="card">
            <div className="font-semibold text-sm mb-2">All Transactions</div>
            <div className="overflow-x-auto max-h-[500px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">Date</th>
                    <th className="text-left py-1.5 px-2">Insider</th>
                    <th className="text-left py-1.5 px-2">Position</th>
                    <th className="text-left py-1.5 px-2">Direction</th>
                    <th className="text-right py-1.5 px-2">Shares</th>
                    <th className="text-right py-1.5 px-2">Value</th>
                    <th className="text-left py-1.5 px-2">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2">{r.date}</td>
                      <td className="py-1 px-2 font-semibold">{r.insider || "—"}</td>
                      <td className="py-1 px-2 text-text-muted">{r.position || "—"}</td>
                      <td className="py-1 px-2">
                        <span
                          className={
                            r.direction === "BUY"
                              ? "text-gain"
                              : r.direction === "SELL"
                                ? "text-loss"
                                : "text-text-muted"
                          }
                        >
                          {r.direction}
                        </span>
                      </td>
                      <td className="py-1 px-2 text-right">{r.shares ? r.shares.toLocaleString() : "—"}</td>
                      <td className="py-1 px-2 text-right">{r.value ? fmtBn(r.value) : "—"}</td>
                      <td className="py-1 px-2 text-text-muted">{r.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <div className="card card-compact text-xs text-text-muted">
        <strong>Research context:</strong> Academic literature (Seyhun 1986, Lakonishok & Lee 2001, Cohen et al. 2012) finds
        insider cluster buying predicts ~6-12% excess returns over 12 months. The signal decays beyond 12 months. Routine
        option exercises and 10b5-1 planned sales should be weighted less heavily than discretionary open-market trades.
      </div>
    </div>
  );
}
