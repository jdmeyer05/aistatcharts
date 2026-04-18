import { PendingSmartMoneyPage } from "../_shared/pending-page";

export default function ShortSidePage() {
  return (
    <PendingSmartMoneyPage
      pageTitle="Short Side"
      subtitle="Short interest, squeeze setups, and short-seller research — the other half of smart money."
      overview="Short sellers are often the best fundamental researchers in public markets (Hindenburg, Muddy Waters, Citron). Their positioning and published reports are signals; so is the mechanical setup when crowded shorts meet tight float. This page combines the structural (short interest, days-to-cover, borrow rate) with the event-driven (published reports, short-seller 13Fs)."
      features={[
        {
          title: "Short Interest & Days-to-Cover",
          description:
            "% of float short + average daily volume → how many days to cover. High DTC with rising price is the squeeze setup.",
        },
        {
          title: "Borrow Rate Trend",
          description:
            "Cost-to-borrow changes often lead price moves. Rising borrow = shorts willing to pay more = conviction.",
        },
        {
          title: "Squeeze Score",
          description:
            "Composite rank on short % float, DTC, borrow rate, and recent price momentum. Identifies setups before they break.",
        },
        {
          title: "Published Short Reports Feed",
          description:
            "Aggregates reports from Hindenburg, Muddy Waters, Citron, Kerrisdale, Bleecker Street, Spruce Point. Historical hit rate per firm.",
        },
        {
          title: "Reverse 13F — Short Funds",
          description:
            "Funds known for short exposure (Kynikos, Melvin legacy, Third Point's short book). Their long 13F often implies short positioning by absence.",
        },
        {
          title: "Failures-to-Deliver (FTD)",
          description:
            "SEC publishes biweekly. Spikes in FTDs indicate naked short pressure and often precede regulatory action or squeezes.",
        },
        {
          title: "Put/Call Open Interest Skew",
          description:
            "Unusual put buying patterns on names with rising short interest = coordinated bearish conviction.",
        },
      ]}
      sources={[
        {
          name: "FINRA short interest",
          status: "free",
          note: "Published biweekly on finra.org. Covers all NYSE/Nasdaq/ARCA-listed equities.",
        },
        {
          name: "NYSE short interest",
          status: "free",
          note: "Biweekly ShortSaleVolume reports via nysedata.com.",
        },
        {
          name: "SEC FTD data",
          status: "free",
          note: "Biweekly failures-to-deliver on sec.gov/foia/docs/failsdata.htm.",
        },
        {
          name: "Interactive Brokers borrow rates",
          status: "paid",
          note: "Real-time CTB data. Our Robinhood integration doesn't expose this — would need IBKR API.",
        },
        {
          name: "Short report aggregators",
          status: "internal",
          note: "Scrape short-seller websites or manually maintain feed. Activist Shorts Research publishes a curated list.",
        },
      ]}
      backlinks={[
        { label: "Overview", href: "/smart-money" },
        { label: "Material Events", href: "/smart-money/events" },
      ]}
    />
  );
}
