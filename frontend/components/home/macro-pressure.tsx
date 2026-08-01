"use client";

/**
 * Macro pressure scorecard — what the macro backdrop is doing to equities.
 *
 * Each row is scored arithmetically: the z-score of the factor's recent change,
 * flipped by a sign convention for whether RISING hurts equities. Positive =
 * supportive. Nothing here is a judgement call, so any verdict can be traced to
 * the number beside it.
 *
 * Scored on CHANGE rather than level, because a level that has been elevated
 * for a year is already in the price; the delta is what moves equities. Level
 * percentile is shown as context, deliberately not as the verdict.
 */

import Link from "next/link";
import { Fragment, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchMacroPressure,
  type MacroPressureBoard,
  type MacroFactorRow,
  type MacroVerdict,
} from "@/lib/api";

function verdictClass(v: MacroVerdict): string {
  switch (v) {
    case "supportive": return "text-gain";
    case "headwind": return "text-loss";
    default: return "text-text-muted";
  }
}

function verdictDot(v: MacroVerdict): string {
  switch (v) {
    case "supportive": return "bg-gain";
    case "headwind": return "bg-loss";
    default: return "bg-text-muted/40";
  }
}

function netClass(label?: string): string {
  if (!label) return "bg-border text-text-muted";
  if (label.includes("negative")) return "bg-loss/15 text-loss";
  if (label.includes("supportive")) return "bg-gain/15 text-gain";
  return "bg-accent/15 text-accent";
}

function fmtLevel(r: MacroFactorRow): string {
  const n = r.display_level;
  const v = Math.abs(n) >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 0 })
                                : n.toFixed(2);
  return r.display_unit === "$T" ? `$${v}T` : `${v}${r.display_unit}`;
}

/** Change is in the factor's own units — points for rates and spreads,
 *  percent for price-like series. Suffix accordingly or the number lies. */
function fmtChange(r: MacroFactorRow): string {
  const sign = r.change > 0 ? "+" : "";
  return r.change_mode === "pct"
    ? `${sign}${r.change.toFixed(1)}%`
    : `${sign}${r.change.toFixed(2)}`;
}

export default function MacroPressure() {
  const q = useQuery<MacroPressureBoard>({
    // Inputs are daily-cadence at best (several weekly/monthly) and the server
    // holds the board 45 min, so polling faster just re-serves the same object.
    queryKey: ["macro-pressure"],
    queryFn: fetchMacroPressure,
    refetchInterval: 30 * 60_000,
    staleTime: 25 * 60_000,
  });

  const d = q.data;

  const grouped = useMemo(() => {
    if (!d?.available || !d.rows) return [];
    const order = d.group_order ?? [];
    const byGroup = new Map<string, MacroFactorRow[]>();
    for (const r of d.rows) {
      if (!byGroup.has(r.group)) byGroup.set(r.group, []);
      byGroup.get(r.group)!.push(r);
    }
    // Follow the server's declared order, then append any group it didn't list
    // so a newly-added factor can't silently vanish from the table.
    const seen = new Set<string>();
    const out: [string, MacroFactorRow[]][] = [];
    for (const g of order) {
      if (byGroup.has(g)) { out.push([g, byGroup.get(g)!]); seen.add(g); }
    }
    for (const [g, rows] of byGroup) if (!seen.has(g)) out.push([g, rows]);
    return out;
  }, [d]);

  // Split into two balanced columns. A single 12-row table stretched the whole
  // card width, leaving a canyon of dead space between the label and its
  // numbers; two columns halve the height and pull each number back toward the
  // row it belongs to. Balanced on total ROWS rather than group count so the
  // columns end up the same height.
  const columns = useMemo<[string, MacroFactorRow[]][][]>(() => {
    const total = grouped.reduce((n, [, rows]) => n + rows.length + 1, 0);
    const left: [string, MacroFactorRow[]][] = [];
    const right: [string, MacroFactorRow[]][] = [];
    let acc = 0;
    for (const entry of grouped) {
      // +1 for the group heading row.
      if (acc < total / 2 || left.length === 0) { left.push(entry); acc += entry[1].length + 1; }
      else right.push(entry);
    }
    return right.length ? [left, right] : [left];
  }, [grouped]);

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-sm font-bold uppercase tracking-wider text-accent">
              Macro Pressure — Equities
            </h2>
            {d?.available && (
              <span
                className={`px-2 py-0.5 rounded text-[0.6rem] font-bold uppercase ${netClass(d.net_label)}`}
                title={
                  d.net_from_n != null && d.net_total_n != null
                    ? `Mean score ${d.net_score?.toFixed(2)} across the ${d.net_from_n} of ${d.net_total_n} factors that reported.` +
                      (d.net_excluded_stale
                        ? ` ${d.net_excluded_stale} stale ${d.net_excluded_stale === 1 ? "series is" : "series are"} excluded — a flat reading there means no new data, not no pressure.`
                        : "")
                    : `Mean factor score ${d.net_score?.toFixed(2)}.`
                }
              >
                Net: {d.net_label}
              </span>
            )}
          </div>
          <div className="text-[0.6rem] text-text-muted mt-0.5">
            {d?.available
              ? `${d.counts?.supportive ?? 0} supportive · ${d.counts?.neutral ?? 0} neutral · ${d.counts?.headwind ?? 0} headwind · ${d.change_window_days}-day change vs ${d.lookback} history` +
                (d.net_excluded_stale ? ` · net excludes ${d.net_excluded_stale} stale` : "")
              : "Rates, credit, dollar, vol, growth and inflation — scored for equity impact"}
          </div>
        </div>
        <Link href="/fed-macro" className="text-[0.6rem] text-text-muted hover:text-accent whitespace-nowrap">
          Fed &amp; macro →
        </Link>
      </div>

      {q.isLoading && (
        <div className="py-10 text-center">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {!q.isLoading && !d?.available && (
        <div className="py-4 flex items-baseline gap-2 flex-wrap">
          <p className="text-xs text-text-muted">
            {q.isError ? "Couldn't load the macro board." : `Macro board unavailable${d?.reason ? ` — ${d.reason}` : ""}.`}
          </p>
          {q.isError && (
            <button type="button" onClick={() => q.refetch()} disabled={q.isFetching}
              className="text-[0.65rem] text-accent hover:underline disabled:opacity-50">
              {q.isFetching ? "Retrying…" : "Retry"}
            </button>
          )}
        </div>
      )}

      {d?.available && (
        <>
          {(d.biggest_headwind || d.biggest_support) && (
            <div className="border-l-2 border-l-accent bg-accent/5 px-3 py-2 rounded-r text-[0.68rem] leading-snug">
              {d.biggest_headwind && (
                <p className="text-text">
                  <span className="font-semibold">Heaviest drag:</span>{" "}
                  {d.biggest_headwind.label} — {d.biggest_headwind.why}
                </p>
              )}
              {d.biggest_support && (
                <p className="text-text mt-1">
                  <span className="font-semibold">Strongest support:</span>{" "}
                  {d.biggest_support.label} — {d.biggest_support.why}
                </p>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-1">
            {columns.map((col, ci) => (
              <table key={ci} className="w-full text-[0.68rem]">
                <thead>
                  <tr className="text-text-muted border-b border-border">
                    <th className="text-left font-medium py-1">Factor</th>
                    <th className="text-right font-medium py-1 tabular-nums w-[4.5rem]">Level</th>
                    <th className="text-right font-medium py-1 tabular-nums w-[4rem]">Δ{d.change_window_days}d</th>
                    <th className="text-right font-medium py-1 tabular-nums w-[3rem]">Pct</th>
                    <th className="text-right font-medium py-1 w-[5.5rem]">Pressure</th>
                  </tr>
                </thead>
                <tbody>
                  {col.map(([group, rows]) => (
                    <Fragment key={group}>
                      <tr>
                        <td colSpan={5} className="pt-2 pb-0.5">
                          <span className="text-[0.58rem] font-bold uppercase tracking-wider text-text-muted">
                            {group}
                          </span>
                        </td>
                      </tr>
                      {rows.map((r) => (
                        <tr key={r.key} className="border-b border-border/40 last:border-0">
                          <td className="py-1 pr-2">
                            <span className="text-text" title={r.why}>{r.label}</span>
                            <span className="ml-1.5 text-[0.52rem] uppercase tracking-wide text-text-muted/70">
                              {r.kind === "fundamental" ? "fund" : "tech"}
                            </span>
                            {r.stale && (
                              <span className="ml-1 text-[0.52rem] uppercase text-spot"
                                title={`Last print ${r.last_print} — ${r.stale_days}d ago. Its flat reading means no new data, not no pressure.`}>
                                stale
                              </span>
                            )}
                          </td>
                          <td className="text-right tabular-nums text-text py-1">{fmtLevel(r)}</td>
                          <td className="text-right tabular-nums text-text py-1">{fmtChange(r)}</td>
                          <td className="text-right tabular-nums text-text-muted py-1">
                            {Math.round(r.pctile * 100)}
                          </td>
                          <td className={`text-right py-1 font-semibold ${verdictClass(r.verdict)}`}>
                            <span className="inline-flex items-center gap-1 justify-end whitespace-nowrap">
                              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${verdictDot(r.verdict)}`} />
                              {r.verdict}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            ))}
          </div>

          <details className="group">
            <summary className="text-[0.62rem] text-text-muted hover:text-accent cursor-pointer select-none list-none flex items-center gap-1">
              <span className="transition-transform group-open:rotate-90">▸</span>
              How to read this
            </summary>
            <div className="text-[0.65rem] text-text-muted leading-relaxed mt-2 space-y-1.5 pl-3 border-l border-border">
              <p>
                <span className="text-text font-semibold">The verdict is arithmetic.</span> Each factor&apos;s
                change over {d.change_window_days} business days is z-scored against its own history of
                {" "}{d.change_window_days}-day moves, then flipped by whether rising is good or bad for
                equities. Beyond ±0.5 becomes supportive or headwind. Normalising per factor is what makes
                a 20bp move in credit spreads comparable to a 20bp move in the 10Y.
              </p>
              <p>
                <span className="text-text font-semibold">Change, not level.</span> A level that has been
                elevated for a year is already discounted — what moves equities is the delta. Percentile
                shows where the level sits in its {d.lookback} range, as context for how much room is left,
                not as the verdict.
              </p>
              <p>
                <span className="text-text font-semibold">Technical vs fundamental.</span> Rows tagged{" "}
                <span className="uppercase text-[0.55rem]">tech</span> are market-priced and update daily
                (spreads, vol, the dollar, copper/gold). Rows tagged{" "}
                <span className="uppercase text-[0.55rem]">fund</span> are reported economic data on a
                weekly or monthly cadence. Market-priced factors turn first; reported data confirms.
              </p>
              <p>
                <span className="text-text font-semibold">Limits.</span> The composite weights every factor
                equally, which is a choice, not a fact — it will understate a regime driven by one dominant
                variable. A <span className="uppercase text-[0.55rem] text-spot">stale</span> tag means the
                underlying print hasn&apos;t updated inside the window, so its flat reading is absence of
                news rather than absence of pressure.
              </p>
            </div>
          </details>

          <div className="text-[0.55rem] text-text-muted border-t border-border pt-2">
            Data through {d.data_asof}
            {d.unavailable && d.unavailable.length > 0 && ` · unavailable: ${d.unavailable.join(", ")}`}
          </div>

        </>
      )}
    </div>
  );
}
