"use client";

import { useState, useMemo, useCallback, useRef } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  fetchTrumpPsychProfile, decodeTrumpStatement, predictTrumpResponse,
  fetchTrumpMonitor, fetchTrumpPatterns, fetchTrumpHistory,
  fetchRobinhoodPositions,
  type TrumpPost, type TrumpPattern, type TrumpDecodedStatement,
} from "@/lib/api";

// ─── Tabs ──────────────────────────────────────────────────
const TABS = ["Decode Statement", "Predict Response", "Live Monitor", "Pattern Database", "Psych Profile"] as const;
type Tab = typeof TABS[number];

const PATTERN_CATEGORIES = ["", "tariffs", "china", "fed", "iran", "trade_war", "executive_order", "foreign_policy"] as const;

// ─── Color helpers ─────────────────────────────────────────
function bluffColor(score: number) {
  if (score >= 70) return "text-gain";   // likely bluff = green (less risk)
  if (score >= 40) return "text-warning"; // uncertain = yellow
  return "text-loss";                     // genuine intent = red (more risk)
}

function impactColor(impact: number) {
  if (impact >= 2) return "text-gain";
  if (impact >= 0.5) return "text-gain/70";
  if (impact <= -2) return "text-loss";
  if (impact <= -0.5) return "text-loss/70";
  return "text-text-secondary";
}

function riskColor(level: string) {
  const l = (level || "").toLowerCase();
  if (l === "high" || l === "critical") return "border-loss bg-loss/10";
  if (l === "medium") return "border-warning bg-warning/10";
  return "border-border bg-surface-alt";
}

function sentimentBadge(s: string) {
  const map: Record<string, string> = {
    aggressive: "bg-loss/20 text-loss", threatening: "bg-loss/20 text-loss",
    conciliatory: "bg-gain/20 text-gain", confident: "bg-accent/20 text-accent",
    defensive: "bg-warning/20 text-warning", boastful: "bg-accent/20 text-accent",
    neutral: "bg-surface-alt text-text-secondary", mixed: "bg-surface-alt text-text-secondary",
  };
  return map[s] || map.neutral;
}

function relevanceBar(score: number) {
  const pct = Math.min(score * 10, 100);
  const color = score >= 7 ? "bg-loss" : score >= 4 ? "bg-warning" : "bg-gain";
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="w-16 h-1.5 bg-border rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-text-muted">{score}/10</span>
    </div>
  );
}

// ─── Markdown-lite renderer ────────────────────────────────
function Prose({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split("\n");
  return (
    <div className="text-sm text-text-secondary leading-relaxed space-y-1.5">
      {lines.map((line, i) => {
        if (line.startsWith("### ")) return <h4 key={i} className="font-semibold text-text mt-3 mb-1">{line.slice(4)}</h4>;
        if (line.startsWith("## ")) return <h3 key={i} className="font-semibold text-text text-base mt-4 mb-1">{line.slice(3)}</h3>;
        if (line.startsWith("# ")) return <h2 key={i} className="font-bold text-text text-lg mt-4 mb-2">{line.slice(2)}</h2>;
        if (line.startsWith("- ") || line.startsWith("* ")) return <li key={i} className="ml-4 list-disc">{boldify(line.slice(2))}</li>;
        if (line.trim() === "") return <div key={i} className="h-1" />;
        return <p key={i}>{boldify(line)}</p>;
      })}
    </div>
  );
}

function boldify(text: string) {
  const parts = text.split(/\*\*(.*?)\*\*/g);
  return parts.map((p, i) => i % 2 === 1 ? <strong key={i} className="text-text font-semibold">{p}</strong> : p);
}

// ═══════════════════════════════════════════════════════════
// Main Page
// ═══════════════════════════════════════════════════════════
export default function TrumpDecoderPage() {
  const [tab, setTab] = useState<Tab>("Decode Statement");

  // ── Shared data: RH positions for risk analysis ──
  const positions = useQuery({
    queryKey: ["rh-positions"],
    queryFn: fetchRobinhoodPositions,
    staleTime: 60_000,
    retry: 1,
  });

  // Build positions summary string for API
  const positionsSummary = useMemo(() => {
    if (!positions.data?.success) return "";
    const d = positions.data;
    const lines: string[] = [];
    lines.push(`Portfolio: $${d.portfolio.equity.toLocaleString()} equity`);
    lines.push(`Greeks: Delta ${d.greeks.delta.toFixed(1)}, Gamma ${d.greeks.gamma.toFixed(2)}, Theta ${d.greeks.theta.toFixed(2)}, Vega ${d.greeks.vega.toFixed(2)}`);
    for (const s of d.stocks) lines.push(`STOCK: ${s.ticker} ${s.qty} shares @ $${s.current_price} (P&L ${s.pl >= 0 ? "+" : ""}$${s.pl.toFixed(0)})`);
    for (const sp of d.spreads) lines.push(`OPTION: ${sp.ticker} ${sp.type} ${sp.strikes} exp ${sp.expiration} qty ${sp.qty} (P&L ${sp.pl >= 0 ? "+" : ""}$${sp.pl.toFixed(0)}, delta ${sp.greeks.delta.toFixed(1)})`);
    return lines.join("\n");
  }, [positions.data]);

  return (
    <div className="space-y-3">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-text">Trump Decoder</h1>
          <p className="text-xs text-text-muted">Psychological profiling + behavioral analysis — 3-model AI</p>
        </div>
        {positions.data?.success && (
          <div className="text-xs text-text-muted border border-border rounded px-2 py-1">
            {positions.data.spreads.length + positions.data.stocks.length} positions loaded
          </div>
        )}
      </div>

      {/* ── Tab bar ── */}
      <div className="flex gap-1 border-b border-border pb-px">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-1.5 text-xs font-medium rounded-t transition-colors ${
              tab === t ? "bg-accent/15 text-accent border-b-2 border-accent" : "text-text-secondary hover:text-text hover:bg-surface-alt"
            }`}>
            {t}
          </button>
        ))}
      </div>

      {/* ── Tab content ── */}
      {tab === "Decode Statement" && <DecodeTab positionsSummary={positionsSummary} />}
      {tab === "Predict Response" && <PredictTab />}
      {tab === "Live Monitor" && <MonitorTab />}
      {tab === "Pattern Database" && <PatternTab />}
      {tab === "Psych Profile" && <PsychTab />}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Tab 1: Decode Statement
// ═══════════════════════════════════════════════════════════
function DecodeTab({ positionsSummary }: { positionsSummary: string }) {
  const [statement, setStatement] = useState("");
  const [context, setContext] = useState("");
  const [imageBase64, setImageBase64] = useState("");
  const [imagePreview, setImagePreview] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const decode = useMutation({
    mutationFn: () => decodeTrumpStatement(statement, context, positionsSummary, imageBase64),
  });

  const d = decode.data;
  const hasInput = statement.trim() || imageBase64;

  // Process an image file into base64
  const processImage = useCallback((file: File) => {
    if (!file.type.startsWith("image/")) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target?.result as string;
      setImagePreview(dataUrl);
      setImageBase64(dataUrl);  // includes data:image/...;base64, prefix
    };
    reader.readAsDataURL(file);
  }, []);

  // Handle paste (Ctrl+V with screenshot)
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) processImage(file);
        return;
      }
    }
    // If no image, let default text paste happen
  }, [processImage]);

  // Handle drag-drop
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) processImage(file);
  }, [processImage]);

  const clearImage = useCallback(() => {
    setImageBase64("");
    setImagePreview("");
  }, []);

  return (
    <div className="space-y-3">
      {/* Input */}
      <div className="border border-border rounded-md p-3 space-y-2 bg-surface">
        <label className="text-xs font-semibold text-text">Paste Trump Statement / Tweet</label>

        {/* Textarea with paste + drop support */}
        <div
          className={`relative rounded border transition-colors ${dragOver ? "border-accent bg-accent/5" : "border-border"}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          <textarea
            value={statement}
            onChange={e => setStatement(e.target.value)}
            onPaste={handlePaste}
            placeholder="Type or paste text here — or paste a screenshot (Ctrl+V) / drag an image..."
            className="w-full h-24 bg-bg rounded px-3 py-2 text-sm text-text placeholder:text-text-muted resize-none focus:outline-none focus:border-accent"
          />
          {dragOver && (
            <div className="absolute inset-0 flex items-center justify-center bg-accent/10 rounded pointer-events-none">
              <span className="text-sm font-semibold text-accent">Drop screenshot here</span>
            </div>
          )}
        </div>

        {/* Image preview */}
        {imagePreview && (
          <div className="flex items-start gap-2 border border-border rounded p-2 bg-bg">
            <img src={imagePreview} alt="Screenshot" className="max-h-32 rounded border border-border" />
            <div className="flex-1 min-w-0">
              <div className="text-xs text-gain font-semibold mb-0.5">Screenshot attached</div>
              <div className="text-[10px] text-text-muted">Claude Sonnet will extract the text before analysis</div>
            </div>
            <button onClick={clearImage} className="text-xs text-text-muted hover:text-loss px-1">Remove</button>
          </div>
        )}

        {/* Upload button (alternative to paste/drop) */}
        {!imagePreview && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => fileInputRef.current?.click()}
              className="px-2 py-1 text-[10px] text-text-secondary border border-border rounded hover:bg-surface-alt"
            >
              Upload Screenshot
            </button>
            <span className="text-[10px] text-text-muted">or Ctrl+V to paste, or drag &amp; drop</span>
            <input ref={fileInputRef} type="file" accept="image/*" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) processImage(f); e.target.value = ""; }} />
          </div>
        )}

        <label className="text-xs font-semibold text-text">Context (optional)</label>
        <input
          value={context} onChange={e => setContext(e.target.value)}
          placeholder="e.g. 'This was posted 12 hours before the tariff deadline'"
          className="w-full bg-bg border border-border rounded px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:border-accent"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={() => decode.mutate()}
            disabled={!hasInput || decode.isPending}
            className="px-4 py-1.5 bg-accent text-bg text-xs font-semibold rounded hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {decode.isPending ? (imageBase64 ? "Reading screenshot + Analyzing (3 models)..." : "Analyzing (3 models)...") : "Decode Statement"}
          </button>
          {decode.isPending && <span className="text-xs text-text-muted">{imageBase64 ? "~25-50s" : "~20-40s"} — Grok searching, then Claude + Gemini in parallel</span>}
        </div>
      </div>

      {decode.error && <div className="text-xs text-loss border border-loss/30 rounded p-2">{(decode.error as Error).message}</div>}

      {/* Results */}
      {d?.success && (
        <div className="space-y-3">
          {/* ── Decoded Meaning ── */}
          <div className="border border-border rounded-md p-3 bg-surface">
            <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">DECODED MEANING</div>
            <p className="text-sm text-text leading-relaxed">{d.decoded_meaning}</p>
          </div>

          {/* ── Score Cards Row ── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {/* Bluff Score */}
            <div className="border border-border rounded-md p-3 bg-surface text-center">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">BLUFF SCORE</div>
              <div className={`text-2xl font-bold ${bluffColor(d.bluff_score)}`}>{d.bluff_score}</div>
              <div className="text-xs text-text-secondary">{d.bluff_label}</div>
              <div className="mt-1.5 w-full h-1.5 bg-border rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${d.bluff_score >= 70 ? "bg-gain" : d.bluff_score >= 40 ? "bg-warning" : "bg-loss"}`}
                  style={{ width: `${d.bluff_score}%` }} />
              </div>
            </div>

            {/* Market Impact */}
            <div className="border border-border rounded-md p-3 bg-surface text-center">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">MARKET IMPACT</div>
              <div className={`text-2xl font-bold ${impactColor(d.market_impact ?? 0)}`}>
                {(d.market_impact ?? 0) > 0 ? "+" : ""}{(d.market_impact ?? 0).toFixed(1)}
              </div>
              <div className="text-xs text-text-secondary">{d.market_impact_label}</div>
              {d.spy_range_pct && d.spy_range_pct.length === 2 && (
                <div className="text-[10px] text-text-muted mt-1">
                  SPY: {d.spy_range_pct[0] > 0 ? "+" : ""}{d.spy_range_pct[0].toFixed(1)}% to {d.spy_range_pct[1] > 0 ? "+" : ""}{d.spy_range_pct[1].toFixed(1)}%
                </div>
              )}
            </div>

            {/* Probability Distribution */}
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">PROBABILITIES</div>
              {Object.entries(d.probability_distribution || {}).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between text-xs mb-0.5">
                  <span className="text-text-secondary capitalize">{k.replace(/_/g, " ")}</span>
                  <span className="font-semibold text-text">{(Number(v) * 100).toFixed(0)}%</span>
                </div>
              ))}
            </div>

            {/* Mood Index */}
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">TRUMP MOOD</div>
              {d.mood_index && (
                <>
                  <div className="text-xs text-text-secondary mb-0.5">Posts: {d.mood_index.posting_frequency || "N/A"}</div>
                  <div className="text-xs text-text-secondary mb-0.5">Tone: {d.mood_index.sentiment || "N/A"}</div>
                  <div className="text-xs text-text-secondary mb-0.5">
                    Escalation: <span className="font-semibold text-text">{d.mood_index.escalation_level || "?"}/10</span>
                  </div>
                  <div className="text-xs text-text-secondary">Trend: {d.mood_index.tone_shift || "N/A"}</div>
                </>
              )}
            </div>
          </div>

          {/* ── Historical Analogs ── */}
          {d.historical_analogs?.length > 0 && (
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-2">HISTORICAL ANALOGS</div>
              <div className="space-y-2">
                {d.historical_analogs.map((a, i) => (
                  <div key={i} className="border border-border rounded p-2 bg-bg text-xs">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold text-accent">{a.date}</span>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${a.was_bluff ? "bg-gain/20 text-gain" : "bg-loss/20 text-loss"}`}>
                        {a.was_bluff ? "BLUFF" : "FOLLOWED THROUGH"}
                      </span>
                      {a.days_to_resolution && <span className="text-text-muted">{a.days_to_resolution}d to resolution</span>}
                    </div>
                    <p className="text-text-secondary">{a.statement_summary || a.similarity}</p>
                    <p className="text-text mt-0.5"><strong>Outcome:</strong> {a.outcome}</p>
                    {a.market_reaction && <p className="text-text-muted mt-0.5">Market: {a.market_reaction}</p>}
                  </div>
                ))}
              </div>
              {Boolean(d.pattern_match?.bluff_rate_in_analogs) && (
                <div className="mt-2 text-xs text-text-secondary border-t border-border pt-2">
                  <strong>Pattern:</strong> {String(d.pattern_match?.bluff_rate_in_analogs ?? "")} — Most likely: {String(d.pattern_match?.most_likely_outcome ?? "unknown")}
                </div>
              )}
            </div>
          )}

          {/* ── Position Risks ── */}
          {d.position_risks?.length > 0 && (
            <div className="border border-loss/40 rounded-md p-3 bg-loss/5">
              <div className="text-[10px] font-bold text-loss tracking-wider mb-2">POSITION RISK ALERTS</div>
              <div className="space-y-1.5">
                {d.position_risks.map((r, i) => (
                  <div key={i} className={`border rounded p-2 text-xs ${riskColor(r.risk_level)}`}>
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="font-bold text-text">{r.ticker}</span>
                      <span className="text-text-secondary">{r.position_type}</span>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        r.risk_level === "HIGH" ? "bg-loss/30 text-loss" : r.risk_level === "MEDIUM" ? "bg-warning/30 text-warning" : "bg-surface-alt text-text-secondary"
                      }`}>{r.risk_level}</span>
                    </div>
                    <p className="text-text-secondary">{r.recommendation}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Affected Sectors/Tickers ── */}
          {(d.affected_sectors?.length > 0 || d.affected_tickers?.length > 0) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {d.affected_sectors?.length > 0 && (
                <div className="border border-border rounded-md p-3 bg-surface">
                  <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">AFFECTED SECTORS</div>
                  {d.affected_sectors.map((s, i) => (
                    <div key={i} className="flex items-center justify-between text-xs py-0.5">
                      <span className="text-text">{typeof s === "string" ? s : s.sector}</span>
                      {typeof s !== "string" && (
                        <span className={s.direction === "bull" ? "text-gain" : "text-loss"}>
                          {s.direction === "bull" ? "+" : "-"}{s.magnitude}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {d.affected_tickers?.length > 0 && (
                <div className="border border-border rounded-md p-3 bg-surface">
                  <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">AFFECTED TICKERS</div>
                  {d.affected_tickers.map((t, i) => (
                    <div key={i} className="flex items-center justify-between text-xs py-0.5">
                      <span className="text-text font-semibold">{typeof t === "string" ? t : t.ticker}</span>
                      {typeof t !== "string" && (
                        <span className={t.direction === "bull" ? "text-gain" : "text-loss"}>
                          {t.direction === "bull" ? "+" : "-"}{t.magnitude} — {t.reason}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Key Signals to Watch ── */}
          {d.key_signals_to_watch && d.key_signals_to_watch.length > 0 && (
            <div className="border border-accent/30 rounded-md p-3 bg-accent/5">
              <div className="text-[10px] font-bold text-accent tracking-wider mb-1">SIGNALS TO WATCH</div>
              <ul className="text-xs text-text-secondary space-y-0.5">
                {d.key_signals_to_watch.map((s, i) => <li key={i} className="ml-3 list-disc">{s}</li>)}
              </ul>
              {d.timeline && <div className="text-xs text-text-muted mt-1.5">Expected timeline: <strong className="text-text">{d.timeline}</strong></div>}
            </div>
          )}

          {/* ── Full Narrative (collapsible) ── */}
          {d.narrative && (
            <details className="border border-border rounded-md bg-surface">
              <summary className="px-3 py-2 text-xs font-semibold text-text cursor-pointer hover:bg-surface-alt">
                Full Narrative Reasoning
              </summary>
              <div className="px-3 pb-3 border-t border-border pt-2">
                <Prose text={d.narrative} />
              </div>
            </details>
          )}

          {/* ── Model Attribution ── */}
          <div className="flex items-center gap-3 text-[10px] text-text-muted">
            <span>Sources:</span>
            {Object.entries(d.model_sources || {}).map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded bg-surface-alt">{k}: {typeof v === "string" ? v.split("(")[0] : v}</span>
            ))}
          </div>
        </div>
      )}

      {/* ── Past Decodes ── */}
      <DecodeHistory />
    </div>
  );
}

// ── Decode History sub-component ──
function DecodeHistory() {
  const history = useQuery({
    queryKey: ["trump-history"],
    queryFn: () => fetchTrumpHistory(10),
    staleTime: 30_000,
  });

  if (!history.data?.statements?.length) return null;

  return (
    <details className="border border-border rounded-md bg-surface">
      <summary className="px-3 py-2 text-xs font-semibold text-text cursor-pointer hover:bg-surface-alt">
        Past Decoded Statements ({history.data.statements.length})
      </summary>
      <div className="px-3 pb-3 border-t border-border pt-2 space-y-2">
        {history.data.statements.map((s: TrumpDecodedStatement) => (
          <div key={s.id} className="border border-border rounded p-2 bg-bg text-xs">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-text-muted">{new Date(s.created_at).toLocaleDateString()}</span>
              <span className={`font-bold ${bluffColor(s.bluff_score ?? 50)}`}>Bluff: {s.bluff_score ?? "?"}</span>
              <span className={`font-bold ${impactColor(s.market_impact ?? 0)}`}>Impact: {(s.market_impact ?? 0) > 0 ? "+" : ""}{(s.market_impact ?? 0).toFixed(1)}</span>
              {s.was_accurate !== null && s.was_accurate !== undefined && (
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${s.was_accurate ? "bg-gain/20 text-gain" : "bg-loss/20 text-loss"}`}>
                  {s.was_accurate ? "ACCURATE" : "MISSED"}
                </span>
              )}
            </div>
            <p className="text-text-secondary truncate">&quot;{s.statement}&quot;</p>
            <p className="text-text mt-0.5">{s.decoded_meaning}</p>
          </div>
        ))}
      </div>
    </details>
  );
}

// ═══════════════════════════════════════════════════════════
// Tab 2: Predict Response
// ═══════════════════════════════════════════════════════════
function PredictTab() {
  const [scenario, setScenario] = useState("");
  const [timeframe, setTimeframe] = useState("48h");

  const predict = useMutation({
    mutationFn: () => predictTrumpResponse(scenario, timeframe),
  });

  const d = predict.data;

  return (
    <div className="space-y-3">
      <div className="border border-border rounded-md p-3 space-y-2 bg-surface">
        <label className="text-xs font-semibold text-text">Describe a Scenario</label>
        <textarea
          value={scenario} onChange={e => setScenario(e.target.value)}
          placeholder="e.g. 'China announces 50% retaliatory tariffs on US agriculture'"
          className="w-full h-20 bg-bg border border-border rounded px-3 py-2 text-sm text-text placeholder:text-text-muted resize-none focus:outline-none focus:border-accent"
        />
        <div className="flex items-center gap-2">
          <label className="text-xs text-text-secondary">Timeframe:</label>
          {["24h", "48h", "1 week", "1 month"].map(tf => (
            <button key={tf} onClick={() => setTimeframe(tf)}
              className={`px-2 py-1 text-xs rounded ${timeframe === tf ? "bg-accent/15 text-accent font-semibold" : "text-text-secondary hover:bg-surface-alt"}`}>
              {tf}
            </button>
          ))}
        </div>
        <button
          onClick={() => predict.mutate()}
          disabled={!scenario.trim() || predict.isPending}
          className="px-4 py-1.5 bg-accent text-bg text-xs font-semibold rounded hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {predict.isPending ? "Predicting..." : "Predict Response"}
        </button>
      </div>

      {predict.error && <div className="text-xs text-loss border border-loss/30 rounded p-2">{(predict.error as Error).message}</div>}

      {d?.success && (
        <div className="space-y-3">
          {/* Predicted Actions */}
          <div className="border border-border rounded-md p-3 bg-surface">
            <div className="text-[10px] font-bold text-text-muted tracking-wider mb-2">PREDICTED ACTIONS (ranked by probability)</div>
            <div className="space-y-2">
              {d.predicted_actions?.map((a, i) => (
                <div key={i} className="border border-border rounded p-2 bg-bg">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-semibold text-text">{a.action}</span>
                    <span className="text-sm font-bold text-accent">{(a.probability * 100).toFixed(0)}%</span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-xs text-text-secondary">
                    <div>Timeline: <span className="text-text">{a.timeline}</span></div>
                    <div>Market: <span className={impactColor(a.market_impact)}>{a.market_impact > 0 ? "+" : ""}{a.market_impact}</span></div>
                    <div>Precedent: <span className="text-text">{a.historical_precedent}</span></div>
                  </div>
                  {a.signals_to_watch?.length > 0 && (
                    <div className="text-[10px] text-text-muted mt-1">Watch: {a.signals_to_watch.join(", ")}</div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Reasoning */}
          {d.psychological_reasoning && (
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">PSYCHOLOGICAL REASONING</div>
              <p className="text-xs text-text-secondary">{d.psychological_reasoning}</p>
            </div>
          )}

          {/* Wild Card */}
          {d.wild_card_risk && (
            <div className="border border-warning/40 rounded-md p-3 bg-warning/5">
              <div className="text-[10px] font-bold text-warning tracking-wider mb-1">WILD CARD RISK</div>
              <p className="text-xs text-text-secondary">{d.wild_card_risk}</p>
            </div>
          )}

          {/* Positioning */}
          {d.recommended_positioning && (
            <div className="border border-accent/30 rounded-md p-3 bg-accent/5">
              <div className="text-[10px] font-bold text-accent tracking-wider mb-1">RECOMMENDED POSITIONING</div>
              <p className="text-xs text-text-secondary">{d.recommended_positioning}</p>
            </div>
          )}

          {/* Narrative */}
          {d.narrative && (
            <details className="border border-border rounded-md bg-surface">
              <summary className="px-3 py-2 text-xs font-semibold text-text cursor-pointer hover:bg-surface-alt">Full Analysis</summary>
              <div className="px-3 pb-3 border-t border-border pt-2"><Prose text={d.narrative} /></div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Tab 3: Live Monitor
// ═══════════════════════════════════════════════════════════
function MonitorTab() {
  const monitor = useQuery({
    queryKey: ["trump-monitor"],
    queryFn: fetchTrumpMonitor,
    enabled: false,  // manual only
    retry: 1,
  });

  const d = monitor.data;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <button
          onClick={() => monitor.refetch()}
          disabled={monitor.isFetching}
          className="px-4 py-1.5 bg-accent text-bg text-xs font-semibold rounded hover:bg-accent/90 disabled:opacity-40"
        >
          {monitor.isFetching ? "Searching X + Truth Social..." : "Fetch Latest Posts"}
        </button>
        {monitor.dataUpdatedAt > 0 && (
          <span className="text-xs text-text-muted">Last: {new Date(monitor.dataUpdatedAt).toLocaleTimeString()}</span>
        )}
      </div>

      {monitor.error && <div className="text-xs text-loss border border-loss/30 rounded p-2">{(monitor.error as Error).message}</div>}

      {d?.success && (
        <div className="space-y-3">
          {/* Mood Summary Bar */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">MOOD</div>
              <p className="text-xs text-text">{d.mood_summary}</p>
            </div>
            <div className="border border-border rounded-md p-3 bg-surface text-center">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">POSTING FREQUENCY</div>
              <div className="text-sm font-bold text-text">{d.posting_frequency}</div>
            </div>
            <div className="border border-border rounded-md p-3 bg-surface text-center">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">ESCALATION TREND</div>
              <div className={`text-sm font-bold ${
                d.escalation_trend === "increasing" ? "text-loss" : d.escalation_trend === "de-escalating" ? "text-gain" : "text-text"
              }`}>{d.escalation_trend}</div>
            </div>
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">KEY THEMES</div>
              <div className="flex flex-wrap gap-1">
                {d.key_themes?.map((t, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded text-[10px] bg-surface-alt text-text-secondary">{t}</span>
                ))}
              </div>
            </div>
          </div>

          {/* Market Alert */}
          {d.market_alert && (
            <div className="border border-loss/50 rounded-md p-3 bg-loss/10">
              <div className="text-[10px] font-bold text-loss tracking-wider mb-1">MARKET ALERT</div>
              <p className="text-xs text-text">{d.market_alert}</p>
            </div>
          )}

          {/* Breaking Developments */}
          {d.breaking_developments && (
            <div className="border border-accent/50 rounded-md p-3 bg-accent/10">
              <div className="text-[10px] font-bold text-accent tracking-wider mb-1">BREAKING DEVELOPMENTS</div>
              <p className="text-xs text-text">{d.breaking_developments}</p>
            </div>
          )}

          {/* Posts (sorted by market relevance) */}
          <div className="space-y-2">
            {[...(d.posts || [])].sort((a, b) => (b.market_relevance || 0) - (a.market_relevance || 0)).map((post: TrumpPost, i: number) => (
              <div key={i} className="border border-border rounded-md p-3 bg-surface">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-[10px] text-text-muted">{post.timestamp}</span>
                  <span className="text-[10px] text-text-muted capitalize">{post.platform}</span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${sentimentBadge(post.sentiment)}`}>
                    {post.sentiment}
                  </span>
                  <span className="text-[10px] text-text-muted capitalize">{post.category}</span>
                  <div className="ml-auto">{relevanceBar(post.market_relevance)}</div>
                </div>
                <p className="text-sm text-text mb-1.5 leading-relaxed">&quot;{post.text}&quot;</p>
                <p className="text-xs text-text-secondary">{post.interpretation}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Tab 4: Pattern Database
// ═══════════════════════════════════════════════════════════
function PatternTab() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [hasSearched, setHasSearched] = useState(false);

  const search = useMutation({
    mutationFn: (overrides?: { q?: string; cat?: string }) =>
      fetchTrumpPatterns(overrides?.q ?? query, overrides?.cat ?? category),
    onSuccess: () => setHasSearched(true),
  });

  const data = search.data;

  return (
    <div className="space-y-3">
      {/* Intro */}
      {!hasSearched && !search.isPending && (
        <div className="border border-border rounded-md p-3 bg-surface text-xs text-text-secondary">
          Search for historical Trump statement-to-outcome cycles from 2017-present. Grok will search the web
          and build a pattern database that accumulates in Supabase over time. Try searching a topic or click a category.
        </div>
      )}

      {/* Search + Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={query} onChange={e => setQuery(e.target.value)}
          placeholder="Search patterns (e.g. 'tariff bluff', 'china trade war')..."
          className="flex-1 min-w-[200px] bg-bg border border-border rounded px-3 py-1.5 text-sm text-text placeholder:text-text-muted focus:outline-none focus:border-accent"
          onKeyDown={e => e.key === "Enter" && search.mutate({ q: query, cat: category })}
        />
        <button
          onClick={() => search.mutate({ q: query, cat: category })}
          disabled={search.isPending}
          className="px-3 py-1.5 bg-accent text-bg text-xs font-semibold rounded hover:bg-accent/90 disabled:opacity-40"
        >
          {search.isPending ? "Searching (Grok)..." : "Search"}
        </button>
      </div>
      <div className="flex gap-1 flex-wrap">
        {PATTERN_CATEGORIES.map(cat => (
          <button key={cat || "all"} onClick={() => { setCategory(cat); search.mutate({ cat }); }}
            className={`px-2 py-1 text-xs rounded border transition-colors ${category === cat ? "bg-accent/15 text-accent border-accent/30 font-semibold" : "text-text-secondary border-border hover:bg-surface-alt"}`}>
            {cat || "All"}
          </button>
        ))}
      </div>

      {search.error && <div className="text-xs text-loss border border-loss/30 rounded p-2">{(search.error as Error).message}</div>}

      {data?.success && (
        <div className="space-y-2">
          <div className="text-xs text-text-muted">{data.count} patterns ({data.source})</div>
          {data.patterns?.map((p: TrumpPattern, i: number) => (
            <details key={i} className="border border-border rounded-md bg-surface">
              <summary className="px-3 py-2 cursor-pointer hover:bg-surface-alt">
                <div className="flex items-center gap-2 text-xs">
                  <span className="px-1.5 py-0.5 rounded bg-accent/15 text-accent font-semibold capitalize">{p.category}</span>
                  <span className="text-text font-semibold">{p.trigger_statement?.slice(0, 80)}</span>
                  <span className="text-text-muted ml-auto">{p.date_range}</span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                    p.pattern_type === "bluff_cycle" ? "bg-gain/20 text-gain" :
                    p.pattern_type === "genuine_policy" ? "bg-loss/20 text-loss" :
                    "bg-surface-alt text-text-secondary"
                  }`}>{p.pattern_type?.replace(/_/g, " ")}</span>
                </div>
              </summary>
              <div className="px-3 pb-3 border-t border-border pt-2 text-xs space-y-2">
                <div className="grid grid-cols-4 gap-2">
                  <div>
                    <div className="text-text-muted">Resolution</div>
                    <div className="text-text font-semibold">{p.resolution_type?.replace(/_/g, " ")}</div>
                  </div>
                  <div>
                    <div className="text-text-muted">Days</div>
                    <div className="text-text font-semibold">{p.days_to_resolution || "?"}</div>
                  </div>
                  <div>
                    <div className="text-text-muted">SPY Move</div>
                    <div className={`font-semibold ${(p.spy_move_pct || 0) >= 0 ? "text-gain" : "text-loss"}`}>
                      {p.spy_move_pct ? `${p.spy_move_pct > 0 ? "+" : ""}${p.spy_move_pct.toFixed(1)}%` : "?"}
                    </div>
                  </div>
                  <div>
                    <div className="text-text-muted">Bluff Score</div>
                    <div className={`font-semibold ${bluffColor(p.bluff_score || 50)}`}>{p.bluff_score ?? "?"}/100</div>
                  </div>
                </div>
                {p.resolution && <div><strong className="text-text">Resolution:</strong> <span className="text-text-secondary">{p.resolution}</span></div>}
                {p.market_impact_summary && <div><strong className="text-text">Market Impact:</strong> <span className="text-text-secondary">{p.market_impact_summary}</span></div>}
                {p.key_lesson && <div className="border-t border-border pt-1.5 mt-1.5"><strong className="text-accent">Lesson:</strong> <span className="text-text-secondary">{p.key_lesson}</span></div>}
                {p.escalation_path?.length > 0 && (
                  <div className="border-t border-border pt-1.5 mt-1.5">
                    <div className="text-text-muted font-semibold mb-1">Escalation Path:</div>
                    {p.escalation_path.map((step, j) => (
                      <div key={j} className="flex gap-2 text-text-secondary py-0.5">
                        <span className="text-text-muted min-w-[70px]">{step.date}</span>
                        <span>{step.event}</span>
                        {step.market_reaction && <span className="text-text-muted ml-auto">{step.market_reaction}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Tab 5: Psych Profile
// ═══════════════════════════════════════════════════════════
function PsychTab() {
  const profile = useQuery({
    queryKey: ["trump-psych"],
    queryFn: fetchTrumpPsychProfile,
    staleTime: 10 * 60_000,
    retry: 0,
  });

  const d = profile.data;
  const p = d?.profile;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <button
          onClick={() => profile.refetch()}
          disabled={profile.isFetching}
          className="px-4 py-1.5 bg-accent text-bg text-xs font-semibold rounded hover:bg-accent/90 disabled:opacity-40"
        >
          {profile.isFetching ? "Generating Profile (Claude Sonnet + Opus + Grok)..." : d?.cached ? "Regenerate Profile" : d ? "Regenerate Profile" : "Generate Profile"}
        </button>
        {d?.cached && <span className="text-xs text-text-muted">Cached (v{d.version})</span>}
        {d && !d.cached && <span className="text-xs text-gain">Freshly generated</span>}
      </div>

      {profile.error && <div className="text-xs text-loss border border-loss/30 rounded p-2">{(profile.error as Error).message}</div>}

      {p && (
        <div className="space-y-3">
          {/* Quick scores */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {/* MBTI */}
            <div className="border border-border rounded-md p-3 bg-surface text-center">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">MBTI</div>
              <div className="text-xl font-bold text-accent">{p.mbti || "?"}</div>
            </div>
            {/* Big Five */}
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">BIG FIVE (OCEAN)</div>
              {Object.entries(p.big_five || {}).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between text-xs py-0.5">
                  <span className="text-text-secondary capitalize">{k}</span>
                  <div className="flex items-center gap-1">
                    <div className="w-12 h-1.5 bg-border rounded-full overflow-hidden">
                      <div className="h-full bg-accent rounded-full" style={{ width: `${Number(v) * 10}%` }} />
                    </div>
                    <span className="text-text font-semibold w-4 text-right">{v}</span>
                  </div>
                </div>
              ))}
            </div>
            {/* Dark Triad */}
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">DARK TRIAD</div>
              {Object.entries(p.dark_triad || {}).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between text-xs py-0.5">
                  <span className="text-text-secondary capitalize">{k}</span>
                  <div className="flex items-center gap-1">
                    <div className="w-12 h-1.5 bg-border rounded-full overflow-hidden">
                      <div className="h-full bg-loss rounded-full" style={{ width: `${Number(v) * 10}%` }} />
                    </div>
                    <span className="text-text font-semibold w-4 text-right">{v}</span>
                  </div>
                </div>
              ))}
            </div>
            {/* Negotiation Quick Stats */}
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">NEGOTIATION</div>
              {p.negotiation_style && (
                <div className="text-xs space-y-0.5">
                  <div className="text-text-secondary">Bluff rate: <span className="text-text font-semibold">{String((p.negotiation_style as Record<string, unknown>).bluff_rate_pct ?? "?")}%</span></div>
                  <div className="text-text-secondary">Follow-through: <span className="text-text font-semibold">{String((p.negotiation_style as Record<string, unknown>).follow_through_rate_pct ?? "?")}%</span></div>
                  <div className="text-text-secondary">Deadline: <span className="text-text">{String((p.negotiation_style as Record<string, unknown>).deadline_behavior || "?").slice(0, 60)}</span></div>
                </div>
              )}
            </div>
          </div>

          {/* Bluff Patterns */}
          {p.bluff_patterns && p.bluff_patterns.length > 0 && (
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-2">DOCUMENTED BLUFF PATTERNS</div>
              <div className="space-y-1.5">
                {p.bluff_patterns.map((b, i) => (
                  <div key={i} className="text-xs border border-border rounded p-2 bg-bg">
                    <div className="font-semibold text-text">{b.pattern}</div>
                    <div className="text-text-muted">Frequency: {b.frequency}</div>
                    <div className="text-text-secondary">Example: {b.example}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Escalation / De-escalation Tells */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {p.escalation_tells && p.escalation_tells.length > 0 && (
              <div className="border border-loss/30 rounded-md p-3 bg-loss/5">
                <div className="text-[10px] font-bold text-loss tracking-wider mb-2">ESCALATION TELLS</div>
                {p.escalation_tells.map((t, i) => (
                  <div key={i} className="text-xs mb-1.5">
                    <div className="font-semibold text-text">{t.tell}</div>
                    <div className="text-text-muted">Indicates: {t.indicates}</div>
                  </div>
                ))}
              </div>
            )}
            {p.deescalation_tells && p.deescalation_tells.length > 0 && (
              <div className="border border-gain/30 rounded-md p-3 bg-gain/5">
                <div className="text-[10px] font-bold text-gain tracking-wider mb-2">DE-ESCALATION TELLS</div>
                {p.deescalation_tells.map((t, i) => (
                  <div key={i} className="text-xs mb-1.5">
                    <div className="font-semibold text-text">{t.tell}</div>
                    <div className="text-text-muted">Indicates: {t.indicates}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Known Triggers */}
          {p.known_triggers && p.known_triggers.length > 0 && (
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-2">KNOWN TRIGGERS</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                {p.known_triggers.map((t, i) => (
                  <div key={i} className="text-xs border border-border rounded p-2 bg-bg">
                    <div className="font-semibold text-text">{t.trigger}</div>
                    <div className="text-text-secondary">Response: {t.typical_response}</div>
                    <div className="text-text-muted">Market: {t.market_impact}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Bluff Detection Rubric */}
          {p.bluff_detection_rubric && p.bluff_detection_rubric.length > 0 && (
            <div className="border border-accent/30 rounded-md p-3 bg-accent/5">
              <div className="text-[10px] font-bold text-accent tracking-wider mb-2">BLUFF DETECTION RUBRIC</div>
              <div className="space-y-1">
                {p.bluff_detection_rubric.map((r, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      r.bluff_indicator === "high" ? "bg-gain/20 text-gain" :
                      r.bluff_indicator === "medium" ? "bg-warning/20 text-warning" :
                      "bg-loss/20 text-loss"
                    }`}>{r.bluff_indicator}</span>
                    <span className="text-text">{r.factor}</span>
                    <span className="text-text-muted ml-auto">weight: {r.weight}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Full Profile Narrative */}
          {p.full_profile && (
            <details className="border border-border rounded-md bg-surface" open>
              <summary className="px-3 py-2 text-xs font-semibold text-text cursor-pointer hover:bg-surface-alt">
                Full Psychological Profile
              </summary>
              <div className="px-3 pb-3 border-t border-border pt-2">
                <Prose text={p.full_profile} />
              </div>
            </details>
          )}

          {/* Current Snapshot from Grok */}
          {p.current_behavioral_snapshot && (
            <div className="border border-border rounded-md p-3 bg-surface">
              <div className="text-[10px] font-bold text-text-muted tracking-wider mb-1">CURRENT BEHAVIORAL SNAPSHOT (Grok Live)</div>
              <p className="text-xs text-text-secondary leading-relaxed">{p.current_behavioral_snapshot}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
