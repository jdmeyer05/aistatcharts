/**
 * Navigation structure — mirrors Streamlit's 7-group dropdown nav.
 * Single source of truth for header, mobile nav, and breadcrumbs.
 */

export interface NavPage {
  label: string;
  href: string;
  description: string;
  status: "live" | "stub";
}

export interface NavGroup {
  label: string;
  pages: NavPage[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Home",
    pages: [
      { label: "Home", href: "/", description: "Dashboard, market scan, position monitor, trade ideas — unified", status: "live" },
    ],
  },
  {
    label: "AI Analysis",
    pages: [
      { label: "Scenario Analysis", href: "/scenario-analysis", description: "6-tab macro engine with Grok AI regime analysis", status: "live" },
      { label: "Stock Analysis", href: "/stock-analysis", description: "3-model AI consensus scorecard + SEC EDGAR", status: "live" },
      { label: "Trump Decoder", href: "/trump-decoder", description: "Psychological profiling + behavioral analysis — decode statements, predict responses", status: "live" },
    ],
  },
  {
    label: "Options",
    pages: [
      { label: "Options Intelligence", href: "/options-analysis", description: "Volatility, positioning, flow, Greeks, chain — unified", status: "live" },
      { label: "Spread Analysis", href: "/spread-analysis", description: "Calendar, iron condor, vertical spreads — unified scanner + builder", status: "live" },
      { label: "Options Portfolio Analysis", href: "/options-portfolio-analysis", description: "Strategy lab, higher Greeks, portfolio Greeks, vol surface — unified", status: "live" },
    ],
  },
  {
    label: "Quant Tools",
    pages: [
      { label: "Algo Backtester", href: "/algo-backtester", description: "13 strategies, de Prado methods (DSR, PBO, triple barrier)", status: "live" },
      { label: "Strategy Scanner", href: "/strategy-scanner", description: "Multi-ticker × multi-strategy scan ranked by Deflated Sharpe", status: "live" },
      { label: "Signal Scanner", href: "/signal-scanner", description: "8-tab institutional scanner: momentum, mean reversion, value", status: "live" },
      { label: "Correlation", href: "/correlation", description: "Cross-asset matrix, regime analysis, PCA, clustering", status: "live" },
      { label: "Causality", href: "/causality", description: "Macro causal research: lead/lag, Granger, transfer entropy, VAR/IRF — across 50+ macro series", status: "live" },
      { label: "Quant Lab", href: "/quant-lab", description: "AFML: frac diff, CUSUM, SADF, meta-labeling, VPIN, entropy", status: "live" },
      { label: "Factor Decomposition", href: "/factors", description: "Fama-French 5-factor + momentum decomposition", status: "live" },
      { label: "Portfolio Optimizer", href: "/portfolio-optimizer", description: "9 allocation methods + Black-Litterman", status: "live" },
      { label: "Meta Analysis", href: "/meta-analysis", description: "9-tab cross-method portfolio comparison engine", status: "live" },
      { label: "Vol Landscape", href: "/vol-landscape", description: "Cross-asset vol surface analysis across 20 ETFs", status: "live" },
      { label: "Track Record", href: "/track-record", description: "5-tab institutional accountability dashboard", status: "live" },
    ],
  },
  {
    label: "Energy",
    pages: [
      { label: "Oil Fundamentals", href: "/oil", description: "EIA crude data, supply/demand, term structure", status: "live" },
      { label: "Natural Gas", href: "/natgas", description: "EIA storage, supply, Henry Hub pricing", status: "live" },
      { label: "ERCOT Power", href: "/ercot-power", description: "Real-time Texas grid monitoring", status: "live" },
      { label: "ERCOT Capacity", href: "/ercot-capacity", description: "Generation pipeline and capacity outlook", status: "live" },
    ],
  },
  {
    label: "Macro",
    pages: [
      { label: "Economic Calendar", href: "/economic-calendar", description: "FRED releases, earnings, auctions", status: "live" },
      { label: "Fed & Macro", href: "/fed-macro", description: "8-tab: signal matrix, FOMC, inflation, labor, yield curve", status: "live" },
      { label: "Sector Analysis", href: "/sector-analysis", description: "All 11 SPDR sectors: revenue, CapEx, valuation, alpha", status: "live" },
      { label: "Positioning (CFTC)", href: "/positioning", description: "45-contract COT: managed money / leveraged funds, divergence Z, regime composites, CTA unwind risk", status: "live" },
      { label: "Polymarket", href: "/polymarket", description: "Crowd-priced odds: Fed decisions, recession, geopolitics, elections — sorted by actionability", status: "live" },
      { label: "WallStreetBets", href: "/wsb", description: "Reddit ticker-mention surface: bull/bear sentiment, options lean, DD post count across r/wsb + r/options + r/stocks", status: "live" },
    ],
  },
  {
    label: "Smart Money",
    pages: [
      { label: "Overview", href: "/smart-money", description: "Smart money conviction score — aggregate signals across sources", status: "live" },
      { label: "Insider Activity", href: "/smart-money/insiders", description: "Form 4 officer/director trades + cluster buy detection", status: "live" },
      { label: "Institutional Holdings", href: "/smart-money/13f", description: "13F filings + follow-the-leader fund clones", status: "live" },
      { label: "Global Smart Money", href: "/smart-money/global", description: "Sovereign wealth funds + endowments + pensions", status: "live" },
      { label: "Congressional & Political", href: "/smart-money/political", description: "House/Senate trades + politician leaderboard", status: "live" },
      { label: "Activist Campaigns", href: "/smart-money/activist", description: "13D filings + historical campaign win rates", status: "live" },
      { label: "Short Side", href: "/smart-money/shorts", description: "Short interest, squeeze setups, short seller reports", status: "live" },
      { label: "Buybacks & Returns", href: "/smart-money/buybacks", description: "Repurchase authorizations + 10b5-1 plans + capital returns", status: "live" },
      { label: "Exit Signals", href: "/smart-money/exits", description: "Inverse tracker — who's leaving what smart money is selling", status: "live" },
      { label: "Material Events", href: "/smart-money/events", description: "8-K filings + forward guidance from press releases", status: "live" },
      { label: "Alerts", href: "/smart-money/alerts", description: "Notifications on tracked filings and positions", status: "live" },
    ],
  },
];

/** Flat list of all pages for routing */
export const ALL_PAGES = NAV_GROUPS.flatMap(g => g.pages);

/** Find which group a path belongs to */
export function findGroup(pathname: string): NavGroup | undefined {
  return NAV_GROUPS.find(g => g.pages.some(p => p.href === pathname));
}

/** Find page by path */
export function findPage(pathname: string): NavPage | undefined {
  return ALL_PAGES.find(p => p.href === pathname);
}
