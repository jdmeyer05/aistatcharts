"use client";

/**
 * The loop's own scoreboard.
 *
 * WHY THIS PAGE EXISTS AND WHAT IT REFUSES TO DO. A system that edits its own
 * prompts on a schedule is worth exactly as much as the record it keeps of
 * whether the edits helped. Everything here is that record: the rule score each
 * generation earned, how the falsifiable calls settled against their own base
 * rates, every prompt version with the reasoning that produced it, and every
 * experiment including the ones the challenger lost.
 *
 * THE TWO NUMBERS ARE NOT INTERCHANGEABLE AND THE PAGE SAYS SO EVERYWHERE. The
 * rule score measures faithfulness to the payload — grounded numbers, no
 * contradiction with the rest of the page. It can rise while the reads get
 * worse. Calibration measures whether the calls were right, against the base
 * rate of the same call, and it is the only number here that touches reality.
 * Presenting a rising rule score as "the AI is getting smarter" would be the
 * exact failure this whole system was built to catch, so the caveat travels
 * with the number rather than sitting in a footnote.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  fetchPromptOverview,
  fetchPromptSummary,
  fetchPromptSnapshots,
  fetchPromptClaims,
  CORE_PROMPT_SURFACES,
  type PromptSurface,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, getPlotConfig, useIsMobile } from "@/lib/chart-theme";
import { Plot } from "@/components/plot";
import { ChartCard } from "@/components/ui/chart-card";
import { Metric } from "@/components/ui/metric";

const SURFACE_META: Record<string, { label: string; blurb: string }> = {
  market_driver: {
    label: "Market Driver",
    blurb: "The regime read at the top of the home page. The only surface that makes falsifiable calls, so the only one with a calibration record.",
  },
  home_interpret: {
    label: "Page Interpretation (home)",
    blurb: "The page-wide interpretation panel on home. Scored on grounding and shape; a promoted version serves the home page only, never the other pages.",
  },
  es_audit: {
    label: "ES Card Auditor",
    blurb: "The contradiction auditor on the ES card. Scored on whether its findings quote both sides and stay inside their remit — an empty finding list is a success, not a failure.",
  },
  news_digest: {
    label: "ES News Digest",
    blurb: "The pre-bell headline synthesis, and the ES card's only AI block — everything else on that card is measured. Its prohibition on implying a direction is enforced as a critical rule.",
  },
};

/** `interpret:smart-money` -> a readable label, for the per-page surfaces. */
function surfaceLabel(id: string): string {
  const meta = SURFACE_META[id];
  if (meta) return meta.label;
  if (id.startsWith("interpret:")) return `Interpretation — ${id.slice("interpret:".length)}`;
  return id;
}

function surfaceBlurb(id: string): string {
  const meta = SURFACE_META[id];
  if (meta) return meta.blurb;
  if (id.startsWith("interpret:")) {
    return (
      "The interpretation panel on this page. Measured and graded by the same rules as the " +
      "home panel, but not versioned or rewritten — the loop only edits a prompt where it has " +
      "a record to argue from, and that record is currently the home page's."
    );
  }
  return "";
}

const TABS = ["Scorecard", "Calibration", "Versions", "Experiments", "Recent Outputs"] as const;
type Tab = (typeof TABS)[number];

const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
const num = (v: number | null | undefined, digits = 3) =>
  v === null || v === undefined ? "—" : v.toFixed(digits);

export default function PromptLoopPage() {
  const [surface, setSurface] = useState<PromptSurface>("market_driver");
  const [tab, setTab] = useState<Tab>("Scorecard");
  const [days, setDays] = useState(30);
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme !== "light";
  const isMobile = useIsMobile();
  const theme = getChartTheme(isDark);

  const overview = useQuery({
    queryKey: ["prompt-overview", days],
    queryFn: () => fetchPromptOverview(days),
    staleTime: 5 * 60_000,
    retry: false,
  });

  const summary = useQuery({
    queryKey: ["prompt-summary", surface, days],
    queryFn: () => fetchPromptSummary(surface, days),
    staleTime: 5 * 60_000,
    retry: false,
  });

  const claims = useQuery({
    queryKey: ["prompt-claims", surface],
    queryFn: () => fetchPromptClaims(surface, 90, "resolved"),
    enabled: tab === "Calibration" && surface === "market_driver",
    staleTime: 5 * 60_000,
    retry: false,
  });

  const snapshots = useQuery({
    queryKey: ["prompt-snapshots", surface],
    queryFn: () => fetchPromptSnapshots(surface, { limit: 25, days: 14 }),
    enabled: tab === "Recent Outputs",
    staleTime: 5 * 60_000,
    retry: false,
  });

  // Built from what the overview actually returned, so a new surface appears
  // here the first time it records anything — no list to keep in sync.
  const surfaces: string[] = Object.keys(overview.data?.surfaces ?? {}).sort((a, b) => {
    const ia = CORE_PROMPT_SURFACES.indexOf(a as never);
    const ib = CORE_PROMPT_SURFACES.indexOf(b as never);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return a.localeCompare(b);
  });

  const denied =
    (overview.error as Error | undefined)?.message?.includes("403") ||
    (summary.error as Error | undefined)?.message?.includes("403");

  if (denied) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-12">
        <div className="card p-6">
          <h1 className="text-xl font-semibold mb-2">Prompt Loop</h1>
          <p className="text-sm text-text-muted">
            Admin only. These views return full prompt text, the payloads behind each
            generation, and the critic&apos;s unedited reasoning — operating detail rather
            than product.
          </p>
        </div>
      </main>
    );
  }

  const s = summary.data;
  const series = s?.score_series ?? [];
  const cal = s?.calibration ?? null;


  return (
    <main className="mx-auto max-w-7xl px-4 py-6 space-y-4">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Prompt Loop</h1>
        <p className="text-sm text-text-muted max-w-4xl">
          Every generation from the home page&apos;s AI blocks is stored with the exact
          payload the model was shown. Deterministic rules grade it, an adversarial pass
          reads the failures and proposes a rewrite, and the rewrite is replayed against
          the incumbent on held-out historical payloads before anything is served.
        </p>
        <p className="text-xs text-text-muted max-w-4xl border border-border rounded p-2">
          <strong>Read the two scores separately.</strong> The rule score is
          faithfulness to the data each note was given — grounded numbers, no
          contradictions with the rest of the page. It says nothing about whether a read
          was right. Calibration is the half that touches reality, and it is only
          meaningful against the base rate printed beside it. A rising rule score with a
          flat calibration number means the notes got cleaner, not smarter.
        </p>
      </header>

      {/* surface + window selectors */}
      <div className="flex flex-wrap items-center gap-2">
        {(surfaces.length ? surfaces : [...CORE_PROMPT_SURFACES]).map((id) => (
          <button
            key={id}
            onClick={() => setSurface(id)}
            className={`px-3 py-1.5 text-sm rounded border transition-colors ${
              surface === id
                ? "border-accent text-accent"
                : "border-border text-text-muted hover:text-text"
            }`}
          >
            {surfaceLabel(id)}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-2 py-1 text-xs rounded border ${
                days === d ? "border-accent text-accent" : "border-border text-text-muted"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      <p className="text-xs text-text-muted">{surfaceBlurb(surface)}</p>

      {/* headline row */}
      <div className="card p-4 grid grid-cols-2 md:grid-cols-5 gap-4">
        <Metric
          label="Serving"
          value={s?.champion ? `v${s.champion.version}` : "—"}
          delta={s?.champion?.origin ?? undefined}
        />
        <Metric label="Outputs graded" value={s?.n_graded != null ? String(s.n_graded) : "—"} />
        <Metric label="Mean rule score" value={num(s?.mean_score, 3)} />
        <Metric
          label="Critical / output"
          value={num(s?.findings_per_output?.critical, 3)}
          deltaType={(s?.findings_per_output?.critical ?? 0) > 0 ? "loss" : "gain"}
          delta={(s?.findings_per_output?.critical ?? 0) > 0 ? "defects reaching the page" : "clean"}
        />
        <Metric
          label="Challenger"
          value={s?.challenger?.version != null ? `v${s.challenger.version}` : "none open"}
        />
      </div>

      {/* tabs */}
      <div className="flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === t ? "border-accent text-accent" : "border-transparent text-text-muted hover:text-text"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {summary.isLoading && <div className="card p-6 text-sm text-text-muted">Loading…</div>}

      {tab === "Scorecard" && (
        <div className="space-y-4">
          <ChartCard
            title="Daily mean rule score"
            subtitle="One point per day, averaged over that day's generations. Promotions are marked; a step that lines up with a promotion is the loop working, a step that does not is the market getting easier."
            height={{ desktop: 340, mobile: 260 }}
            error={series.length === 0 ? "No graded generations in this window yet." : null}
          >
            <Plot
              data={[
                {
                  x: series.map((p) => p.date),
                  y: series.map((p) => p.mean_score),
                  type: "scatter",
                  mode: "lines+markers",
                  name: "mean rule score",
                  line: { color: theme.accent, width: 2 },
                  marker: { size: 6 },
                  hovertemplate: "%{x}<br>score %{y:.3f}<extra></extra>",
                },
              ]}
              layout={{
                ...getBaseLayout(theme, {
                  height: isMobile ? 260 : 340,
                  yaxis: { title: "score", range: [0, 1.02] },
                  xaxis: { title: "" },
                  shapes: (s?.versions ?? [])
                    .filter((v) => v.promoted_at)
                    .map((v) => ({
                      type: "line",
                      x0: v.promoted_at!.slice(0, 10),
                      x1: v.promoted_at!.slice(0, 10),
                      y0: 0,
                      y1: 1.02,
                      line: { color: theme.grid, width: 1, dash: "dot" },
                    })),
                }),
              }}
              config={getPlotConfig(isMobile)}
              style={{ width: "100%" }}
            />
          </ChartCard>

          <div className="card p-4">
            <h2 className="text-sm font-semibold mb-3">What the rules are catching</h2>
            <div className="grid grid-cols-3 gap-4">
              {(["critical", "major", "minor"] as const).map((k) => (
                <div key={k}>
                  <div className="metric-label capitalize">{k}</div>
                  <div className="metric-value">{s?.finding_totals?.[k] ?? 0}</div>
                  <div className="text-xs text-text-muted">
                    {num(s?.findings_per_output?.[k], 3)} per output
                  </div>
                </div>
              ))}
            </div>
            <p className="text-xs text-text-muted mt-3">
              Critical findings are the defects that reached a live page once already — a
              sub-20 VIX called elevated, an absent price change reported as flat, a
              &ldquo;no catalyst&rdquo; written while the macro feed carried the story, an
              invented ticker. Those same rules form the regression suite: a challenger
              that reintroduces any of them is refused regardless of its score.
            </p>
          </div>
        </div>
      )}

      {tab === "Calibration" && (
        <div className="space-y-4">
          {surface !== "market_driver" ? (
            <div className="card p-4 text-sm text-text-muted">
              Only the market-driver surface makes falsifiable calls. The interpretation
              panel describes what a page shows and the auditor reports contradictions
              within it — neither states anything price can settle, so neither has a
              calibration record. Scoring them on market outcomes would be inventing a
              claim they never made.
            </div>
          ) : !cal?.n ? (
            <div className="card p-4 text-sm text-text-muted">
              No calls have settled yet. Each one resolves from the first close after the
              note was written to a close one to five sessions later, so the first
              numbers appear a few days after the loop starts recording — and they will
              be reported with their confidence interval, because a handful of settled
              calls is not a track record.
            </div>
          ) : (
            <>
              <div className="card p-4 grid grid-cols-2 md:grid-cols-5 gap-4">
                <Metric label="Calls settled" value={String(cal.n)} />
                <Metric
                  label="Hit rate"
                  value={pct(cal.hit_rate)}
                  delta={
                    cal.hit_rate_ci95
                      ? `95% CI ${pct(cal.hit_rate_ci95[0], 0)}–${pct(cal.hit_rate_ci95[1], 0)}`
                      : undefined
                  }
                />
                <Metric
                  label="Base rate"
                  value={pct(cal.base_rate)}
                  delta="what the same calls score by chance"
                />
                <Metric label="Brier" value={num(cal.brier)} delta={`vs ${num(cal.brier_base_rate)} base`} />
                <Metric
                  label="Brier skill"
                  value={num(cal.brier_skill)}
                  deltaType={(cal.brier_skill ?? 0) > 0 ? "gain" : "loss"}
                  delta={(cal.brier_skill ?? 0) > 0 ? "beats its base rate" : "no edge over the base rate"}
                />
              </div>

              <div className="card p-4">
                <p className="text-xs text-text-muted">
                  <strong>Brier skill is the number that matters.</strong> It compares the
                  model&apos;s stated confidences against simply quoting the base rate every
                  time. Zero means the calls were worth what the calendar was worth.
                  Negative means quoting the base rate would have been better. A hit rate
                  above 50% with skill at zero is a model calling things that were going to
                  happen anyway.
                </p>
              </div>

              <div className="card p-4 overflow-x-auto">
                <h2 className="text-sm font-semibold mb-3">By call type</h2>
                <table className="w-full text-sm">
                  <thead className="text-text-muted text-xs">
                    <tr>
                      <th className="text-left py-1">Op</th>
                      <th className="text-right">n</th>
                      <th className="text-right">Hit rate</th>
                      <th className="text-right">Base rate</th>
                      <th className="text-right">Edge</th>
                    </tr>
                  </thead>
                  <tbody className="font-data">
                    {Object.entries(cal.by_op ?? {}).map(([op, b]) => (
                      <tr key={op} className="border-t border-border">
                        <td className="py-1">{op}</td>
                        <td className="text-right">{b.n}</td>
                        <td className="text-right">{pct(b.hit_rate)}</td>
                        <td className="text-right">{pct(b.base_rate)}</td>
                        <td
                          className={`text-right ${
                            b.hit_rate != null && b.base_rate != null && b.hit_rate > b.base_rate
                              ? "text-gain"
                              : "text-loss"
                          }`}
                        >
                          {b.hit_rate != null && b.base_rate != null
                            ? `${((b.hit_rate - b.base_rate) * 100).toFixed(1)}pp`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="card p-4 overflow-x-auto">
                <h2 className="text-sm font-semibold mb-3">Settled calls</h2>
                <table className="w-full text-sm">
                  <thead className="text-text-muted text-xs">
                    <tr>
                      <th className="text-left py-1">Stated</th>
                      <th className="text-left">Call</th>
                      <th className="text-right">Conf</th>
                      <th className="text-right">Base</th>
                      <th className="text-right">Move</th>
                      <th className="text-right">Result</th>
                    </tr>
                  </thead>
                  <tbody className="font-data">
                    {(claims.data?.data ?? []).slice(0, 60).map((c) => (
                      <tr key={c.id} className="border-t border-border">
                        <td className="py-1">{c.stated_at.slice(0, 16).replace("T", " ")}</td>
                        <td>
                          {c.claim.subject}
                          {c.claim.vs ? ` vs ${c.claim.vs}` : ""} {c.claim.op}{" "}
                          {c.claim.threshold}% / {c.claim.sessions}d
                        </td>
                        <td className="text-right">{pct(c.confidence, 0)}</td>
                        <td className="text-right">{pct(c.base_rate, 0)}</td>
                        <td className="text-right">
                          {typeof c.actual?.move_pct === "number"
                            ? `${(c.actual.move_pct as number).toFixed(2)}%`
                            : "—"}
                        </td>
                        <td className={`text-right ${c.correct ? "text-gain" : "text-loss"}`}>
                          {c.correct ? "hit" : "miss"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {tab === "Versions" && (
        <div className="space-y-4">
          {s?.challenger && (
            <div className="card p-4 border-accent">
              <h2 className="text-sm font-semibold mb-2">
                Open challenger — v{s.challenger.version}
              </h2>
              <p className="text-xs text-text-muted mb-2">
                Proposed {new Date(s.challenger.created_at).toLocaleString()}. Not served
                to anyone. It has to win two independent holdout draws on different days
                before it replaces v{s.champion?.version}.
              </p>
              <pre className="text-xs whitespace-pre-wrap text-text-muted">
                {s.challenger.rationale}
              </pre>
            </div>
          )}

          <div className="card p-4 overflow-x-auto">
            <h2 className="text-sm font-semibold mb-3">Version history</h2>
            <table className="w-full text-sm">
              <thead className="text-text-muted text-xs">
                <tr>
                  <th className="text-left py-1">v</th>
                  <th className="text-left">Status</th>
                  <th className="text-left">Origin</th>
                  <th className="text-left">Created</th>
                  <th className="text-left">Promoted</th>
                  <th className="text-left">Why it exists</th>
                </tr>
              </thead>
              <tbody>
                {(s?.versions ?? []).map((v) => (
                  <tr key={v.version} className="border-t border-border align-top">
                    <td className="py-1 font-data">{v.version}</td>
                    <td>
                      <span
                        className={
                          v.status === "champion"
                            ? "text-gain"
                            : v.status === "rejected"
                              ? "text-loss"
                              : "text-text-muted"
                        }
                      >
                        {v.status}
                      </span>
                    </td>
                    <td className="text-text-muted">{v.origin}</td>
                    <td className="text-text-muted font-data text-xs">
                      {v.created_at.slice(0, 10)}
                    </td>
                    <td className="text-text-muted font-data text-xs">
                      {v.promoted_at ? v.promoted_at.slice(0, 10) : "—"}
                    </td>
                    <td className="text-xs text-text-muted max-w-xl">
                      {v.rationale ?? v.diff_summary ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "Experiments" && (
        <div className="card p-4 overflow-x-auto">
          <h2 className="text-sm font-semibold mb-1">Every experiment, including the losses</h2>
          <p className="text-xs text-text-muted mb-3">
            Each row is a paired replay: both prompts handed the same held-out payloads,
            both graded by the same rules, differences taken per situation. The rejects
            are the point — a loop that only records its wins is a loop that cannot be
            audited.
          </p>
          <table className="w-full text-sm">
            <thead className="text-text-muted text-xs">
              <tr>
                <th className="text-left py-1">When</th>
                <th className="text-left">Match</th>
                <th className="text-right">n</th>
                <th className="text-right">Δ score</th>
                <th className="text-left">95% CI</th>
                <th className="text-left">Verdict</th>
                <th className="text-left">Reasons</th>
              </tr>
            </thead>
            <tbody>
              {(s?.experiments ?? []).map((e) => {
                const m = e.metrics as Record<string, unknown>;
                const ci = m.ci95 as [number, number] | undefined;
                return (
                  <tr key={e.id} className="border-t border-border align-top">
                    <td className="py-1 font-data text-xs">{e.created_at.slice(0, 16).replace("T", " ")}</td>
                    <td className="font-data text-xs">
                      v{e.champion_version} → v{e.challenger_version}
                    </td>
                    <td className="text-right font-data">{e.n_holdout}</td>
                    <td className="text-right font-data">
                      {typeof m.mean_diff === "number" ? (m.mean_diff as number).toFixed(3) : "—"}
                    </td>
                    <td className="font-data text-xs">
                      {ci ? `[${ci[0].toFixed(3)}, ${ci[1].toFixed(3)}]` : "—"}
                    </td>
                    <td className={e.verdict === "promote" ? "text-gain" : "text-loss"}>
                      {e.verdict}
                    </td>
                    <td className="text-xs text-text-muted max-w-md">{e.notes ?? "—"}</td>
                  </tr>
                );
              })}
              {(s?.experiments ?? []).length === 0 && (
                <tr>
                  <td colSpan={7} className="py-3 text-sm text-text-muted">
                    No experiments yet. One runs whenever a challenger is open.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === "Recent Outputs" && (
        <div className="space-y-3">
          {(snapshots.data?.data ?? []).map((snap) => (
            <div key={snap.id} className="card p-4">
              <div className="flex flex-wrap items-baseline gap-3 mb-2">
                <span className="font-data text-xs text-text-muted">
                  {snap.created_at.slice(0, 16).replace("T", " ")}
                </span>
                <span className="text-xs text-text-muted">{snap.session_phase}</span>
                <span className="text-xs text-text-muted">v{snap.prompt_version}</span>
                <span className="text-xs text-text-muted">{snap.split}</span>
                <span
                  className={`ml-auto font-data text-sm ${
                    (snap.score ?? 0) >= 0.9 ? "text-gain" : (snap.score ?? 0) >= 0.6 ? "" : "text-loss"
                  }`}
                >
                  {num(snap.score, 3)}
                </span>
              </div>
              {snap.findings.length > 0 ? (
                <ul className="text-xs space-y-1">
                  {snap.findings.map((f, i) => (
                    <li key={i} className="flex gap-2">
                      <span
                        className={
                          f.severity === "critical"
                            ? "text-loss"
                            : f.severity === "major"
                              ? "text-warn"
                              : "text-text-muted"
                        }
                      >
                        {f.severity}
                      </span>
                      <span className="font-data text-text-muted">{f.rule}</span>
                      <span>{f.detail}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-text-muted">Passed every rule.</p>
              )}
            </div>
          ))}
          {snapshots.isFetched && (snapshots.data?.data ?? []).length === 0 && (
            <div className="card p-4 text-sm text-text-muted">
              Nothing recorded in the last 14 days. Snapshots are written only when a
              model actually generates — cache hits are not recorded, so a quiet stretch
              or a warm cache both look like this.
            </div>
          )}
        </div>
      )}
    </main>
  );
}
