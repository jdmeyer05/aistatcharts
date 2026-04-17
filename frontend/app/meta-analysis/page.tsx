"use client";
import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistoryBatch } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Equity Curves", "Performance Ranking", "Drawdown"];
const METHODS = ["Equal Weight", "Inv Vol", "Momentum (12-1)"];
const TICKERS = ["SPY","QQQ","IWM","TLT","GLD","EFA","EEM","XLE","HYG","USO"];

function invVolWeights(retArrays: number[][]): number[] {
  const vols = retArrays.map(r => { const m = r.reduce((s,v)=>s+v,0)/r.length; return Math.sqrt(r.reduce((s,v)=>s+(v-m)**2,0)/r.length)*Math.sqrt(252); });
  const inv = vols.map(v => v > 0 ? 1/v : 0);
  const total = inv.reduce((s,v)=>s+v,0);
  return total > 0 ? inv.map(v=>v/total) : Array(retArrays.length).fill(1/retArrays.length);
}

export default function MetaAnalysisPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [data, setData] = useState<Record<string, {Date:string;Close:number}[]>>({});

  const load = useMutation({
    mutationFn: () => fetchPriceHistoryBatch(TICKERS, 504),
    onSuccess: d => setData(d),
  });

  const results = useMemo(() => {
    const valid = TICKERS.filter(tk => (data[tk]?.length ?? 0) > 60);
    if (valid.length < 3) return null;
    const minLen = Math.min(...valid.map(tk => data[tk].length));
    const dates = data[valid[0]].slice(-minLen).map(d => d.Date);
    const retArrays = valid.map(tk => {
      const c = data[tk].slice(-minLen).map(d => d.Close);
      return c.slice(1).map((v,i) => c[i] > 0 ? (v - c[i])/c[i] : 0);
    });
    const n = valid.length;

    // Build equity curves for each method
    const eqW = Array(n).fill(1/n);
    const ivW = invVolWeights(retArrays);
    // Momentum: weight by 12-month return
    const mom = retArrays.map(r => { const cum = r.reduce((s,v)=>s*(1+v),1); return cum - 1; });
    const momPos = mom.map(m => Math.max(0, m));
    const momTotal = momPos.reduce((s,v)=>s+v,0);
    const momW = momTotal > 0 ? momPos.map(v=>v/momTotal) : eqW;

    const buildEquity = (weights: number[]) => {
      const eq = [1];
      for (let day = 0; day < retArrays[0].length; day++) {
        const dayRet = weights.reduce((s, w, i) => s + w * retArrays[i][day], 0);
        eq.push(eq[eq.length-1] * (1 + dayRet));
      }
      return eq;
    };

    const equities = [buildEquity(eqW), buildEquity(ivW), buildEquity(momW)];
    // Spy benchmark
    const spyIdx = valid.indexOf("SPY");
    const spyEq = spyIdx >= 0 ? retArrays[spyIdx].reduce((eq, r) => { eq.push(eq[eq.length-1]*(1+r)); return eq; }, [1]) : [];

    const annRets = equities.map(eq => (Math.pow(eq[eq.length-1]/eq[0], 252/(eq.length-1)) - 1) * 100);
    const maxDDs = equities.map(eq => {
      let peak = eq[0], maxDD = 0;
      eq.forEach(v => { peak = Math.max(peak, v); maxDD = Math.min(maxDD, (v/peak-1)*100); });
      return maxDD;
    });
    const sharpes = equities.map((eq, mi) => {
      const rets = eq.slice(1).map((v,i) => v/eq[i]-1);
      const vol = Math.sqrt(rets.reduce((s,r)=>s+r*r,0)/rets.length)*Math.sqrt(252)*100;
      return vol > 0 ? (annRets[mi] - 4.5)/vol : 0;
    });

    return { dates: dates.slice(1), equities, spyEq, annRets, maxDDs, sharpes, valid };
  }, [data]);

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-bold tracking-tight">Meta Analysis</h1>
        <p className="text-text-secondary text-sm mt-1">Cross-method portfolio comparison: equal weight, inverse vol, momentum — vs SPY benchmark.</p></div>
      <div className="card card-compact">
        <button onClick={() => load.mutate()} disabled={load.isPending}
          className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
          {load.isPending ? "Loading..." : "Run Comparison"}</button></div>
      {load.isPending && <div className="card text-center py-12"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>}
      {results && (<>
        <div className="card card-compact"><div className="flex flex-wrap gap-6">
          {METHODS.map((m, i) => (<div key={m} className="border border-border rounded-lg p-2 min-w-[140px]">
            <div className="metric-label">{m}</div>
            <div className="flex gap-3 mt-1">
              <Metric label="Ann Ret" value={`${results.annRets[i].toFixed(1)}%`} />
              <Metric label="Sharpe" value={results.sharpes[i].toFixed(2)} />
              <Metric label="Max DD" value={`${results.maxDDs[i].toFixed(1)}%`} />
            </div></div>))}
        </div></div>
        <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
          {TABS.map((tab, i) => (<button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>{tab}</button>))}
        </div>
        {activeTab === 0 && <div className="card"><Plot data={[
          ...METHODS.map((m, i) => ({x: results.dates, y: results.equities[i].slice(1).map(v=>(v-1)*100), type:"scatter" as const, mode:"lines" as const,
            name: m, line:{width:2,color:[t.accent,t.hv20,t.gain][i]}})),
          ...(results.spyEq.length > 0 ? [{x: results.dates, y: results.spyEq.slice(1).map(v=>(v-1)*100), type:"scatter" as const, mode:"lines" as const,
            name: "SPY (Benchmark)", line:{width:1.5,color:t.muted,dash:"dash" as const}}] : []),
        ]} layout={{height:450,...L,yaxis:{title:"Cumulative Return (%)",gridcolor:t.grid},xaxis:{gridcolor:t.grid},hovermode:"x unified",
          legend:{x:0.01,y:0.99,bgcolor:"transparent"}}}
          config={{displayModeBar:false,responsive:true}} style={{width:"100%"}} /></div>}
        {activeTab === 1 && <div className="card"><div className="overflow-x-auto"><table className="data-table text-xs">
          <thead><tr><th>Rank</th><th>Method</th><th>Ann Return</th><th>Sharpe</th><th>Max DD</th></tr></thead>
          <tbody>{[...METHODS.map((m,i)=>({name:m,ret:results.annRets[i],sharpe:results.sharpes[i],dd:results.maxDDs[i]}))].sort((a,b)=>b.sharpe-a.sharpe).map((r,i) => (
            <tr key={r.name}><td className="font-data">{i+1}</td><td className="font-semibold">{r.name}</td>
              <td className={`font-data ${r.ret>0?"text-gain":"text-loss"}`}>{r.ret.toFixed(1)}%</td>
              <td className="font-data">{r.sharpe.toFixed(2)}</td>
              <td className="font-data text-loss">{r.dd.toFixed(1)}%</td></tr>))}</tbody></table></div></div>}
        {activeTab === 2 && <div className="card"><Plot data={METHODS.map((m,i) => {
          const eq = results.equities[i]; let peak = eq[0];
          const dd = eq.map(v => { peak = Math.max(peak,v); return (v/peak-1)*100; });
          return {x: results.dates, y: dd.slice(1), type:"scatter" as const, mode:"lines" as const, name:m, line:{width:1.5,color:[t.accent,t.hv20,t.gain][i]},
            fill:"tozeroy" as const, fillcolor: [t.accent,t.hv20,t.gain][i] + "10"};
        })} layout={{height:350,...L,yaxis:{title:"Drawdown (%)",gridcolor:t.grid},xaxis:{gridcolor:t.grid},hovermode:"x unified"}}
          config={{displayModeBar:false,responsive:true}} style={{width:"100%"}} /></div>}
      </>)}
    </div>
  );
}
