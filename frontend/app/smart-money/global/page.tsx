import { PendingSmartMoneyPage } from "../_shared/pending-page";

export default function GlobalSmartMoneyPage() {
  return (
    <PendingSmartMoneyPage
      pageTitle="Global Smart Money"
      subtitle="Sovereign wealth funds, pension systems, and endowments — long-horizon capital you won't see on a hedge-fund tracker."
      overview="Tracks 13F filings from non-hedge-fund institutional capital: Norway's GPFG, Singapore GIC, Temasek, CalPERS, CalSTRS, NY Common Retirement, Harvard Management, Yale Investments, Stanford Management, Princeton Company. These are 10+ year holders whose additions and removals are strategic signals, not quarterly noise."
      features={[
        {
          title: "Sovereign Wealth Fund Holdings",
          description:
            "Norway GPFG (~$1.6T AUM, discloses full equity book), GIC, ADIA, Temasek. Flows reveal official-sector conviction.",
        },
        {
          title: "Endowment 13Fs",
          description:
            "Harvard, Yale, Stanford, Princeton, MIT. The Swensen-era endowment model pioneered alt-heavy portfolios — their equity picks still matter.",
        },
        {
          title: "US Public Pensions",
          description:
            "CalPERS ($500B), CalSTRS, NY Common, Texas Teachers, Florida SBA. Slower-moving but massive. Their rebalance flows define sector rotations.",
        },
        {
          title: "Cross-fund consensus",
          description:
            "Which tickers show up in 3+ global long-horizon portfolios simultaneously? These are the structural blue-chip conviction names.",
        },
        {
          title: "Quarter-over-quarter position deltas",
          description:
            "% change in share count per fund per ticker. Small % changes in huge funds = meaningful dollar flows.",
        },
        {
          title: "Currency-hedged flow inference",
          description:
            "For non-US funds (GPFG, GIC), overlay reported FX hedge ratios to estimate actual USD exposure change.",
        },
      ]}
      sources={[
        {
          name: "SEC EDGAR 13F",
          status: "free",
          note: "All these funds file 13F-HR quarterly. Just need to add their CIKs to TRACKED_FUNDS in src/edgar.py.",
        },
        {
          name: "Norway GPFG disclosures",
          status: "free",
          note: "Published annually on nbim.no with full holdings including non-US equities.",
        },
        {
          name: "CalPERS/CalSTRS portals",
          status: "free",
          note: "State systems publish holdings quarterly, sometimes more frequently than 13F.",
        },
      ]}
      backlinks={[
        { label: "13F Holdings", href: "/smart-money/13f" },
        { label: "Overview", href: "/smart-money" },
      ]}
    />
  );
}
