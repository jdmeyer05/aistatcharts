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
    label: "Summary",
    pages: [
      { label: "Dashboard", href: "/", description: "Market pulse, signals, positions, heatmap, risk", status: "live" },
      { label: "Market Scan", href: "/daily-briefing", description: "Market intelligence, opportunities, and fact-checked news", status: "live" },
      { label: "Position Monitor", href: "/position-monitor", description: "Live Robinhood positions, P&L, strike distance", status: "live" },
      { label: "Trade Ideas", href: "/trade-ideas", description: "24-strategy consensus scanner — family-weighted conviction ranking", status: "live" },
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
      { label: "Options Analysis", href: "/options-analysis", description: "IV skew, open interest walls, Greek exposures", status: "live" },
      { label: "Options Flow", href: "/options-flow", description: "Unusual activity, P/C analysis, GEX, block detection", status: "live" },
      { label: "Options Lab", href: "/options-lab", description: "Strategy modeler, BS-MJD pricing, Greeks lab", status: "live" },
      { label: "Calendar Spreads", href: "/calendar-spread", description: "8-tab calendar spread suite with scanner and backtest", status: "live" },
      { label: "Vol Surface", href: "/vol-surface", description: "9-tab IV surface with AI trade ideas", status: "live" },
      { label: "Portfolio Greeks", href: "/portfolio-greeks", description: "Position-level Greeks with delta hedging", status: "live" },
      { label: "Iron Condor", href: "/iron-condor", description: "57-ticker scanner with 7-factor scoring", status: "live" },
      { label: "Vertical Spreads", href: "/vertical-spreads", description: "Bull/bear credit & debit spread scanner", status: "live" },
      { label: "Higher Greeks", href: "/higher-greeks", description: "2nd/3rd order Greeks, Vanna-Volga pricing", status: "live" },
    ],
  },
  {
    label: "Quant Tools",
    pages: [
      { label: "Algo Backtester", href: "/algo-backtester", description: "13 strategies, de Prado methods (DSR, PBO, triple barrier)", status: "live" },
      { label: "Strategy Scanner", href: "/strategy-scanner", description: "Multi-ticker × multi-strategy scan ranked by Deflated Sharpe", status: "live" },
      { label: "Signal Scanner", href: "/signal-scanner", description: "8-tab institutional scanner: momentum, mean reversion, value", status: "live" },
      { label: "Correlation", href: "/correlation", description: "Cross-asset matrix, regime analysis, PCA, clustering", status: "live" },
      { label: "Quant Lab", href: "/quant-lab", description: "AFML: frac diff, CUSUM, SADF, meta-labeling, VPIN, entropy", status: "live" },
      { label: "Factor Decomposition", href: "/factors", description: "Fama-French 5-factor + momentum decomposition", status: "live" },
      { label: "Portfolio Optimizer", href: "/portfolio-optimizer", description: "9 allocation methods + Black-Litterman", status: "live" },
      { label: "Meta Analysis", href: "/meta-analysis", description: "9-tab cross-method portfolio comparison engine", status: "live" },
      { label: "Vol Landscape", href: "/vol-landscape", description: "Cross-asset vol surface analysis across 20 ETFs", status: "live" },
      { label: "Track Record", href: "/track-record", description: "5-tab institutional accountability dashboard", status: "live" },
    ],
  },
  {
    label: "Sectors",
    pages: [
      { label: "Sector Analysis", href: "/sector-analysis", description: "All 11 SPDR sectors: revenue, CapEx, valuation, alpha", status: "live" },
    ],
  },
  {
    label: "Energy",
    pages: [
      { label: "Oil Fundamentals", href: "/oil", description: "EIA crude data, supply/demand, term structure", status: "live" },
      { label: "Natural Gas", href: "/natgas", description: "EIA storage, supply, Henry Hub pricing", status: "live" },
      { label: "ERCOT Power", href: "/ercot-power", description: "Real-time Texas grid monitoring", status: "live" },
      { label: "ERCOT Capacity", href: "/ercot-capacity", description: "Generation pipeline and capacity outlook", status: "live" },
      { label: "Futures", href: "/futures", description: "Multi-asset futures dashboard", status: "live" },
    ],
  },
  {
    label: "Macro",
    pages: [
      { label: "Economic Calendar", href: "/economic-calendar", description: "FRED releases, yield curve, earnings, auctions", status: "live" },
      { label: "Fed & Macro", href: "/fed-macro", description: "8-tab: signal matrix, FOMC, inflation, labor, yield curve", status: "live" },
      { label: "Smart Money", href: "/smart-money", description: "13F holdings, congressional trades, activist investors", status: "live" },
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
