"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import {
  fetchFredBatch,
  fetchFedMacroSentiment,
  fetchFedBalanceSheet,
  fetchCotPositioning,
  fetchOecdCli,
  fetchNextFomc,
  type StockTwitsItem,
  type PolymarketItem,
} from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";


const TABS = [
  "Signal Matrix",
  "Driver Trends",
  "Fed Policy",
  "FOMC Statement Diff",
  "Inflation",
  "Labor Market",
  "Yield Curve",
  "Sentiment",
];

// ── FRED drivers + metadata (mirrors Streamlit page) ─────────────────
interface DriverInfo {
  name: string;
  unit: string;
  yoy: boolean;
  color: string;
  category: string;
  fed_weight: "Primary" | "High" | "Medium" | "Low";
}

const FED_DRIVERS: Record<string, DriverInfo> = {
  CPIAUCSL:  { name: "CPI (All Items)",       unit: "index", yoy: true,  color: "#ff4b4b", category: "Inflation",  fed_weight: "Primary" },
  PCEPILFE:  { name: "Core PCE",              unit: "index", yoy: true,  color: "#ffaa00", category: "Inflation",  fed_weight: "Primary" },
  UNRATE:    { name: "Unemployment Rate",     unit: "%",     yoy: false, color: "#00d1ff", category: "Employment", fed_weight: "Primary" },
  PAYEMS:    { name: "Nonfarm Payrolls",      unit: "K",     yoy: false, color: "#00ff96", category: "Employment", fed_weight: "Primary" },
  FEDFUNDS:  { name: "Fed Funds Rate",        unit: "%",     yoy: false, color: "#ad7fff", category: "Fed",        fed_weight: "Primary" },
  T10Y2Y:    { name: "2s10s Yield Spread",    unit: "%",     yoy: false, color: "#ff69b4", category: "Rates",      fed_weight: "High" },
  DGS10:     { name: "10-Year Treasury Yield", unit: "%",    yoy: false, color: "#00bcd4", category: "Rates",      fed_weight: "High" },
  DGS2:      { name: "2-Year Treasury Yield",  unit: "%",    yoy: false, color: "#8bc34a", category: "Rates",      fed_weight: "High" },
  RSAFS:     { name: "Retail Sales",          unit: "$M",    yoy: true,  color: "#e91e63", category: "Consumer",   fed_weight: "Medium" },
  UMCSENT:   { name: "Consumer Sentiment",    unit: "index", yoy: false, color: "#ffc107", category: "Consumer",   fed_weight: "Medium" },
  INDPRO:    { name: "Industrial Production", unit: "index", yoy: true,  color: "#795548", category: "Production", fed_weight: "Medium" },
  GDP:       { name: "Real GDP",              unit: "$B",    yoy: true,  color: "#607d8b", category: "Growth",     fed_weight: "High" },
  HOUST:     { name: "Housing Starts",        unit: "K",     yoy: false, color: "#9c27b0", category: "Housing",    fed_weight: "Medium" },
  DTWEXBGS:  { name: "Trade-Weighted Dollar", unit: "index", yoy: false, color: "#4caf50", category: "FX",         fed_weight: "Medium" },
  ICSA:      { name: "Initial Jobless Claims", unit: "",     yoy: false, color: "#ff5722", category: "Employment", fed_weight: "High" },
  SAHMCURRENT: { name: "Sahm Rule Indicator", unit: "",      yoy: false, color: "#d50000", category: "Recession Signal", fed_weight: "High" },
  NFCI:      { name: "Chicago Fed FCI",       unit: "index", yoy: false, color: "#00897b", category: "Financial Conditions", fed_weight: "High" },
  VIXCLS:    { name: "VIX (Fear Index)",      unit: "",      yoy: false, color: "#f44336", category: "Market Stress", fed_weight: "Medium" },
  BAMLH0A0HYM2: { name: "HY Credit Spread",   unit: "%",     yoy: false, color: "#e65100", category: "Market Stress", fed_weight: "High" },
  T5YIE:     { name: "5Y Breakeven Inflation", unit: "%",    yoy: false, color: "#ff6f00", category: "Inflation Expectations", fed_weight: "High" },
  T10YIE:    { name: "10Y Breakeven Inflation", unit: "%",   yoy: false, color: "#ff8f00", category: "Inflation Expectations", fed_weight: "Medium" },
  PERMIT:    { name: "Building Permits",      unit: "K",     yoy: false, color: "#ce93d8", category: "Housing",    fed_weight: "Medium" },
  DGORDER:   { name: "Durable Goods Orders",  unit: "$M",    yoy: true,  color: "#80cbc4", category: "Production", fed_weight: "Medium" },
  JTSJOL:    { name: "JOLTS Job Openings",    unit: "K",     yoy: false, color: "#4dd0e1", category: "Employment", fed_weight: "High" },
  GDPNOW:    { name: "GDPNow (Atlanta Fed)",  unit: "%",     yoy: false, color: "#26a69a", category: "Growth",     fed_weight: "High" },
  DGS3MO:    { name: "3-Month Treasury",      unit: "%",     yoy: false, color: "#90a4ae", category: "Yield Curve", fed_weight: "Low" },
  DGS30:     { name: "30-Year Treasury",      unit: "%",     yoy: false, color: "#546e7a", category: "Yield Curve", fed_weight: "Medium" },
};
const DRIVER_IDS = Object.keys(FED_DRIVERS);

// ── FOMC static data (mirrors Streamlit) ─────────────────────────────

interface FomcStatement {
  text: string;
  rate: string;
  action: string;
  vote: string;
  dissent: string;
  forward_guidance: string;
}

const FOMC_STATEMENTS: Record<string, FomcStatement> = {
  "March 18-19, 2026": {
    text: "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. Uncertainty around the economic outlook has increased. The Committee is attentive to the risks to both sides of its dual mandate.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. The unemployment rate has stabilized at a low level in recent months, and labor market conditions remain solid. Inflation remains somewhat elevated.\n\nIn support of its goals, the Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent. In considering the extent and timing of additional adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks. The Committee is prepared to adjust the stance of monetary policy as appropriate if risks emerge that could impede the attainment of the Committee's goals.",
    rate: "3.50-3.75%", action: "Hold", vote: "11-1", dissent: "Waller (preferred cut)",
    forward_guidance: "The Committee is prepared to adjust the stance of monetary policy as appropriate if risks emerge.",
  },
  "January 28-29, 2026": {
    text: "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance. The economic outlook is uncertain, and the Committee is attentive to the risks to both sides of its dual mandate.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. The unemployment rate has stabilized at a low level in recent months, and labor market conditions remain solid. Inflation has made progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nIn support of its goals, the Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent. The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence that inflation is moving sustainably toward 2 percent.",
    rate: "3.50-3.75%", action: "Hold", vote: "12-0", dissent: "None",
    forward_guidance: "The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence.",
  },
  "December 17-18, 2025": {
    text: "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Labor market conditions have generally eased, and the unemployment rate has moved up but remains low. Inflation has made further progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nIn support of its goals, the Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 3-1/2 to 3-3/4 percent. The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance.",
    rate: "3.50-3.75%", action: "Cut 25bp", vote: "11-1", dissent: "Hammack (preferred hold)",
    forward_guidance: "The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance.",
  },
  "November 6-7, 2025": {
    text: "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Labor market conditions have generally eased, and the unemployment rate has moved up but remains low. Inflation has made progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nIn support of its goals, the Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent. In considering additional adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks.",
    rate: "3.75-4.00%", action: "Cut 25bp", vote: "12-0", dissent: "None",
    forward_guidance: "In considering additional adjustments to the target range, the Committee will carefully assess incoming data.",
  },
  "September 17-18, 2025": {
    text: "The Committee has gained greater confidence that inflation is moving sustainably toward 2 percent, and judges that the risks to achieving its employment and inflation goals are roughly in balance.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Job gains have slowed, and the unemployment rate has moved up but remains low. Inflation has made further progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nIn light of the progress on inflation and the balance of risks, the Committee decided to lower the target range for the federal funds rate by 1/2 percentage point to 4 to 4-1/4 percent.",
    rate: "4.00-4.25%", action: "Cut 50bp", vote: "11-1", dissent: "Bowman (preferred 25bp cut)",
    forward_guidance: "The Committee has gained greater confidence that inflation is moving sustainably toward 2 percent.",
  },
  "July 30-31, 2025": {
    text: "The Committee judges that the risks to achieving its employment and inflation goals continue to move into better balance. The economic outlook is uncertain, and the Committee is attentive to the risks to both sides of its dual mandate.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Job gains have moderated, and the unemployment rate has moved up but remains low. Inflation has made some further progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nThe Committee decided to maintain the target range for the federal funds rate at 4-1/4 to 4-1/2 percent. The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence that inflation is moving sustainably toward 2 percent.",
    rate: "4.25-4.50%", action: "Hold", vote: "12-0", dissent: "None",
    forward_guidance: "The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence.",
  },
  "June 11-12, 2025": {
    text: "The Committee judges that the risks to achieving its employment and inflation goals have moved toward better balance over the past year. The economic outlook is uncertain, and the Committee remains highly attentive to inflation risks.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Job gains have remained strong, and the unemployment rate has remained low. Inflation has eased over the past year but remains elevated.\n\nThe Committee decided to maintain the target range for the federal funds rate at 4-1/4 to 4-1/2 percent.",
    rate: "4.25-4.50%", action: "Hold", vote: "12-0", dissent: "None",
    forward_guidance: "The Committee remains highly attentive to inflation risks.",
  },
  "May 6-7, 2025": {
    text: "Uncertainty about the economic outlook has increased further. The Committee is attentive to the risks to both sides of its dual mandate and judges that the risks of higher unemployment and higher inflation have risen.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. The unemployment rate has stabilized at a low level in recent months, and labor market conditions remain solid. Inflation remains somewhat elevated.\n\nThe Committee decided to maintain the target range for the federal funds rate at 4-1/4 to 4-1/2 percent.",
    rate: "4.25-4.50%", action: "Hold", vote: "12-0", dissent: "None",
    forward_guidance: "The risks of higher unemployment and higher inflation have risen.",
  },
};

const FOMC_META: Record<string, { date: string; spy_1d: number; tlt_1d: number; dxy_1d: number }> = {
  "March 18-19, 2026": { date: "2026-03-19", spy_1d: -1.2, tlt_1d: 0.8, dxy_1d: -0.3 },
  "January 28-29, 2026": { date: "2026-01-29", spy_1d: 0.5, tlt_1d: -0.4, dxy_1d: 0.2 },
  "December 17-18, 2025": { date: "2025-12-18", spy_1d: -2.9, tlt_1d: -1.8, dxy_1d: 1.1 },
  "November 6-7, 2025": { date: "2025-11-07", spy_1d: 0.7, tlt_1d: 0.3, dxy_1d: -0.5 },
  "September 17-18, 2025": { date: "2025-09-18", spy_1d: 1.7, tlt_1d: 1.1, dxy_1d: -0.8 },
  "July 30-31, 2025": { date: "2025-07-31", spy_1d: 1.6, tlt_1d: -0.3, dxy_1d: 0.1 },
  "June 11-12, 2025": { date: "2025-06-12", spy_1d: 0.9, tlt_1d: 0.2, dxy_1d: -0.4 },
  "May 6-7, 2025": { date: "2025-05-07", spy_1d: 0.4, tlt_1d: -0.1, dxy_1d: 0.3 },
};

const HAWKISH_WORDS = [
  "elevated", "restrictive", "tightening", "attentive to inflation", "higher inflation",
  "not expect it will be appropriate to reduce", "uncertainty has increased",
  "risks have risen", "highly attentive to inflation risks",
];
const DOVISH_WORDS = [
  "progress", "eased", "greater confidence", "moving sustainably toward 2 percent",
  "roughly in balance", "better balance", "lower the target range", "decided to lower",
  "gained greater confidence",
];

// Dot plot data (March 2026 + December 2025)
const MAR26_DOTS: Record<string, Record<number, number>> = {
  "2026": { 3.625: 7, 3.375: 7, 3.125: 2, 2.875: 2, 2.625: 1 },
  "2027": { 3.875: 1, 3.625: 3, 3.375: 4, 3.125: 6, 2.875: 3, 2.625: 1, 2.375: 1 },
  "2028": { 3.875: 1, 3.625: 3, 3.375: 3, 3.125: 7, 2.875: 3, 2.625: 2 },
  "Longer Run": { 3.875: 1, 3.750: 1, 3.625: 1, 3.500: 1, 3.375: 2, 3.250: 1, 3.125: 3, 3.000: 5, 2.875: 2, 2.625: 2 },
};
const DEC25_DOTS: Record<string, Record<number, number>> = {
  "2026": { 4.000: 3, 3.875: 4, 3.625: 4, 3.375: 4, 3.125: 2, 2.875: 1, 2.125: 1 },
  "2027": { 4.000: 2, 3.875: 2, 3.625: 2, 3.375: 6, 3.125: 3, 2.875: 2, 2.625: 1 },
  "2028": { 4.000: 2, 3.875: 2, 3.625: 2, 3.375: 2, 3.125: 3, 2.875: 4 },
  "Longer Run": { 4.000: 1, 3.875: 2, 3.625: 3, 3.375: 3, 3.125: 6, 2.875: 4 },
};
const MAR26_MEDIANS: Record<string, number> = { "2026": 3.4, "2027": 3.1, "2028": 3.125, "Longer Run": 3.0 };
const DEC25_MEDIANS: Record<string, number> = { "2026": 3.4, "2027": 3.1, "2028": 3.0, "Longer Run": 3.0 };
const PERIODS = ["2026", "2027", "2028", "Longer Run"];

const SEP_ROWS: Array<[string, string, string, string, string]> = [
  ["GDP Growth", "2.4%", "2.3%", "2.1%", "2.0%"],
  ["Unemployment", "4.4%", "4.3%", "4.2%", "4.2%"],
  ["PCE Inflation", "2.7%", "2.2%", "2.0%", "2.0%"],
  ["Core PCE", "2.7%", "2.2%", "2.0%", "—"],
  ["Fed Funds (Median)", "3.4%", "3.1%", "3.1%", "3.0%"],
];

const REACTION_ROWS: Array<[string, string, string, string, string]> = [
  ["1", "Core PCE (YoY)", "> 2.5% or accelerating", "< 2.0% or decelerating", "Primary inflation gauge; Fed's 2% target"],
  ["2", "Unemployment Rate", "< 4.0% (tight labor)", "> 4.5% or rising fast", "Dual mandate; NAIRU ~4.0-4.2%"],
  ["3", "NFP (MoM change)", "> 200K (strong hiring)", "< 100K (weakening)", "Labor momentum; breakeven ~100-150K"],
  ["4", "Initial Claims", "< 200K (tight)", "> 300K or rising trend", "Leading indicator; weekly frequency"],
  ["5", "2s10s Spread", "N/A", "Inverted (< 0)", "Preceded every recession since 1970"],
  ["6", "Real GDP (YoY)", "> 3.0% (overheating)", "< 1.0% (stalling)", "Overall growth trajectory"],
  ["7", "Retail Sales (YoY)", "Strong growth", "Declining", "~70% of GDP is consumption"],
  ["8", "Consumer Sentiment", "Rising", "Falling sharply", "Forward-looking demand"],
];

const INFLATION_SERIES: Array<[string, string, string]> = [
  ["CPIAUCSL", "CPI All Items", "#ff4b4b"],
  ["CPILFESL", "Core CPI (ex F&E)", "#ffaa00"],
  ["PCEPILFE", "Core PCE", "#00d1ff"],
  ["CUUR0000SAH1", "Shelter", "#ad7fff"],
  ["CUUR0000SAF1", "Food", "#00ff87"],
  ["CUUR0000SETB01", "Gasoline", "#ff6b35"],
  ["CUSR0000SETA02", "Used Cars", "#ff2277"],
  ["CUSR0000SAM1", "Medical Care", "#00e0d0"],
];

const LABOR_SERIES: Array<[string, string]> = [
  ["PAYEMS", "Nonfarm Payrolls"],
  ["UNRATE", "Unemployment Rate"],
  ["ICSA", "Initial Jobless Claims"],
  ["JTSJOL", "JOLTS Job Openings"],
  ["CES0500000003", "Avg Hourly Earnings"],
  ["CIVPART", "Labor Force Participation"],
];

const YIELD_CURVE: Array<[string, string]> = [
  ["DGS1MO", "1M"], ["DGS3MO", "3M"], ["DGS6MO", "6M"], ["DGS1", "1Y"],
  ["DGS2", "2Y"], ["DGS3", "3Y"], ["DGS5", "5Y"], ["DGS7", "7Y"],
  ["DGS10", "10Y"], ["DGS20", "20Y"], ["DGS30", "30Y"],
];

const YIELD_SERIES = YIELD_CURVE.map((x) => x[0]);
const ALL_FRED_SERIES = [
  ...DRIVER_IDS,
  ...INFLATION_SERIES.map((x) => x[0]),
  ...LABOR_SERIES.map((x) => x[0]),
  ...YIELD_SERIES,
];
// dedupe
const UNIQUE_FRED_SERIES = Array.from(new Set(ALL_FRED_SERIES));

function latest(records: Array<Record<string, unknown>> | undefined) {
  if (!records || records.length === 0) return null;
  const r = records[records.length - 1];
  const prev = records.length > 1 ? records[records.length - 2] : r;
  return {
    value: Number(r.value ?? 0),
    prev: Number(prev.value ?? 0),
    date: String(r.date ?? ""),
  };
}

function yoyOf(records: Array<Record<string, unknown>> | undefined) {
  if (!records || records.length < 13) return null;
  const v1 = Number(records[records.length - 1].value ?? 0);
  const v0 = Number(records[records.length - 13].value ?? 0);
  if (v0 === 0) return null;
  return (v1 / v0 - 1) * 100;
}

// ═══════════════════════════════════════════════════════════════
// PAGE
// ═══════════════════════════════════════════════════════════════

export default function FedMacroPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);

  const fredQ = useQuery({
    queryKey: ["fed-macro-fred", UNIQUE_FRED_SERIES.length],
    queryFn: () => fetchFredBatch(UNIQUE_FRED_SERIES, 260),
    staleTime: 10 * 60_000,
  });
  const fomcQ = useQuery({ queryKey: ["fed-macro-next-fomc"], queryFn: fetchNextFomc, staleTime: 60 * 60_000 });

  const fredData = (fredQ.data ?? {}) as Record<string, Array<Record<string, unknown>>>;

  // ── Dual mandate scorecard ──
  const scorecardCards = useMemo(() => {
    const cards: Array<{ label: string; value: string; delta: string; color: string; deltaColor: "gain" | "loss" | "neutral" }> = [];
    const pce = fredData.PCEPILFE ?? [];
    if (pce.length >= 14) {
      const v1 = Number(pce[pce.length - 1].value);
      const v12 = Number(pce[pce.length - 13].value);
      const v2 = Number(pce[pce.length - 2].value);
      const v13 = Number(pce[pce.length - 14].value);
      const yoy = (v1 / v12 - 1) * 100;
      const prev = (v2 / v13 - 1) * 100;
      const chg = yoy - prev;
      cards.push({
        label: "Core PCE YoY",
        value: `${yoy.toFixed(1)}%`,
        delta: `${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%`,
        color: yoy > 3 ? t.loss : yoy > 2 ? t.spot : t.gain,
        deltaColor: chg > 0 ? "loss" : "gain",
      });
    }
    const ur = fredData.UNRATE ?? [];
    if (ur.length >= 2) {
      const v = Number(ur[ur.length - 1].value);
      const prev = Number(ur[ur.length - 2].value);
      const chg = v - prev;
      cards.push({
        label: "Unemployment",
        value: `${v.toFixed(1)}%`,
        delta: `${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%`,
        color: v > 5 ? t.loss : v > 4 ? t.spot : t.gain,
        deltaColor: chg > 0 ? "loss" : "gain",
      });
    }
    const ff = fredData.FEDFUNDS ?? [];
    if (ff.length >= 2) {
      const v = Number(ff[ff.length - 1].value);
      const prev = Number(ff[ff.length - 2].value);
      cards.push({
        label: "Fed Funds",
        value: `${v.toFixed(2)}%`,
        delta: `${v - prev >= 0 ? "+" : ""}${(v - prev).toFixed(2)}%`,
        color: t.accent,
        deltaColor: "neutral",
      });
    }
    const sp = fredData.T10Y2Y ?? [];
    if (sp.length > 0) {
      const v = Number(sp[sp.length - 1].value);
      cards.push({
        label: "2s10s Spread",
        value: `${v.toFixed(2)}%`,
        delta: v < 0 ? "Inverted" : "Normal",
        color: v < 0 ? t.loss : t.gain,
        deltaColor: v < 0 ? "loss" : "gain",
      });
    }
    const nfp = fredData.PAYEMS ?? [];
    if (nfp.length >= 2) {
      const chg = Number(nfp[nfp.length - 1].value) - Number(nfp[nfp.length - 2].value);
      cards.push({
        label: "NFP (MoM)",
        value: `${chg >= 0 ? "+" : ""}${chg.toFixed(0)}K`,
        delta: "jobs",
        color: chg > 0 ? t.gain : t.loss,
        deltaColor: chg > 0 ? "gain" : "loss",
      });
    }
    return cards;
  }, [fredData, t]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Fed &amp; Macro Drivers</h1>
        <p className="text-text-secondary text-sm mt-1">
          The key economic indicators the Federal Reserve watches most closely when setting monetary policy.
        </p>
      </div>

      {fredQ.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-2">Fetching {UNIQUE_FRED_SERIES.length} FRED series…</div>
        </div>
      )}

      {fredQ.error && <div className="card border-loss text-loss text-sm">Failed to load FRED data. Check API key.</div>}

      {Object.keys(fredData).length > 0 && (
        <>
          {/* Dual mandate scorecard */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-3">
              {scorecardCards.map((c) => (
                <div key={c.label} className="flex-1 min-w-[140px] text-center border border-border rounded px-2 py-2">
                  <div className="metric-label">{c.label}</div>
                  <div className="text-lg font-bold" style={{ color: c.color }}>{c.value}</div>
                  <div className={`text-xs ${c.deltaColor === "gain" ? "text-gain" : c.deltaColor === "loss" ? "text-loss" : "text-text-muted"}`}>{c.delta}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button
                key={tab}
                onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          {activeTab === 0 && <SignalMatrixTab fredData={fredData} fomcDate={fomcQ.data?.date} t={t} L={L} />}
          {activeTab === 1 && <DriverTrendsTab fredData={fredData} t={t} L={L} />}
          {activeTab === 2 && <FedPolicyTab t={t} L={L} />}
          {activeTab === 3 && <FomcDiffTab t={t} L={L} />}
          {activeTab === 4 && <InflationTab fredData={fredData} t={t} L={L} />}
          {activeTab === 5 && <LaborTab fredData={fredData} t={t} L={L} />}
          {activeTab === 6 && <YieldCurveTab fredData={fredData} t={t} L={L} />}
          {activeTab === 7 && <SentimentTab t={t} />}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 0: SIGNAL MATRIX
// ═══════════════════════════════════════════════════════════════

function SignalMatrixTab({
  fredData, fomcDate, t, L,
}: {
  fredData: Record<string, Array<Record<string, unknown>>>;
  fomcDate: string | null | undefined;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const rows = useMemo(() => {
    const out: Array<{ sid: string; name: string; category: string; display: string; change_display: string; signal: string; weight: string }> = [];
    for (const [sid, info] of Object.entries(FED_DRIVERS)) {
      const records = fredData[sid];
      if (!records || records.length === 0) continue;
      const last = Number(records[records.length - 1].value ?? 0);
      const prev = records.length > 1 ? Number(records[records.length - 2].value ?? 0) : last;
      const change = last - prev;
      const yoy = yoyOf(records);
      const prevYoy = records.length >= 14 ? (() => {
        const v0 = Number(records[records.length - 14].value ?? 0);
        if (v0 === 0) return null;
        return (Number(records[records.length - 2].value ?? 0) / v0 - 1) * 100;
      })() : null;

      let display: string;
      if (info.yoy && yoy !== null) display = `${yoy.toFixed(1)}% YoY`;
      else if (info.unit === "%") display = `${last.toFixed(2)}%`;
      else if (["K", "$M", "$B"].includes(info.unit)) display = `${last.toLocaleString(undefined, { maximumFractionDigits: 0 })} ${info.unit}`;
      else display = last.toLocaleString(undefined, { maximumFractionDigits: 1 });

      let signal = "Neutral";
      let changeDisplay = `${change >= 0 ? "+" : ""}${change.toFixed(2)}`;

      if (sid === "CPIAUCSL" || sid === "PCEPILFE") {
        if (yoy !== null && prevYoy !== null) {
          signal = yoy < prevYoy ? "Dovish" : yoy > prevYoy ? "Hawkish" : "Neutral";
          changeDisplay = `${yoy - prevYoy >= 0 ? "+" : ""}${(yoy - prevYoy).toFixed(2)}pp`;
        }
      } else if (["RSAFS", "GDP", "INDPRO", "DGORDER"].includes(sid)) {
        if (yoy !== null && prevYoy !== null) {
          signal = yoy > prevYoy ? "Hawkish" : yoy < prevYoy ? "Dovish" : "Neutral";
          changeDisplay = `${yoy - prevYoy >= 0 ? "+" : ""}${(yoy - prevYoy).toFixed(2)}pp`;
        }
      } else if (sid === "UNRATE") {
        signal = change > 0 ? "Dovish" : change < 0 ? "Hawkish" : "Neutral";
        changeDisplay = `${change >= 0 ? "+" : ""}${change.toFixed(1)}pp`;
      } else if (sid === "ICSA") {
        signal = change > 0 ? "Dovish" : change < 0 ? "Hawkish" : "Neutral";
        changeDisplay = `${change >= 0 ? "+" : ""}${change.toLocaleString()}`;
      } else if (sid === "PAYEMS") {
        signal = change > 150 ? "Hawkish" : change < 100 ? "Dovish" : "Neutral";
        changeDisplay = `${change >= 0 ? "+" : ""}${change.toFixed(0)}K`;
      } else if (["DGS10", "DGS2"].includes(sid)) {
        signal = change > 0 ? "Tightening" : change < 0 ? "Easing" : "Neutral";
        changeDisplay = `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
      } else if (sid === "T10Y2Y") {
        signal = last < 0 ? "Recession Risk" : "Normal";
      } else if (sid === "SAHMCURRENT") {
        signal = last >= 0.5 ? "Recession Risk" : "Normal";
        changeDisplay = last.toFixed(2);
      } else if (sid === "NFCI") {
        signal = last > 0 ? "Tightening" : "Easing";
        changeDisplay = last.toFixed(2);
      } else if (sid === "VIXCLS") {
        signal = last > 25 ? "Stress" : "Calm";
      } else if (sid === "BAMLH0A0HYM2") {
        signal = last > 4 ? "Stress" : "Calm";
      } else if (sid === "T5YIE" || sid === "T10YIE") {
        signal = last > 2.5 ? "Hawkish" : last < 2.0 ? "Dovish" : "Neutral";
      } else if (sid === "FEDFUNDS") {
        signal = "Neutral";
      }

      out.push({
        sid, name: info.name, category: info.category,
        display, change_display: changeDisplay, signal, weight: info.fed_weight,
      });
    }
    return out;
  }, [fredData]);

  const signalColors: Record<string, string> = {
    Dovish: t.gain, Easing: t.gain, Calm: t.gain, Normal: t.muted, Neutral: t.muted,
    Hawkish: t.loss, Tightening: t.loss, "Recession Risk": t.loss, Stress: t.loss,
  };
  const weightColors: Record<string, string> = {
    Primary: t.accent, High: t.spot, Medium: t.muted, Low: t.muted,
  };

  const nHawk = rows.filter((r) => ["Hawkish", "Tightening", "Stress", "Recession Risk"].includes(r.signal)).length;
  const nDove = rows.filter((r) => ["Dovish", "Easing", "Calm"].includes(r.signal)).length;
  const nNeutral = rows.filter((r) => ["Neutral", "Normal"].includes(r.signal)).length;
  const nTotal = rows.length;
  const hawkPct = nTotal > 0 ? (nHawk / nTotal) * 100 : 0;
  const dovePct = nTotal > 0 ? (nDove / nTotal) * 100 : 0;
  const netLabel = hawkPct > dovePct + 10 ? "HAWKISH" : dovePct > hawkPct + 10 ? "DOVISH" : "MIXED";
  const netColor = netLabel === "HAWKISH" ? t.loss : netLabel === "DOVISH" ? t.gain : t.spot;

  // Taylor Rule
  const cpi = fredData.CPIAUCSL ?? [];
  const ur = fredData.UNRATE ?? [];
  const ff = fredData.FEDFUNDS ?? [];
  let taylor: { actual: number; taylor: number; gap: number; cpi_yoy: number; unemployment: number } | null = null;
  if (cpi.length >= 13 && ur.length > 0 && ff.length > 0) {
    const cpiYoy = (Number(cpi[cpi.length - 1].value) / Number(cpi[cpi.length - 13].value) - 1) * 100;
    const unemployment = Number(ur[ur.length - 1].value);
    const fedRate = Number(ff[ff.length - 1].value);
    const rStar = 2.5, piStar = 2.0, nairu = 4.2;
    const rule = Math.max(0, rStar + 0.5 * (cpiYoy - piStar) + 0.5 * (nairu - unemployment));
    taylor = { actual: fedRate, taylor: rule, gap: fedRate - rule, cpi_yoy: cpiYoy, unemployment };
  }

  // FOMC countdown
  let daysToFomc: number | null = null;
  let fomcDateObj: Date | null = null;
  if (fomcDate) {
    fomcDateObj = new Date(fomcDate);
    const now = new Date();
    daysToFomc = Math.floor((fomcDateObj.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
  }

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-1">Policy signal matrix</div>
        <div className="text-xs text-text-muted mb-2">Where each indicator stands relative to thresholds that influence Fed policy.</div>
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr><th>Indicator</th><th>Category</th><th>Current</th><th>Change</th><th>Signal</th><th>Weight</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.sid}>
                  <td className="font-semibold">{r.name}</td>
                  <td className="text-text-muted">{r.category}</td>
                  <td className="font-data font-semibold">{r.display}</td>
                  <td className="font-data">{r.change_display}</td>
                  <td className="font-semibold" style={{ color: signalColors[r.signal] ?? t.muted }}>{r.signal}</td>
                  <td className="text-xs" style={{ color: weightColors[r.weight] ?? t.muted }}>{r.weight}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className="card card-compact text-center" style={{ border: `2px solid ${netColor}` }}>
          <div className="metric-label">Net signal</div>
          <div className="text-xl font-bold" style={{ color: netColor }}>{netLabel}</div>
        </div>
        <Metric label="Hawkish" value={`${nHawk}/${nTotal}`} delta={`${hawkPct.toFixed(0)}%`} deltaType="loss" />
        <Metric label="Dovish" value={`${nDove}/${nTotal}`} delta={`${dovePct.toFixed(0)}%`} deltaType="gain" />
        <Metric label="Neutral" value={`${nNeutral}/${nTotal}`} />
      </div>

      {taylor && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Taylor Rule vs actual rate</div>
          <div className="flex flex-wrap gap-6">
            <Metric label="Actual rate" value={`${taylor.actual.toFixed(2)}%`} />
            <Metric label="Taylor Rule" value={`${taylor.taylor.toFixed(2)}%`} />
            <Metric
              label="Gap"
              value={`${taylor.gap >= 0 ? "+" : ""}${taylor.gap.toFixed(2)}%`}
              delta={Math.abs(taylor.gap) < 0.5 ? "About Right" : taylor.gap > 0 ? "Too Tight" : "Too Loose"}
              deltaType={Math.abs(taylor.gap) < 0.5 ? "neutral" : taylor.gap > 0 ? "loss" : "gain"}
            />
          </div>
          <div className="text-xs text-text-muted mt-2">
            Inputs: CPI YoY {taylor.cpi_yoy.toFixed(1)}%, Unemployment {taylor.unemployment.toFixed(1)}%, r* = 2.5%, NAIRU = 4.2%.
          </div>
        </div>
      )}

      {daysToFomc !== null && fomcDateObj && (
        <div className="card card-compact text-center" style={{ border: `1px solid ${daysToFomc <= 7 ? t.loss : daysToFomc <= 21 ? t.spot : t.accent}` }}>
          <div className="metric-label">Next FOMC meeting</div>
          <div className="text-3xl font-bold" style={{ color: daysToFomc <= 7 ? t.loss : daysToFomc <= 21 ? t.spot : t.accent }}>{daysToFomc}d</div>
          <div className="text-xs text-text-muted">{fomcDateObj.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}</div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 1: DRIVER TRENDS (sparklines)
// ═══════════════════════════════════════════════════════════════

function DriverTrendsTab({ fredData, t, L }: { fredData: Record<string, Array<Record<string, unknown>>>; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const items = Object.entries(FED_DRIVERS).filter(([sid]) => (fredData[sid]?.length ?? 0) > 0);
  return (
    <div className="card">
      <div className="text-sm font-semibold mb-2">Driver trend charts</div>
      <div className="text-xs text-text-muted mb-3">Dashed line = mean (middle 95% of data).</div>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {items.map(([sid, info]) => {
          const records = fredData[sid] ?? [];
          const last = Number(records[records.length - 1]?.value ?? 0);
          const prev = records.length > 1 ? Number(records[records.length - 2].value) : last;
          const change = last - prev;

          let displayVal: string;
          let changeStr: string;
          let xData: string[];
          let yData: number[];

          if (info.yoy && records.length >= 13) {
            yData = records.slice(12).map((r, i) => {
              const v1 = Number(r.value);
              const v0 = Number(records[i].value ?? 0);
              return v0 > 0 ? (v1 / v0 - 1) * 100 : 0;
            });
            xData = records.slice(12).map((r) => String(r.date ?? ""));
            const yoyNow = yData[yData.length - 1];
            displayVal = `${yoyNow.toFixed(1)}%`;
            changeStr = `${change >= 0 ? "+" : ""}${change.toFixed(2)}`;
          } else {
            yData = records.map((r) => Number(r.value ?? 0));
            xData = records.map((r) => String(r.date ?? ""));
            if (info.unit === "%") {
              displayVal = `${last.toFixed(2)}%`;
              changeStr = `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
            } else if (sid === "PAYEMS") {
              displayVal = `${change >= 0 ? "+" : ""}${change.toFixed(0)}K`;
              changeStr = "";
            } else {
              displayVal = last.toLocaleString(undefined, { maximumFractionDigits: 1 });
              changeStr = `${change >= 0 ? "+" : ""}${change.toFixed(1)}`;
            }
          }

          const valid = yData.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
          const lo = valid.length > 10 ? valid[Math.floor(valid.length * 0.025)] : null;
          const hi = valid.length > 10 ? valid[Math.floor(valid.length * 0.975)] : null;
          const trimmed = lo !== null && hi !== null ? valid.filter((v) => v >= lo && v <= hi) : valid;
          const meanVal = trimmed.length > 0 ? trimmed.reduce((s, v) => s + v, 0) / trimmed.length : null;

          return (
            <div key={sid} className="border border-border rounded p-2">
              <div className="text-xs font-semibold truncate">{info.name}</div>
              <div className="text-xs">
                <span className="font-data">{displayVal}</span>{" "}
                {changeStr && <span className={`font-data ${change >= 0 ? "text-gain" : "text-loss"}`}>{changeStr}</span>}
              </div>
              <Plot
                data={[
                  { x: xData, y: yData, type: "scatter" as const, mode: "lines" as const, line: { color: info.color, width: 1.5 }, hoverinfo: "skip" as const },
                ]}
                layout={{
                  height: 72, ...L,
                  margin: { l: 4, r: 4, t: 4, b: 4 },
                  xaxis: { visible: false },
                  yaxis: { visible: false },
                  showlegend: false,
                  shapes: meanVal !== null ? [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: meanVal, y1: meanVal, line: { color: t.muted, dash: "dot", width: 1 } }] : [],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 2: FED POLICY (dot plot + SEP + reaction + balance sheet + COT + CLI)
// ═══════════════════════════════════════════════════════════════

function FedPolicyTab({ t, L }: { t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const bsQ = useQuery({ queryKey: ["fm-bs"], queryFn: fetchFedBalanceSheet, staleTime: 30 * 60_000 });
  const cotQ = useQuery({ queryKey: ["fm-cot"], queryFn: fetchCotPositioning, staleTime: 30 * 60_000 });
  const cliQ = useQuery({ queryKey: ["fm-oecd"], queryFn: fetchOecdCli, staleTime: 60 * 60_000 });

  const dotTraces: Array<Record<string, unknown>> = [];
  PERIODS.forEach((period, pi) => {
    // March 2026 dots (current)
    for (const [rate, count] of Object.entries(MAR26_DOTS[period] ?? {})) {
      const r = Number(rate);
      const c = count as number;
      for (let i = 0; i < c; i++) {
        const xOffset = (i - (c - 1) / 2) * 0.03;
        dotTraces.push({
          x: [pi + xOffset],
          y: [r],
          type: "scatter",
          mode: "markers",
          marker: { size: 10, color: t.accent, symbol: "circle", line: { width: 1, color: t.paper } },
          showlegend: false,
          hovertemplate: `<b>Mar 2026</b><br>${period}: ${r.toFixed(3)}%<extra></extra>`,
        });
      }
    }
    // Dec 2025 faded
    for (const [rate, count] of Object.entries(DEC25_DOTS[period] ?? {})) {
      const r = Number(rate);
      const c = count as number;
      for (let i = 0; i < c; i++) {
        const xOffset = (i - (c - 1) / 2) * 0.03;
        dotTraces.push({
          x: [pi + xOffset],
          y: [r],
          type: "scatter",
          mode: "markers",
          marker: { size: 7, color: "rgba(255,170,0,0.4)", symbol: "circle" },
          showlegend: false,
          hovertemplate: `<b>Dec 2025</b><br>${period}: ${r.toFixed(3)}%<extra></extra>`,
        });
      }
    }
  });
  dotTraces.push({
    x: PERIODS.map((_, i) => i),
    y: PERIODS.map((p) => MAR26_MEDIANS[p]),
    type: "scatter",
    mode: "lines+markers",
    name: "Mar 2026 Median",
    line: { color: t.accent, width: 2, dash: "dash" },
    marker: { size: 8, symbol: "diamond" },
  });
  dotTraces.push({
    x: PERIODS.map((_, i) => i),
    y: PERIODS.map((p) => DEC25_MEDIANS[p]),
    type: "scatter",
    mode: "lines+markers",
    name: "Dec 2025 Median",
    line: { color: t.spot, width: 1.5, dash: "dot" },
    marker: { size: 6, symbol: "diamond" },
  });

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-1">FOMC dot plot — March 2026 vs December 2025</div>
        <Plot
          data={dotTraces}
          layout={{
            height: 500, ...L,
            xaxis: { tickvals: PERIODS.map((_, i) => i), ticktext: PERIODS, gridcolor: t.grid },
            yaxis: { title: "Federal funds rate (%)", dtick: 0.25, range: [2.0, 4.25], gridcolor: t.grid },
            hovermode: "closest",
            legend: { orientation: "h", y: 1.05, xanchor: "right", x: 1 },
            shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 3.625, y1: 3.625, line: { color: t.muted, width: 1 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
        <div className="text-xs text-text-muted mt-1">Cyan = March 2026 | Faded orange = December 2025 | Diamonds = median</div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Summary of Economic Projections (March 2026)</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th></th><th>2026</th><th>2027</th><th>2028</th><th>Longer Run</th></tr>
          </thead>
          <tbody>
            {SEP_ROWS.map((row) => (
              <tr key={row[0]}>
                <td className="font-semibold">{row[0]}</td>
                <td className="font-data">{row[1]}</td>
                <td className="font-data">{row[2]}</td>
                <td className="font-data">{row[3]}</td>
                <td className="font-data">{row[4]}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="text-xs text-text-muted mt-2">
          Inflation projection raised to 2.7% from 2.5% reflecting Iran oil shock. 14 of 19 members see 0-1 cuts in 2026 (vs 7 of 19 in December).
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Fed reaction function</div>
        <div className="text-xs text-text-muted mb-2">Simplified model of how the Fed weighs each driver.</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>#</th><th>Driver</th><th style={{ color: t.loss }}>Hawkish</th><th style={{ color: t.gain }}>Dovish</th><th>Rationale</th></tr>
          </thead>
          <tbody>
            {REACTION_ROWS.map((row) => (
              <tr key={row[0]}>
                <td className="text-text-muted">{row[0]}</td>
                <td className="font-semibold">{row[1]}</td>
                <td style={{ color: t.loss }} className="text-xs">{row[2]}</td>
                <td style={{ color: t.gain }} className="text-xs">{row[3]}</td>
                <td className="text-text-muted text-xs">{row[4]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Balance Sheet */}
      {bsQ.data && Object.keys(bsQ.data.series).length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Fed balance sheet &amp; liquidity</div>
          <div className="text-xs text-text-muted mb-2">Net liquidity = Total Assets − TGA − Reverse Repo. Dominant driver of risk asset prices.</div>
          <div className="flex flex-wrap gap-6 mb-3">
            <Metric label="Total Assets" value={bsQ.data.snapshot.total_assets != null ? `$${bsQ.data.snapshot.total_assets}T` : "—"} />
            <Metric label="TGA" value={bsQ.data.snapshot.tga != null ? `$${bsQ.data.snapshot.tga}B` : "—"} />
            <Metric label="Reverse Repo" value={bsQ.data.snapshot.rrp != null ? `$${bsQ.data.snapshot.rrp}B` : "—"} />
            <Metric
              label="Net Liquidity"
              value={bsQ.data.snapshot.net_liquidity != null ? `$${bsQ.data.snapshot.net_liquidity}T` : "—"}
              delta={bsQ.data.snapshot.net_liq_change != null ? `${bsQ.data.snapshot.net_liq_change >= 0 ? "+" : ""}$${bsQ.data.snapshot.net_liq_change.toFixed(0)}B/mo` : undefined}
              deltaType={bsQ.data.snapshot.net_liq_change != null && bsQ.data.snapshot.net_liq_change >= 0 ? "gain" : "loss"}
            />
          </div>
          <Plot
            data={Object.entries(bsQ.data.series).map(([label, values]) => ({
              x: bsQ.data!.dates,
              y: values.map((v) => v !== null ? v / 1e6 : null),
              type: "scatter" as const,
              mode: "lines" as const,
              name: label,
            }))}
            layout={{ height: 360, ...L, yaxis: { title: "$ Trillions", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 } }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* COT Positioning */}
      {cotQ.data && Object.keys(cotQ.data.positioning).length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Managed money positioning (CFTC COT)</div>
          <div className="text-xs text-text-muted mb-2">Hedge fund / CTA positioning from weekly reports. Extremes often precede reversals.</div>
          <div className="flex flex-wrap gap-3">
            {Object.entries(cotQ.data.positioning).map(([contract, pos]) => (
              <Metric
                key={contract}
                label={contract}
                value={`${pos.direction} (${pos.net_pct_oi >= 0 ? "+" : ""}${pos.net_pct_oi.toFixed(1)}%)`}
                delta={`${pos.change >= 0 ? "+" : ""}${pos.change.toLocaleString()} weekly`}
                deltaType={pos.direction.toLowerCase() === "long" ? "gain" : "loss"}
              />
            ))}
          </div>
        </div>
      )}

      {/* OECD CLI */}
      {cliQ.data && Object.keys(cliQ.data.series).length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">OECD Composite Leading Indicators</div>
          <div className="text-xs text-text-muted mb-2">CLI leads GDP by 6-9 months. Above 100 = expansion, below 100 = contraction.</div>
          <Plot
            data={Object.entries(cliQ.data.series).map(([country, values]) => ({
              x: cliQ.data!.dates,
              y: values,
              type: "scatter" as const,
              mode: "lines" as const,
              name: country,
            }))}
            layout={{
              height: 360, ...L,
              yaxis: { title: "CLI (100 = trend)", gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.18 },
              shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: t.muted, dash: "dash", width: 1 } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 3: FOMC STATEMENT DIFF
// ═══════════════════════════════════════════════════════════════

function FomcDiffTab({ t, L }: { t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const dates = Object.keys(FOMC_STATEMENTS);
  const [newerDate, setNewerDate] = useState(dates[0]);
  const [olderDate, setOlderDate] = useState(dates[1]);

  const toneData = [...dates].reverse().map((d) => {
    const text = FOMC_STATEMENTS[d].text.toLowerCase();
    const hawk = HAWKISH_WORDS.filter((w) => text.includes(w.toLowerCase())).length;
    const dove = DOVISH_WORDS.filter((w) => text.includes(w.toLowerCase())).length;
    return { meeting: d.split(",")[0], hawk, dove, score: hawk - dove };
  });

  const diffHtml = useMemo(() => {
    const older = FOMC_STATEMENTS[olderDate]?.text ?? "";
    const newer = FOMC_STATEMENTS[newerDate]?.text ?? "";
    return wordDiffHtml(older, newer, t);
  }, [olderDate, newerDate, t]);

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-2">Meeting history &amp; dissents</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>Meeting</th><th>Action</th><th>Rate</th><th>Vote</th><th>Dissent</th><th>SPY 1D</th><th>TLT 1D</th><th>DXY 1D</th></tr>
          </thead>
          <tbody>
            {dates.map((d) => {
              const m = FOMC_STATEMENTS[d];
              const meta = FOMC_META[d];
              return (
                <tr key={d}>
                  <td>{d.split(",")[0]}</td>
                  <td className={`font-semibold ${m.action.includes("Cut") ? "text-gain" : m.action.includes("Hike") ? "text-loss" : "text-spot"}`}>{m.action}</td>
                  <td className="font-data">{m.rate}</td>
                  <td className="font-data">{m.vote}</td>
                  <td className="text-xs">{m.dissent}</td>
                  <td className={`font-data ${meta?.spy_1d >= 0 ? "text-gain" : "text-loss"}`}>{meta ? `${meta.spy_1d >= 0 ? "+" : ""}${meta.spy_1d.toFixed(1)}%` : "—"}</td>
                  <td className={`font-data ${meta?.tlt_1d >= 0 ? "text-gain" : "text-loss"}`}>{meta ? `${meta.tlt_1d >= 0 ? "+" : ""}${meta.tlt_1d.toFixed(1)}%` : "—"}</td>
                  <td className={`font-data ${meta?.dxy_1d >= 0 ? "text-gain" : "text-loss"}`}>{meta ? `${meta.dxy_1d >= 0 ? "+" : ""}${meta.dxy_1d.toFixed(1)}%` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-1">Hawkish / Dovish tone score</div>
        <div className="text-xs text-text-muted mb-2">Counts signal words in each statement.</div>
        <Plot
          data={[
            { type: "bar" as const, x: toneData.map((d) => d.meeting), y: toneData.map((d) => d.hawk), name: "Hawkish", marker: { color: t.loss }, opacity: 0.7 },
            { type: "bar" as const, x: toneData.map((d) => d.meeting), y: toneData.map((d) => -d.dove), name: "Dovish", marker: { color: t.gain }, opacity: 0.7 },
            { type: "scatter" as const, x: toneData.map((d) => d.meeting), y: toneData.map((d) => d.score), mode: "lines+markers" as const, name: "Net Score", line: { color: t.accent, width: 3 }, marker: { size: 8 } },
          ]}
          layout={{ height: 320, ...L, barmode: "overlay" as const, yaxis: { title: "Word count", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 } }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Forward guidance evolution</div>
        <div className="space-y-2">
          {dates.map((d) => {
            const m = FOMC_STATEMENTS[d];
            const actColor = m.action.includes("Cut") ? t.gain : m.action.includes("Hike") ? t.loss : t.spot;
            return (
              <div key={d} style={{ borderLeft: `3px solid ${actColor}`, padding: "6px 12px", background: `${actColor}15` }}>
                <div className="text-xs font-semibold" style={{ color: actColor }}>
                  {d.split(",")[0]} ({m.action})
                </div>
                <div className="text-xs mt-1">&ldquo;{m.forward_guidance}&rdquo;</div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Statement diff</div>
        <div className="text-xs text-text-muted mb-2">Green = new language. Red struck-through = removed.</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
          <div>
            <label className="metric-label">Current statement</label>
            <select value={newerDate} onChange={(e) => setNewerDate(e.target.value)} className="mt-0.5 w-full px-3 py-1.5 border border-border rounded text-sm bg-surface">
              {dates.slice(0, -1).map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div>
            <label className="metric-label">Previous statement</label>
            <select value={olderDate} onChange={(e) => setOlderDate(e.target.value)} className="mt-0.5 w-full px-3 py-1.5 border border-border rounded text-sm bg-surface">
              {dates.filter((d) => dates.indexOf(d) > dates.indexOf(newerDate)).map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
        </div>
        {/* xss-safe: diffHtml is produced by wordDiffHtml() which escapes inserts/deletes via escapeHtml(); inputs are trusted Fed-statement text */}
        <div className="border border-border rounded p-3 text-xs leading-relaxed" dangerouslySetInnerHTML={{ __html: diffHtml }} />
      </div>
    </div>
  );
}

function wordDiffHtml(older: string, newer: string, t: ReturnType<typeof getChartTheme>): string {
  const olderWords = older.split(/\s+/);
  const newerWords = newer.split(/\s+/);
  const ops = myersDiff(olderWords, newerWords);
  const addStyle = `background:${t.gain}30;color:${t.gain};padding:1px 3px;border-radius:2px;font-weight:600;`;
  const delStyle = `background:${t.loss}30;color:${t.loss};padding:1px 3px;border-radius:2px;text-decoration:line-through;`;
  const parts: string[] = [];
  for (const op of ops) {
    if (op.type === "equal") parts.push(op.tokens.join(" "));
    else if (op.type === "insert") parts.push(`<span style="${addStyle}">${escapeHtml(op.tokens.join(" "))}</span>`);
    else parts.push(`<span style="${delStyle}">${escapeHtml(op.tokens.join(" "))}</span>`);
  }
  return parts.join(" ");
}

type DiffOp = { type: "equal" | "insert" | "delete"; tokens: string[] };

function myersDiff(a: string[], b: string[]): DiffOp[] {
  // Compute LCS then produce opcodes
  const n = a.length, m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  const ops: DiffOp[] = [];
  let i = n, j = m;
  const equalStack: string[] = [];
  const insertStack: string[] = [];
  const deleteStack: string[] = [];
  const flush = () => {
    if (deleteStack.length) { ops.unshift({ type: "delete", tokens: [...deleteStack].reverse() }); deleteStack.length = 0; }
    if (insertStack.length) { ops.unshift({ type: "insert", tokens: [...insertStack].reverse() }); insertStack.length = 0; }
    if (equalStack.length) { ops.unshift({ type: "equal", tokens: [...equalStack].reverse() }); equalStack.length = 0; }
  };
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      if (deleteStack.length || insertStack.length) flush();
      equalStack.push(a[i - 1]);
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      if (equalStack.length) flush();
      insertStack.push(b[j - 1]);
      j--;
    } else {
      if (equalStack.length) flush();
      deleteStack.push(a[i - 1]);
      i--;
    }
  }
  flush();
  return ops;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ═══════════════════════════════════════════════════════════════
// TAB 4: INFLATION DEEP DIVE
// ═══════════════════════════════════════════════════════════════

function InflationTab({ fredData, t, L }: { fredData: Record<string, Array<Record<string, unknown>>>; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const avail = INFLATION_SERIES.filter(([sid]) => (fredData[sid]?.length ?? 0) >= 13);

  const traces = avail.map(([sid, label, color]) => {
    const records = fredData[sid] ?? [];
    const yoyVals = records.slice(12).map((r, i) => {
      const v = Number(r.value);
      const v0 = Number(records[i].value ?? 0);
      return v0 > 0 ? (v / v0 - 1) * 100 : 0;
    });
    const xData = records.slice(12).map((r) => String(r.date ?? ""));
    return { x: xData, y: yoyVals, type: "scatter" as const, mode: "lines" as const, name: label, line: { color, width: 2 } };
  });

  const currentRows = avail.map(([sid, label]) => {
    const records = fredData[sid] ?? [];
    const n = records.length;
    const yoy = (Number(records[n - 1].value) / Number(records[n - 13].value) - 1) * 100;
    const prevYoy = n >= 14 ? (Number(records[n - 2].value) / Number(records[n - 14].value) - 1) * 100 : yoy;
    const mom = n > 1 ? (Number(records[n - 1].value) / Number(records[n - 2].value) - 1) * 100 : 0;
    const direction = yoy < prevYoy ? "Falling" : yoy > prevYoy ? "Rising" : "Flat";
    return { sid, label, yoy, mom, direction };
  });

  const sticky = ["CUUR0000SAH1", "CUSR0000SAM1"].filter((s) => avail.some(([sid]) => sid === s));
  const flexible = ["CUUR0000SETB01", "CUSR0000SETA02"].filter((s) => avail.some(([sid]) => sid === s));

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-1">Inflation decomposition — YoY</div>
        <Plot
          data={traces}
          layout={{
            height: 420, ...L,
            yaxis: { title: "YoY (%)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            legend: { orientation: "h", y: -0.18 },
            shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 2, y1: 2, line: { color: t.gain, dash: "dash", width: 1 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Current readings</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>Component</th><th>YoY (%)</th><th>MoM (%)</th><th>Direction</th><th>Annualized MoM</th></tr>
          </thead>
          <tbody>
            {currentRows.map((r) => (
              <tr key={r.sid} style={{ background: r.direction === "Falling" ? `${t.gain}10` : r.direction === "Rising" ? `${t.loss}10` : undefined }}>
                <td className="font-semibold">{r.label}</td>
                <td className="font-data">{r.yoy.toFixed(1)}%</td>
                <td className="font-data">{r.mom.toFixed(2)}%</td>
                <td className={`font-semibold ${r.direction === "Falling" ? "text-gain" : r.direction === "Rising" ? "text-loss" : "text-text-muted"}`}>{r.direction}</td>
                <td className="font-data">{(r.mom * 12).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(sticky.length > 0 || flexible.length > 0) && (
        <div className="card">
          <div className="text-sm font-semibold mb-2">Sticky vs flexible inflation</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="metric-label mb-1">Sticky components</div>
              {sticky.map((sid) => {
                const row = currentRows.find((r) => r.sid === sid);
                return row ? <Metric key={sid} label={row.label} value={`${row.yoy.toFixed(1)}% YoY`} /> : null;
              })}
            </div>
            <div>
              <div className="metric-label mb-1">Flexible components</div>
              {flexible.map((sid) => {
                const row = currentRows.find((r) => r.sid === sid);
                return row ? <Metric key={sid} label={row.label} value={`${row.yoy.toFixed(1)}% YoY`} /> : null;
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 5: LABOR MARKET
// ═══════════════════════════════════════════════════════════════

function LaborTab({ fredData, t, L }: { fredData: Record<string, Array<Record<string, unknown>>>; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const ur = fredData.UNRATE ?? [];
  const nfp = fredData.PAYEMS ?? [];
  const claims = fredData.ICSA ?? [];
  const ahe = fredData.CES0500000003 ?? [];

  const nfpChanges = nfp.length > 1 ? nfp.slice(1).map((r, i) => ({
    date: String(r.date ?? ""),
    change: Number(r.value) - Number(nfp[i].value ?? 0),
  })) : [];

  const urLast = ur.length > 0 ? Number(ur[ur.length - 1].value) : NaN;
  const urPrev = ur.length > 1 ? Number(ur[ur.length - 2].value) : urLast;
  const nfpChange = nfp.length > 1 ? Number(nfp[nfp.length - 1].value) - Number(nfp[nfp.length - 2].value) : 0;
  const claimsLast = claims.length > 0 ? Number(claims[claims.length - 1].value) : NaN;
  const aheLast = ahe.length > 0 ? Number(ahe[ahe.length - 1].value) : NaN;

  return (
    <div className="space-y-4">
      <div className="card card-compact flex flex-wrap gap-6">
        <Metric
          label="Unemployment"
          value={Number.isFinite(urLast) ? `${urLast.toFixed(1)}%` : "—"}
          delta={Number.isFinite(urLast - urPrev) ? `${urLast - urPrev >= 0 ? "+" : ""}${(urLast - urPrev).toFixed(1)}%` : undefined}
          deltaType={urLast - urPrev > 0 ? "loss" : "gain"}
        />
        <Metric label="NFP change" value={`${nfpChange >= 0 ? "+" : ""}${nfpChange.toFixed(0)}K`} deltaType={nfpChange > 0 ? "gain" : "loss"} />
        <Metric label="Initial claims" value={Number.isFinite(claimsLast) ? claimsLast.toLocaleString() : "—"} />
        <Metric label="Avg hourly earnings" value={Number.isFinite(aheLast) ? `$${aheLast.toFixed(2)}` : "—"} />
      </div>

      {nfpChanges.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Monthly payroll changes</div>
          <Plot
            data={[{
              type: "bar" as const,
              x: nfpChanges.map((r) => r.date),
              y: nfpChanges.map((r) => r.change),
              marker: { color: nfpChanges.map((r) => r.change > 0 ? t.gain : t.loss) },
            }]}
            layout={{ height: 320, ...L, yaxis: { title: "Monthly change (K)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 150, y1: 150, line: { color: t.spot, dash: "dot", width: 1 } }] }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {fredData.JTSJOL && fredData.JTSJOL.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Job openings (JOLTS)</div>
          <Plot
            data={[{
              x: fredData.JTSJOL.map((r) => String(r.date ?? "")),
              y: fredData.JTSJOL.map((r) => Number(r.value)),
              type: "scatter" as const,
              mode: "lines" as const,
              line: { color: t.accent, width: 2 },
            }]}
            layout={{ height: 300, ...L, yaxis: { title: "Thousands", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      <div className="card">
        <div className="text-sm font-semibold mb-2">Additional labor indicators</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {LABOR_SERIES.filter(([sid]) => !["PAYEMS", "UNRATE", "ICSA", "JTSJOL"].includes(sid) && (fredData[sid]?.length ?? 0) > 0).map(([sid, label]) => {
            const records = fredData[sid];
            return (
              <div key={sid}>
                <div className="text-xs font-semibold mb-1">{label}</div>
                <Plot
                  data={[{
                    x: records.map((r) => String(r.date ?? "")),
                    y: records.map((r) => Number(r.value)),
                    type: "scatter" as const,
                    mode: "lines" as const,
                    line: { color: t.accent, width: 2 },
                  }]}
                  layout={{ height: 200, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 6: YIELD CURVE
// ═══════════════════════════════════════════════════════════════

function YieldCurveTab({ fredData, t, L }: { fredData: Record<string, Array<Record<string, unknown>>>; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const curveLabels: string[] = [];
  const currentYields: number[] = [];
  const hist1M: (number | null)[] = [];
  const hist3M: (number | null)[] = [];
  const hist1Y: (number | null)[] = [];
  for (const [sid, label] of YIELD_CURVE) {
    const records = fredData[sid];
    if (!records || records.length === 0) continue;
    curveLabels.push(label);
    const n = records.length;
    currentYields.push(Number(records[n - 1].value));
    hist1M.push(n > 22 ? Number(records[n - 23].value) : null);
    hist3M.push(n > 66 ? Number(records[n - 67].value) : null);
    hist1Y.push(n > 252 ? Number(records[n - 253].value) : null);
  }

  const y2 = fredData.DGS2?.[fredData.DGS2.length - 1]?.value;
  const y10 = fredData.DGS10?.[fredData.DGS10.length - 1]?.value;
  const y3m = fredData.DGS3MO?.[fredData.DGS3MO.length - 1]?.value;
  const spread2s10s = Number(y10 ?? 0) - Number(y2 ?? 0);
  const spread3m10y = Number(y10 ?? 0) - Number(y3m ?? 0);

  const t10y2y = fredData.T10Y2Y ?? [];
  const nfci = fredData.NFCI ?? [];
  const sahm = fredData.SAHMCURRENT ?? [];
  const sahmVal = sahm.length > 0 ? Number(sahm[sahm.length - 1].value) : NaN;

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-1">US Treasury yield curve</div>
        <Plot
          data={[
            ...(hist1Y.some((v) => v !== null) ? [{ x: curveLabels, y: hist1Y, type: "scatter" as const, mode: "lines+markers" as const, name: "1Y Ago", line: { color: "#ad7fff", width: 1.5, dash: "dot" as const }, marker: { size: 5 } }] : []),
            ...(hist3M.some((v) => v !== null) ? [{ x: curveLabels, y: hist3M, type: "scatter" as const, mode: "lines+markers" as const, name: "3M Ago", line: { color: t.spot, width: 1.5, dash: "dot" as const }, marker: { size: 5 } }] : []),
            ...(hist1M.some((v) => v !== null) ? [{ x: curveLabels, y: hist1M, type: "scatter" as const, mode: "lines+markers" as const, name: "1M Ago", line: { color: t.muted, width: 1.5, dash: "dot" as const }, marker: { size: 5 } }] : []),
            { x: curveLabels, y: currentYields, type: "scatter" as const, mode: "lines+markers" as const, name: "Current", line: { color: t.accent, width: 3 }, marker: { size: 8 } },
          ]}
          layout={{ height: 380, ...L, xaxis: { title: "Maturity", gridcolor: t.grid }, yaxis: { title: "Yield (%)", gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 } }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card card-compact flex flex-wrap gap-6">
        <Metric label="2Y" value={y2 !== undefined ? `${Number(y2).toFixed(2)}%` : "—"} />
        <Metric label="10Y" value={y10 !== undefined ? `${Number(y10).toFixed(2)}%` : "—"} />
        <Metric label="2s10s" value={`${spread2s10s.toFixed(2)}%`} deltaType={spread2s10s < 0 ? "loss" : "gain"} />
        <Metric label="3M-10Y" value={`${spread3m10y.toFixed(2)}%`} deltaType={spread3m10y < 0 ? "loss" : "gain"} />
      </div>

      {spread2s10s < 0 && (
        <div className="card card-compact border-spot text-spot text-xs">
          <strong>Yield curve inverted</strong> (2s10s at {spread2s10s.toFixed(2)}%). Historically precedes recessions by 12-18 months.
        </div>
      )}

      {t10y2y.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">2s10s spread history</div>
          <Plot
            data={[{
              x: t10y2y.map((r) => String(r.date ?? "")),
              y: t10y2y.map((r) => Number(r.value)),
              type: "scatter" as const,
              mode: "lines" as const,
              line: { color: t.accent, width: 2 },
            }]}
            layout={{ height: 260, ...L, yaxis: { title: "Spread (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: t.muted, width: 1 } }] }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {nfci.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Financial Conditions Index (Chicago Fed NFCI)</div>
          <div className="text-xs text-text-muted mb-2">Positive = tight (restrictive). Negative = loose (accommodative).</div>
          <Plot
            data={[{
              x: nfci.map((r) => String(r.date ?? "")),
              y: nfci.map((r) => Number(r.value)),
              type: "scatter" as const,
              mode: "lines" as const,
              fill: "tozeroy" as const,
              fillcolor: t.accent + "20",
              line: { color: t.accent, width: 2 },
            }]}
            layout={{ height: 280, ...L, yaxis: { title: "NFCI", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: t.muted, width: 1 } }] }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {sahm.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Sahm Rule recession indicator</div>
          <Plot
            data={[{
              x: sahm.map((r) => String(r.date ?? "")),
              y: sahm.map((r) => Number(r.value)),
              type: "scatter" as const,
              mode: "lines" as const,
              line: { color: t.loss, width: 2 },
            }]}
            layout={{ height: 260, ...L, yaxis: { title: "Sahm Rule", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0.5, y1: 0.5, line: { color: t.loss, dash: "dash", width: 1 } }] }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
          {Number.isFinite(sahmVal) && (sahmVal >= 0.5 ? (
            <div className="mt-2 text-xs text-loss"><strong>Sahm Rule triggered</strong> ({sahmVal.toFixed(2)} ≥ 0.5). Historically 100% accurate recession indicator.</div>
          ) : (
            <div className="mt-2 text-xs text-text-muted">Sahm Rule: {sahmVal.toFixed(2)} (below 0.5 threshold — no recession signal).</div>
          ))}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 7: SENTIMENT
// ═══════════════════════════════════════════════════════════════

function SentimentTab({ t }: { t: ReturnType<typeof getChartTheme> }) {
  const q = useQuery({ queryKey: ["fm-sentiment"], queryFn: fetchFedMacroSentiment, staleTime: 10 * 60_000 });

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div className="card">
        <div className="text-sm font-semibold mb-1">Prediction markets (Polymarket)</div>
        <div className="text-xs text-text-muted mb-2">Live real-money betting odds.</div>
        {q.isPending && <div className="text-xs text-text-muted">Loading…</div>}
        {q.data?.polymarket && q.data.polymarket.length > 0 ? (
          <div className="space-y-1 text-xs">
            {q.data.polymarket.map((item: PolymarketItem, idx: number) => {
              const color = item.yes_prob > 60 ? t.loss : item.yes_prob > 40 ? t.spot : t.gain;
              return (
                <div key={idx} className="flex justify-between items-center px-2 py-1 border-b border-border">
                  <span className="flex-1 pr-2">{item.question}</span>
                  <span className="font-data font-semibold" style={{ color, minWidth: "50px", textAlign: "right" }}>{item.yes_prob}%</span>
                </div>
              );
            })}
          </div>
        ) : <div className="text-xs text-text-muted">Polymarket data unavailable.</div>}
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-1">Retail sentiment (StockTwits)</div>
        <div className="text-xs text-text-muted mb-2">Bull/bear ratio from last 30 posts per symbol.</div>
        {q.isPending && <div className="text-xs text-text-muted">Loading…</div>}
        {q.data?.stocktwits && q.data.stocktwits.length > 0 ? (
          <div className="space-y-2">
            {q.data.stocktwits.map((item: StockTwitsItem, idx: number) => {
              const color = item.signal.toLowerCase().includes("bull") ? t.gain : item.signal.toLowerCase().includes("bear") ? t.loss : t.muted;
              const barWidth = Math.min(item.bull_ratio, 100);
              return (
                <div key={idx} className="border-b border-border pb-2">
                  <div className="flex justify-between text-xs">
                    <span className="font-semibold">{item.symbol}</span>
                    <span className="font-data" style={{ color }}>{item.bull_ratio.toFixed(0)}% Bull</span>
                  </div>
                  <div className="h-1 rounded-full bg-surface-alt mt-1">
                    <div className="h-full rounded-full" style={{ width: `${barWidth}%`, background: color }} />
                  </div>
                </div>
              );
            })}
          </div>
        ) : <div className="text-xs text-text-muted">StockTwits data unavailable.</div>}
      </div>
    </div>
  );
}
