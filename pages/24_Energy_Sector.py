from src.layout import setup_page
from src.edgar import ENERGY_COMPANIES, ENERGY_GUIDANCE_SNAPSHOT
from src.sector_analysis import SectorConfig, render_sector_page

setup_page("24_Energy_Sector")

ENERGY_CONFIG = SectorConfig(
    page_id="24_Energy_Sector",
    title="Energy Sector Analysis",
    subtitle="Top 10 US oil & gas companies — financials, CapEx, valuation, guidance, and market positioning.",
    etf="XLE",
    companies=ENERGY_COMPANIES,
    subsectors={
        "Integrated": ["XOM", "CVX"],
        "E&P": ["COP", "EOG", "OXY", "DVN"],
        "Refining": ["MPC", "PSX", "VLO"],
        "Services": ["SLB"],
    },
    guidance_snapshot=ENERGY_GUIDANCE_SNAPSHOT,
    macro_overlay={"fred_series": "DCOILWTICO", "label": "WTI Crude ($/bbl)"},
    factor_proxies=["SPY", "USO", "UUP", "TLT"],
    cot_commodities=[("Crude Oil", "crude_oil"), ("Natural Gas", "natural_gas")],
)

render_sector_page(ENERGY_CONFIG)
