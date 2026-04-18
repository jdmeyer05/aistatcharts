"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { OptionsLabContent } from "./_components/lab";
import { HigherGreeksContent } from "./_components/higher-greeks";
import { PortfolioGreeksContent } from "./_components/greeks";
import { VolSurfaceContent } from "./_components/vol-surface";

type View = "lab" | "higher-greeks" | "portfolio" | "vol-surface";

const VIEW_META: Record<View, { label: string; subtitle: string }> = {
  "lab": {
    label: "Strategy Lab",
    subtitle: "Black-Scholes pricing, Greeks calculator, and multi-leg strategy P&L modeler.",
  },
  "higher-greeks": {
    label: "Higher-Order Greeks",
    subtitle: "Second and third order sensitivities — vanna, charm, volga, speed, zomma, color.",
  },
  "portfolio": {
    label: "Portfolio Greeks",
    subtitle: "Enter live positions, see aggregate risk, model scenarios, plan delta hedges.",
  },
  "vol-surface": {
    label: "Vol Surface",
    subtitle: "Institutional vol surface analysis — skew metrics, gamma scalping, regime comparison, AI trade ideas.",
  },
};

function OptionsPortfolioInner() {
  const params = useSearchParams();
  const router = useRouter();
  const qp = (params.get("view") || "").toLowerCase();
  const validViews: View[] = ["lab", "higher-greeks", "portfolio", "vol-surface"];
  const initialView: View = validViews.includes(qp as View) ? (qp as View) : "lab";
  const [view, setView] = useState<View>(initialView);

  useEffect(() => {
    const current = params.get("view");
    if (current !== view) {
      const search = new URLSearchParams(params.toString());
      search.set("view", view);
      router.replace(`/options-portfolio-analysis?${search.toString()}`, { scroll: false });
    }
  }, [view, params, router]);

  const meta = VIEW_META[view];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Options Portfolio Analysis</h1>
        <p className="text-text-secondary text-sm mt-1">{meta.subtitle}</p>
      </div>

      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {validViews.map(v => (
          <button
            key={v}
            onClick={() => setView(v)}
            title={VIEW_META[v].subtitle}
            className={`px-4 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              view === v
                ? "bg-accent text-white"
                : "text-text-muted hover:text-text hover:bg-surface-alt"
            }`}
          >
            {VIEW_META[v].label}
          </button>
        ))}
      </div>

      <div key={view}>
        {view === "lab" && <OptionsLabContent />}
        {view === "higher-greeks" && <HigherGreeksContent />}
        {view === "portfolio" && <PortfolioGreeksContent />}
        {view === "vol-surface" && <VolSurfaceContent />}
      </div>
    </div>
  );
}

export default function OptionsPortfolioAnalysisPage() {
  return (
    <Suspense fallback={<div className="card text-center py-8 text-text-muted">Loading...</div>}>
      <OptionsPortfolioInner />
    </Suspense>
  );
}
