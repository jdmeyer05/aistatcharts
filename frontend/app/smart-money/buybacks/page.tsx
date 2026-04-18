import { PendingSmartMoneyPage } from "../_shared/pending-page";

export default function BuybacksPage() {
  return (
    <PendingSmartMoneyPage
      pageTitle="Buybacks & Capital Returns"
      subtitle="Repurchase authorizations, 10b5-1 plans, and actual shares retired — companies voting with their balance sheets."
      overview="A company buying its own stock at scale is arguably the purest smart money signal: the people with the most information about the business are allocating its capital into that business. This page tracks announced authorizations, actual repurchases (disclosed quarterly in 10-Q), dividend changes, and 10b5-1 executive plans."
      features={[
        {
          title: "Active Buyback Programs",
          description:
            "Authorized $ amount, remaining capacity, pace (actual vs announced). % of market cap represented by authorization.",
        },
        {
          title: "Quarterly Execution Tracker",
          description:
            "From 10-Q Item 5 'Issuer Purchases of Equity Securities'. Actual shares retired per quarter — separates talk from action.",
        },
        {
          title: "Buyback Yield Ranking",
          description:
            "Dollars returned / market cap, trailing 12 months. Rank across S&P 500 / Russell 2000.",
        },
        {
          title: "10b5-1 Plan Activity",
          description:
            "Pre-scheduled insider sales. Plan adoptions spike before known future sales — useful as an advance signal.",
        },
        {
          title: "Dividend Change Feed",
          description:
            "Initiations, increases, cuts, suspensions. Dividend policy communicates management conviction.",
        },
        {
          title: "ASR (Accelerated Share Repurchase) Announcements",
          description:
            "Large one-time repurchase blocks via investment bank. Much stronger signal than open-market authorizations.",
        },
        {
          title: "Total Shareholder Yield",
          description:
            "Dividends + buybacks - net equity issuance, as % of market cap. Pure capital return metric.",
        },
      ]}
      sources={[
        {
          name: "SEC 10-Q Item 5",
          status: "free",
          note: "EDGAR filings — parse 'Issuer Purchases of Equity Securities' table quarterly. Already have EDGAR pipeline.",
        },
        {
          name: "SEC 8-K buyback announcements",
          status: "free",
          note: "Item 7.01 / 8.01 when companies announce new authorizations. Filter via existing 8-K pipeline.",
        },
        {
          name: "Polygon dividends endpoint",
          status: "paid",
          note: "Our Polygon tier already includes /v3/reference/dividends.",
        },
        {
          name: "Yahoo Finance / yfinance",
          status: "free",
          note: "Dividend history on Ticker.dividends and Ticker.actions. Already used elsewhere.",
        },
        {
          name: "S&P / SPGMI buyback index",
          status: "paid",
          note: "S&P 500 Buyback Index for benchmark context. Could self-compute from holdings + repurchase data.",
        },
      ]}
      backlinks={[
        { label: "Material Events", href: "/smart-money/events" },
        { label: "Overview", href: "/smart-money" },
      ]}
    />
  );
}
