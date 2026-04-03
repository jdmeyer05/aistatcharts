"use client";
import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistory } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Fractional Diff", "CUSUM Filter", "Return Distribution", "Autocorrelation"];

function fracDiff(prices: number[], d: number, thresh = 0.01): number[] {
  const n = prices.length;
  const logP = prices.map(p => Math.log(p));
  const weights: number[] = [1];
  for (let k = 1; k < n; k++) { weights.push(-weights[k-1]*(d-k+1)/k); if (Math.abs(weights[k]) < thresh) break; }
  const result: number[] = [];
  for (let t = weights.length - 1; t < n; t++) {
    let val = 0;
    for (let k = 0; k < weights.length; k++) val += weights[k] * logP[t - k];
    result.push(val);
  }
  return result;
}

function cusumFilter(rets: number[], h: number): number[] {
  const events: number[] = [];
  let sPos = 0, sNeg = 0;
  for (let i = 0; i < rets.length; i++) {
    sPos = Math.max(0, sPos + rets[i]); sNeg = Math.min(0, sNeg + rets[i]);
    if (sPos > h) { events.push(i); sPos = 0; }
    if (sNeg < -h) { events.push(i); sNeg = 0; }
  }
  return events;
}

function autocorr(rets: number[], maxLag: number): number[] {
  const n = rets.length;
  const mean = rets.reduce((s,r)=>s+r,0)/n;
  const var0 = rets.reduce((s,r)=>s+(r-mean)**2,0)/n;
  if (var0 === 0) return Array(maxLag).fill(0);
  return Array.from({length: maxLag}, (_, lag) => {
    let s = 0;
    for (let i = lag + 1; i < n; i++) s += (rets[i]-mean)*(rets[i-lag-1]-mean);
    return s / (n * var0);
  });
}

export default function QuantLab() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [ticker, setTicker] = useState("SPY");
  const [activeTab, setActiveTab] = useState(0);
  const [closes, setCloses] = useState<number[]>([]);
  const [dates, setDates] = useState<string[]>([]);
  const [fracD, setFracD] = useState(0.4);
  const [cusumH, setCusumH] = useState(0.02);

  const load = useMutation({
    mutationFn: (tk: string) => fetchPriceHistory(tk, 1260),
    onSuccess: (d) => { setCloses(d.data.map(b => b.Close)); setDates(d.data.map(b => b.Date)); },
  });

  const rets = useMemo(() => closes.slice(1).map((c,i) => closes[i] > 0 ? (c - closes[i])/closes[i] : 0), [closes]);
  const fracSeries = useMemo(() => closes.length > 50 ? fracDiff(closes, fracD) : [], [closes, fracD]);
  const cusumEvents = useMemo(() => rets.length > 0 ? cusumFilter(rets, cusumH) : [], [rets, cusumH]);
  const acf = useMemo(() => rets.length > 50 ? autocorr(rets, 30) : [], [rets]);

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-bold tracking-tight">Quant Lab</h1>
        <p className="text-text-secondary text-sm mt-1">Fractional differentiation, CUSUM filter, return analysis, autocorrelation.</p></div>
      <div className="card card-compact"><div className="flex items-center gap-3">
        <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} onKeyDown={e => e.key === "Enter" && load.mutate(ticker)}
          className="w-24 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
        <button onClick={() => load.mutate(ticker)} disabled={load.isPending}
          className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
          {load.isPending ? "Loading..." : "Load Data"}</button></div></div>
      {closes.length > 50 && (<>
        <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
          {TABS.map((tab, i) => (<button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>{tab}</button>))}
        </div>
        {activeTab === 0 && <div className="card space-y-3">
          <div className="flex items-center gap-3"><label className="metric-label">d =</label>
            <input type="number" value={fracD} onChange={e => setFracD(Number(e.target.value))} step={0.05} min={0} max={1}
              className="w-20 px-2 py-1 border border-border rounded text-xs font-data bg-surface" />
            <span className="text-xs text-text-muted">0 = original, 1 = full diff, 0.3-0.5 = preserve memory</span></div>
          <Plot data={[{x: dates.slice(dates.length - fracSeries.length), y: fracSeries, type:"scatter" as const, mode:"lines" as const,
            line:{color:t.accent,width:1.5}, name:`d=${fracD}`}]}
            layout={{height:350,...L,yaxis:{title:`Frac Diff (d=${fracD})`,gridcolor:t.grid},xaxis:{gridcolor:t.grid},hovermode:"x unified"}}
            config={{displayModeBar:false,responsive:true}} style={{width:"100%"}} /></div>}
        {activeTab === 1 && <div className="card space-y-3">
          <div className="flex items-center gap-3"><label className="metric-label">Threshold h =</label>
            <input type="number" value={cusumH} onChange={e => setCusumH(Number(e.target.value))} step={0.005} min={0.001}
              className="w-24 px-2 py-1 border border-border rounded text-xs font-data bg-surface" />
            <Metric label="Events" value={String(cusumEvents.length)} /></div>
          <Plot data={[{x: dates.slice(1), y: rets.map(r=>r*100), type:"scatter" as const, mode:"lines" as const, line:{color:t.muted,width:0.8}, name:"Returns"},
            {x: cusumEvents.map(i=>dates[i+1]), y: cusumEvents.map(i=>rets[i]*100), type:"scatter" as const, mode:"markers" as const,
              marker:{color:t.loss,size:6,symbol:"diamond"}, name:"CUSUM Events"}]}
            layout={{height:350,...L,yaxis:{title:"Daily Return (%)",gridcolor:t.grid},xaxis:{gridcolor:t.grid},hovermode:"x unified"}}
            config={{displayModeBar:false,responsive:true}} style={{width:"100%"}} /></div>}
        {activeTab === 2 && <div className="card">
          <Plot data={[{x: rets.map(r=>r*100), type:"histogram" as const, nbinsx:100,
            marker:{color:t.accent+"60",line:{color:t.accent,width:1}}}]}
            layout={{height:350,...L,xaxis:{title:"Daily Return (%)",gridcolor:t.grid},yaxis:{title:"Frequency",gridcolor:t.grid}}}
            config={{displayModeBar:false,responsive:true}} style={{width:"100%"}} /></div>}
        {activeTab === 3 && acf.length > 0 && <div className="card">
          <Plot data={[{x: Array.from({length:acf.length},(_,i)=>i+1), y: acf, type:"bar" as const,
            marker:{color:acf.map(v=>Math.abs(v)>2/Math.sqrt(rets.length)?t.loss:t.accent)}}]}
            layout={{height:350,...L,xaxis:{title:"Lag",gridcolor:t.grid},yaxis:{title:"ACF",gridcolor:t.grid},
              shapes:[{type:"line",y0:2/Math.sqrt(rets.length),y1:2/Math.sqrt(rets.length),x0:0,x1:1,xref:"paper",line:{color:t.muted,width:1,dash:"dot"}},
                {type:"line",y0:-2/Math.sqrt(rets.length),y1:-2/Math.sqrt(rets.length),x0:0,x1:1,xref:"paper",line:{color:t.muted,width:1,dash:"dot"}}]}}
            config={{displayModeBar:false,responsive:true}} style={{width:"100%"}} /></div>}
      </>)}
    </div>
  );
}
