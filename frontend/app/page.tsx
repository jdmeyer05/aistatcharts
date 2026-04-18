"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { DashboardContent } from "./_home/dashboard";
import { MarketScanContent } from "./_home/market-scan";
import { TradeIdeasContent } from "./_home/trade-ideas";

// Position Monitor is temporarily hidden — Robinhood creds aren't mounted on
// Cloud Run so the tab errors out in prod. Re-add to VALID_VIEWS / VIEW_META
// (and restore the PositionMonitorContent import) once the RH pipeline is
// deployed.
type View = "dashboard" | "market-scan" | "trade-ideas";

const VIEW_META: Record<View, { label: string; subtitle: string }> = {
  "dashboard": {
    label: "Dashboard",
    subtitle: "Market pulse, signals, positions, heatmap, risk — your morning snapshot.",
  },
  "market-scan": {
    label: "Market Scan",
    subtitle: "Market intelligence, opportunities, and fact-checked news across your watchlist.",
  },
  "trade-ideas": {
    label: "Trade Ideas",
    subtitle: "24-strategy consensus scanner with family-weighted conviction ranking.",
  },
};

const VALID_VIEWS: View[] = ["dashboard", "market-scan", "trade-ideas"];

function HomeInner() {
  const params = useSearchParams();
  const router = useRouter();
  const qp = (params.get("view") || "").toLowerCase();
  const initialView: View = VALID_VIEWS.includes(qp as View) ? (qp as View) : "dashboard";
  const [view, setView] = useState<View>(initialView);

  useEffect(() => {
    const current = params.get("view");
    // Dashboard is the default — keep the URL clean (no view query) when on it
    if (view === "dashboard" && current) {
      router.replace("/", { scroll: false });
    } else if (view !== "dashboard" && current !== view) {
      const search = new URLSearchParams(params.toString());
      search.set("view", view);
      router.replace(`/?${search.toString()}`, { scroll: false });
    }
  }, [view, params, router]);

  const meta = VIEW_META[view];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Home</h1>
        <p className="text-text-secondary text-sm mt-1">{meta.subtitle}</p>
      </div>

      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {VALID_VIEWS.map(v => (
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
        {view === "dashboard" && <DashboardContent />}
        {view === "market-scan" && <MarketScanContent />}
        {view === "trade-ideas" && <TradeIdeasContent />}
      </div>
    </div>
  );
}

export default function HomePage() {
  return (
    <Suspense fallback={<div className="card text-center py-8 text-text-muted">Loading...</div>}>
      <HomeInner />
    </Suspense>
  );
}
