"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { CalendarSpreadContent } from "./_components/calendar";
import { IronCondorContent } from "./_components/iron-condor";
import { VerticalSpreadContent } from "./_components/vertical";

type Strategy = "calendar" | "iron-condor" | "vertical";

const STRATEGY_META: Record<Strategy, { label: string; subtitle: string }> = {
  "calendar": {
    label: "Calendar",
    subtitle: "Build, analyze, and simulate calendar spreads — front/back expiry selection with live Greeks.",
  },
  "iron-condor": {
    label: "Iron Condor",
    subtitle: "Scan short iron condors ranked by credit, probability of profit, and IV percentile.",
  },
  "vertical": {
    label: "Vertical Spread",
    subtitle: "Scan bull/bear credit and debit spreads ranked by score, POP, and IV percentile.",
  },
};

function SpreadAnalysisInner() {
  const params = useSearchParams();
  const router = useRouter();
  const qp = (params.get("strategy") || "").toLowerCase();
  const initialStrategy: Strategy =
    qp === "calendar" || qp === "iron-condor" || qp === "vertical"
      ? (qp as Strategy)
      : "calendar";
  const [strategy, setStrategy] = useState<Strategy>(initialStrategy);

  // Keep URL in sync so deep links + browser back/forward work.
  useEffect(() => {
    const current = params.get("strategy");
    if (current !== strategy) {
      const search = new URLSearchParams(params.toString());
      search.set("strategy", strategy);
      router.replace(`/spread-analysis?${search.toString()}`, { scroll: false });
    }
  }, [strategy, params, router]);

  const meta = STRATEGY_META[strategy];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Spread Analysis</h1>
        <p className="text-text-secondary text-sm mt-1">{meta.subtitle}</p>
      </div>

      <div className="flex gap-1 border-b border-border pb-1">
        {(Object.keys(STRATEGY_META) as Strategy[]).map(s => (
          <button
            key={s}
            onClick={() => setStrategy(s)}
            className={`px-4 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              strategy === s
                ? "bg-accent text-white"
                : "text-text-muted hover:text-text hover:bg-surface-alt"
            }`}
          >
            {STRATEGY_META[s].label}
          </button>
        ))}
      </div>

      {/* Remount on strategy change so internal state (ticker box, filters)
          resets cleanly between strategies instead of leaking across. */}
      <div key={strategy}>
        {strategy === "calendar" && <CalendarSpreadContent />}
        {strategy === "iron-condor" && <IronCondorContent />}
        {strategy === "vertical" && <VerticalSpreadContent />}
      </div>
    </div>
  );
}

export default function SpreadAnalysisPage() {
  return (
    <Suspense fallback={<div className="card text-center py-8 text-text-muted">Loading...</div>}>
      <SpreadAnalysisInner />
    </Suspense>
  );
}
