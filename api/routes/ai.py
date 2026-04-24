"""AI-powered page interpretation via Claude Opus 4.7.

Each page sends a compact JSON summary of what the user is looking at. Claude
reads the data in context and returns a short trader-facing interpretation:
what's notable, what's noise, what (if anything) to act on.

All calls use prompt caching on the base system prompt so repeated
interpretations across users / pages keep cost low (~75% input discount on
cache hits).
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_user
from api.rate_limit import limiter
from src.api_keys import get_secret

logger = logging.getLogger(__name__)
router = APIRouter()

MODEL = "claude-opus-4-7"

BASE_SYSTEM = """You are a senior quantitative analyst at an institutional trading desk. The user is looking at a page in our quant research platform and wants you to tell them WHAT IT MEANS — not describe what they're seeing, but interpret it.

Style rules:
- Be direct, specific, and actionable. No throat-clearing, no meta-commentary.
- Use bullet points. Keep under 220 words total.
- Distinguish signal from noise. A single outlier number is often noise; confluence of signals is usually signal.
- Name the tradeable implication where one exists (long/short bias, sizing consideration, what would invalidate the read).
- Be honest about data limitations — if the sample is small or the window is short, say so.
- Assume the user is sophisticated: don't explain basic terms.
- End with one line: "Bottom line:" followed by a single crisp takeaway.

ACCURACY RULES — non-negotiable:
- Only cite numbers that appear in the payload, either literally or as direct derivations (ratios, averages, sums) of payload values.
- If you compute a derived number, either show the inputs (e.g., "177/129 = 1.37x buy/sell") or prefix with "roughly"/"approximately".
- Prefer qualitative language ("modestly bullish", "heavy skew") over invented precise figures when precision isn't in the data.
- Never cite a ticker, fund, or person not present in the payload.
- If the data is too sparse for a specific claim, say so — don't pad with generalities.

SELF-CHECK — before finalizing:
Draft your response first. Then re-read it once and verify: every number traces to the payload, every ticker/fund/person appears in the payload, and your Bottom line is consistent with the data cited. Make small corrections if anything fails — this is a verification pass, not a rewrite. Output only the final revised version."""


# Extract numeric-looking tokens from Claude's interpretation so we can check
# each one appears in the data payload. Handles plain numbers, thousands
# commas, percentages, and $B/$M/$K suffixes.
_NUM_TOKEN = re.compile(
    r"""\$?\s*-?\d+(?:,\d{3})*(?:\.\d+)?\s*[%xBMKTbmkt]?""",
)


def _normalize_num(token: str) -> tuple[float | None, bool]:
    """Turn a token like '$1.2B', '15%', '1,500', '1.37x', '$3.2T' into a float.
    Returns (value, is_percent). is_percent tells the grounding check to also
    try the decimal form when matching against payload numbers (since data
    often stores percentages as decimals — 0.153 vs "15.3%")."""
    s = token.strip().replace("$", "").replace(",", "").replace(" ", "")
    mult = 1.0
    is_percent = False
    if s.endswith("%"):
        s = s[:-1]
        is_percent = True
    elif s.lower().endswith("t"):
        s = s[:-1]
        mult = 1e12
    elif s.lower().endswith("b"):
        s = s[:-1]
        mult = 1e9
    elif s.lower().endswith("m"):
        s = s[:-1]
        mult = 1e6
    elif s.lower().endswith("k"):
        s = s[:-1]
        mult = 1e3
    elif s.lower().endswith("x"):
        s = s[:-1]
    try:
        return float(s) * mult, is_percent
    except (ValueError, TypeError):
        return None, False


def _collect_payload_numbers(obj, out: set[float]) -> None:
    """Recursively gather every numeric value in the payload for fuzzy match."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_payload_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_payload_numbers(v, out)
    elif isinstance(obj, str):
        # Parse numeric substrings in string values too (e.g., "15.3%" in data)
        for m in _NUM_TOKEN.findall(obj):
            n, _ = _normalize_num(m)
            if n is not None:
                out.add(n)


def _check_grounding(interpretation: str, data: dict) -> dict:
    """Post-hoc hallucination check.

    Every numeric claim in the interpretation either (a) appears verbatim in
    the payload, (b) matches a payload value within 2% tolerance (for rounded
    claims like "$1.2B" when payload has 1,215,432,000), or (c) is a trivial
    derivation (ratio/percent of two payload values within tolerance).
    Unverified tokens are surfaced in the response so the UI can flag them.
    """
    payload_nums: set[float] = set()
    _collect_payload_numbers(data, payload_nums)
    # Also include the stringified version for verbatim substring matches.
    data_str = json.dumps(data, default=str)

    grounded: list[str] = []
    unverified: list[str] = []
    skip_tiny = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0}

    for raw_token in _NUM_TOKEN.findall(interpretation):
        token = raw_token.strip()
        if not token or token in ("-", "$"):
            continue
        n, is_pct = _normalize_num(token)
        if n is None:
            continue
        # Skip tiny counting numbers — "3 buys", "2 sells" are everywhere and
        # a false flag here is worse than a missed one.
        if n in skip_tiny and "." not in token and "%" not in token:
            continue

        # Verbatim substring match on data JSON (handles "NDAQ (6 buys)"
        # where 6 is in the payload's count field).
        clean = token.replace("$", "").replace(",", "").replace(" ", "").rstrip("%xBMKTbmkt")
        if clean and clean in data_str:
            grounded.append(token)
            continue

        # Fuzzy numeric match within 2% tolerance against any payload number.
        # For percentage tokens, also check the decimal form — payloads often
        # store "15.3%" as 0.153. Also try the /100 form for bare integers in
        # 1..100 (likely implicit percentile / percentage claims like
        # "95th percentile" when the payload stores 0.95).
        candidates = [n]
        if is_pct:
            candidates.append(n / 100.0)
        elif "." not in token and "%" not in token and 1 <= n <= 100 and n == int(n):
            candidates.append(n / 100.0)
        matched = False
        for c in candidates:
            tolerance = max(abs(c) * 0.02, 0.01)
            if any(abs(c - p) <= tolerance for p in payload_nums):
                matched = True
                break
        if matched:
            grounded.append(token)
            continue

        # Ratio check: is this a derivation of two payload numbers?
        # Covers cases like "1.37x" when payload has {purchases: 177, sales: 129}.
        ratio_match = False
        if 0.01 < abs(n) < 1000:
            nums_list = list(payload_nums)
            for i, a in enumerate(nums_list):
                if a == 0:
                    continue
                for j, b in enumerate(nums_list):
                    if i == j:
                        continue
                    r = b / a
                    if abs(r - n) <= abs(n) * 0.02:
                        ratio_match = True
                        break
                if ratio_match:
                    break
        if ratio_match:
            grounded.append(token)
            continue

        unverified.append(token)

    return {
        "grounded_count": len(grounded),
        "unverified_count": len(unverified),
        "unverified_tokens": unverified[:10],  # cap to avoid noisy UI
    }

# Per-page context so Claude understands the semantic meaning of each payload
# without reinventing it every call. These stay in the user turn, not the
# system prompt (the system prompt cache works better when it's stable).
PAGE_CONTEXT: dict[str, str] = {
    "overview": "Smart Money Overview — the Conviction Score dashboard. Aggregates ticker-level signals across insider Form 4 activity, activist 13D filings, and 8-K event pulse. A confluence of 2+ signal families historically produces institutional-grade edge in backtests.",
    "insiders": "Form 4 insider trades for a single ticker. Cluster buys (3+ distinct insiders within 30 days same direction) are highly predictive per the insider-trading literature (Seyhun, Cohen, Malloy, Pomorski). Sells are noisier — routine 10b5-1 plans, options exercises, and diversification dominate.",
    "13f": "Quarterly 13F-HR institutional holdings for a single fund. Fund's top positions by value. Stale data — filed 45 days after quarter end, so positions may have changed by the time you're reading it.",
    "political": "Congressional stock trades (House PTR filings) with performance vs SPY overlay. Politicians are not monolithic — some beat, most match, a few significantly underperform. Alpha is measured as stock return from trade date to today minus SPY return over the same window.",
    "activist": "Recent 13D/13D-A filings. New 13D = someone took a >5% stake with intent to influence (historically bullish 3-12 months); amendment = stake change (could be up or down, needs reading the filing text).",
    "shorts": "Short interest structural metrics. High % float + high days-to-cover + recent price momentum = squeeze setup. Alone, heavy shorting is usually correct. The setup that matters is structurally-trapped shorts facing a catalyst.",
    "buybacks": "Company's own capital return history from cashflow statements. Buyback yield = TTM repurchases / market cap. Total shareholder yield = buyback + dividend yield. Research (Ikenberry, Michaely) shows buyback-announcing firms outperform peers by ~4-12% over 4 years. Execution matters more than authorization — look for consistent actual repurchases.",
    "exits": "Inverse smart-money tracker — where institutional and political money is reducing exposure. Congressional net selling + activist amendments + cluster insider sells on the same ticker is a coordinated exit signal.",
    "global": "Sovereign wealth funds, public pensions, and endowments — 10+ year horizon. Cross-fund consensus picks (held by 3+ global funds) are structural conviction blue chips. Quarter-over-quarter deltas reveal rotation.",
    "factors": "Fama-French 5-factor regression on a single ticker. Betas quantify exposure to Market / Size / Value / Profitability / Investment factors. Alpha is the excess return unexplained by factor exposures — what's left after stripping out the systematic bets.",
    "positioning": (
        "CFTC Commitments of Traders snapshot — 45 flagship contracts. "
        "Data fields: four regime composites (risk_on_off, reflation, safe_haven, dollar; each a z-score aggregate across multi-contract baskets), top_divergences (spec-vs-commercial spread Z), top_unwind (positioning extremity × realized-vol regime), top_flows (largest WoW net changes as % of OI).\n"
        "Interpretation rules:\n"
        "- |regime z| ≥ 1.5 is meaningful, ≥ 2 is extreme.\n"
        "- |divergence_z| ≥ 2 is the historical threshold for contrarian setups; ≥ 3 is where base rates are strongest.\n"
        "- Commercials are producers / hedgers — smart money. When specs crowded one way AND commercials the other, commercials usually win over 8-12 weeks.\n"
        "- Managed-money percentile ≥ 0.95 + rising vol = forced-unwind risk.\n"
        "- Cross-validate: if ≥2 regime composites + ≥1 divergence align, that's a real thesis. A single number in isolation is usually noise.\n"
        "Coverage requirement — do NOT cherry-pick. Go through every row of top_divergences and top_unwind and account for ANY contract at |divergence_z| ≥ 3 OR unwind_score ≥ 0.7. The common failure mode is optimizing for a clean thematic cluster (e.g., 5 grains) and skipping single-name extremes that don't fit the theme (e.g., a lone FX cross at the top of the unwind list). Those are often the sharpest individual trades — name them.\n"
        "Output structure:\n"
        "1) Lead with the 1-2 strongest thematic or single-name reads.\n"
        "2) A short 'Also in this data' section listing any other contract at |divergence_z| ≥ 3 or unwind_score ≥ 0.7 that wasn't already covered, with one-line implication each. Skip this section only if genuinely none exist.\n"
        "3) Price action that would confirm or invalidate the top read.\n"
        "Do NOT repeat the field definitions — the user knows them. Do NOT cite specific historical years."
    ),
    "positioning_heatmap": (
        "CFTC positioning heatmap payload: per-tile `symbol`, `name`, `asset_class`, `pctile_3y` (spec net percentile, 0..1), `zscore_3y`, `chg_1w` (WoW contracts change), `divergence_z` (spec vs commercial spread Z).\n"
        "Interpretation rules:\n"
        "- Group by asset_class. Check if equities / rates / FX / commodities are coherent or conflicted.\n"
        "- pctile_3y ≤ 0.05 or ≥ 0.95 is a meaningful extreme.\n"
        "- Large chg_1w with high pctile = accelerating crowd. Large chg_1w with mid pctile = fresh positioning building.\n"
        "- divergence_z ≥ 2 → commercials taking the other side of a crowded spec long (contrarian bearish). ≤ -2 inverse.\n"
        "What to produce: the 2-3 most striking groupings or outliers, what they imply for the broader regime, and which single contract is the 'sharpest' name to watch. Use the symbol plus a price direction (e.g., 'CL structurally long, fade bounces'). Skip definitions."
    ),
    "positioning_divergence": (
        "Spec-vs-commercial divergence Z ranked table. Positive Z = speculators crowded long while commercials (producers / swap dealers hedging real flow) are crowded short. Negative Z = inverse.\n"
        "Interpretation rules:\n"
        "- |Z| ≥ 2 is the historical threshold where commercials systematically win over 8-12 weeks — has caught most major commodity and rate reversals of the last two decades.\n"
        "- |Z| ≥ 3 is where base rates are strongest.\n"
        "- Concentration matters: if divergences cluster in a theme (all energies, all grains, all rates), it's a sector call. But the cluster and individual single-name extremes coexist — don't pick one at the expense of the other.\n"
        "- A row at |Z| barely over 2 is a weak read.\n"
        "Coverage requirement: account for EVERY row in the table at |Z| ≥ 3. If the top read is a thematic cluster, still call out any |Z| ≥ 3 contract that sits outside the cluster — those are often the sharpest individual tickets.\n"
        "Output structure:\n"
        "1) The top thematic or single-name read, with implied trade direction.\n"
        "2) An 'Also extreme' list covering every remaining |Z| ≥ 3 row with one-line implication.\n"
        "3) A flag on whether the strongest row is genuinely at |Z| ≥ 3 or merely barely past 2 (weaker signal).\n"
        "Do NOT cite specific historical years — refer to past setups by asset (e.g., 'similar to prior oil-top setups')."
    ),
    "positioning_cta_watch": (
        "CTA forced-unwind watch payload: `unwind` list ranked by unwind_score (positioning extremity × realized-vol percentile). `flows` list ranked by this-week WoW change as % of open interest.\n"
        "Interpretation rules:\n"
        "- unwind_score ≥ 0.7 with direction=long and vol_pctile ≥ 0.7 is the forced-seller setup — next vol spike triggers deleveraging.\n"
        "- When a contract appears near the top of BOTH tables, the crowd just got more crowded right before vol crosses up. Strong near-term tell.\n"
        "- If `flows` shows specs building (chg_1w_pct_oi > 0) in a contract NOT yet at high pctile_3y, treat as early trend, not late extreme.\n"
        "- Direction-based flow vs positioning conflicts (spec net short AND net buying aggressively) often signal a capitulation-in-progress.\n"
        "What to produce: the single setup most likely to unwind this month, any cross-contract thread between the two lists, and the vol or price trigger that would confirm. Skip repeating what unwind_score means."
    ),
    "sector-overview": (
        "SPDR sector deep-dive — top-10 companies by weight in the ETF. Payload: "
        "`financials` (revenue, net_margin, roe, roa, debt_to_equity, current_ratio, eps per company) "
        "and `forecasts` (analyst revenue + EPS estimates for the next 4 quarters where available).\n"
        "Interpretation rules:\n"
        "- Dispersion matters more than average: a sector with 1-2 margin outliers plus a pack of weak-margin names tells a different story than a uniformly strong sector.\n"
        "- ROE > 20% with D/E < 1.5 is structurally healthy; ROE > 20% with D/E > 3 is leveraged capital return that can reverse on a rate move.\n"
        "- Forecast revisions embedded in the payload (if present as `up_pct_rev`) matter more than absolute level — name which companies have strengthening vs weakening estimate trends.\n"
        "Output: single best thesis on the sector in 2-3 sentences, then the one name with the cleanest long setup and the one with the cleanest short/avoid setup based on these rows. No preamble."
    ),
    "sector-valuation": (
        "SPDR sector valuation snapshot — per-company forward/trailing P/E, P/B, EV/EBITDA, FCF yield, "
        "dividend yield, payout ratio, net debt/EBITDA, beta, plus a `momentum` table (1M/3M/6M/12M total returns).\n"
        "Interpretation rules:\n"
        "- Cheap + outperforming = quality value; cheap + underperforming = value trap.\n"
        "- FCF yield > 6% with net_debt_ebitda < 2 is a structurally attractive cash-return profile.\n"
        "- Divergence between trailing_pe and forward_pe flags big earnings trajectory changes — call it out when the gap is >30%.\n"
        "- Beta > 1.3 means the sector amplifies SPY moves; frame momentum-vs-valuation reads in that context.\n"
        "Output: which valuation regime this sector sits in (cheap/expensive, momentum/mean-reversion setup), the 1-2 names with the best forward-P/E × momentum setup, and any value-trap candidates to avoid."
    ),
    "sector-alpha": (
        "SPDR sector alpha signals — `eps_revisions` (up_7d / up_30d / down_7d / down_30d / net_30d per company) "
        "and `insider` (Form 4 buy_count / sell_count / net_value over trailing 90 days). Momentum also included.\n"
        "Interpretation rules:\n"
        "- Cluster of ≥3 insider buys in 30d on the same name = meaningful; sells are noisier (10b5-1, option exercises, diversification).\n"
        "- Positive net_30d EPS revisions (analyst upgrades minus downgrades) > +3 on a name is a real signal.\n"
        "- Look for CONFLUENCE: insider buying + positive EPS revisions + positive 3M momentum = highest-conviction long candidate.\n"
        "- Inverse pattern (cluster sells + negative revisions + weak momentum) is the short/avoid setup.\n"
        "Output: the top 1-2 confluence-long names, the top 1-2 confluence-avoid names, and a one-line note on broad sector tilt of the signals (net bullish/bearish/mixed)."
    ),
    "sector-compare": (
        "Cross-sector comparison table — one row per SPDR sector (XLE, XLF, XLK, XLV, XLI, XLP, XLY, XLB, XLU, XLC, XLRE). "
        "Per-sector fields: median_forward_pe, avg_net_margin, avg_roe, companies_count, total_revenue_usd.\n"
        "Interpretation rules:\n"
        "- Lead with rotation reads: which sector is cheapest on forward P/E vs its margin quality (the classic value screen).\n"
        "- Flag divergences: a sector with high margin + low fwd P/E relative to peers is the asymmetric setup.\n"
        "- Do NOT recite all 11 rows — pick the 2-3 most actionable cross-sector reads.\n"
        "Output: the single strongest long sector call and short/avoid sector call with one-line justification each, then one non-obvious tilt (e.g., 'XLRE and XLU both yield 4%+ but XLRE's roe is half XLU's — prefer XLU for equal income + better quality')."
    ),
    "positioning_cta_model": (
        "CTA model for ONE contract — replicates Nomura / GS CTA desk readouts. Payload fields: `exposure` (signed, -100..+100; -100 = max short, +100 = max long), `bias_1w` and `bias_1m` (values: all_buying, all_selling, mixed, neutral), `triggers` (nearest prices where the ensemble's component signals flip, with type + distance_pct), `scenarios_1w` and `scenarios_1m` (grid of ±1σ/±2σ terminal-price moves with projected exposure).\n"
        "Interpretation rules:\n"
        "- bias='all_buying' → asymmetric upside tailwind (CTAs buy in every scenario on this horizon).\n"
        "- bias='all_selling' → forced-seller setup, expect systematic supply.\n"
        "- bias='mixed' → CTA flow direction depends on which way price resolves; the `triggers` ladder shows nearest flip prices.\n"
        "- Exposure near ±100 means near-max positioning; flow becomes asymmetric because CTAs can only go the other way.\n"
        "- Distance_pct < 3% on a nearby trigger means it's within normal-week price noise — high probability of firing.\n"
        "What to produce: crisp bias call for the contract (buying / selling / mixed with the key scenario), the nearest trigger that would flip the model (cite level + distance_pct), and whether the scenario grid implies favorable risk/reward from the current exposure. Skip explanations of what CTAs are."
    ),
    "options-iv-skew": (
        "Options Intelligence → IV & Skew tab payload. The UI shows: (1) a skew chart of Call IV vs Put IV across strikes for the SELECTED expiration, (2) a term-structure chart of ATM IV across all loaded expirations, (3) an expected-move table per expiration. Payload mirrors those: spot, selected_expiration, selected_dte, selected_atm_iv_pct, call_iv_curve (list of [strike, iv_pct]), put_iv_curve (same), term_structure list of {exp, dte, atm_iv_pct, expected_move_dollars, expected_move_pct}, term_shape ('Contango'|'Backwardation'|'Flat').\n"
        "Interpretation rules:\n"
        "- Start with the FRONT-MONTH expected move from term_structure[0]: 'Market is pricing ±X% into expiry'. Cite the dollar figure and DTE.\n"
        "- Describe the put/call skew AT the selected expiration by comparing IV at delta-equivalent strikes (e.g., 0.25-delta put IV vs 0.25-delta call IV in your data). Call out whether it's steeper/flatter than the equity-default (put skew of 3-8 vol points is normal).\n"
        "- Term-structure call: contango = normal; backwardation = event risk priced. If an expiration in the payload falls on a KNOWN scheduled event (earnings, FOMC, CPI) that you're certain of, name it; otherwise say 'markets are pricing near-term risk into {date}' without inventing a specific catalyst. If flat, say markets see no near-term catalyst.\n"
        "- Right-side skew (call IV > put IV at equivalent deltas) in a single name = flag hard; acquisition/squeeze/blow-off setup.\n"
        "- Cheap vs rich: front-month ATM IV well below typical realized = vol buying opportunity (long straddle / calendar); well above = premium-selling opportunity (iron condor / credit spread) — name the structure.\n"
        "What to produce: (a) one-line front-month expected move, (b) skew + term-structure regime in one sentence each, (c) ONE specific tradeable — exact structure + legs, not a generic 'buy calls'."
    ),
    "options-positioning": (
        "Options Intelligence → Positioning & Max Pain tab payload. The UI shows: (1) P/C volume + OI ratio cards, (2) a bell-curve gauge of current P/C vs ticker-specific historical mean ± 1σ bands, (3) open interest by strike bars for the selected expiration, (4) a max-pain curve with current spot and max-pain strikes marked, (5) P/C ratio by expiration bars. Payload: ticker, is_index (bool), spot, selected_expiration, pc_vol, pc_oi, pc_vol_z (vs historical mean), pc_hist_mean, pc_hist_std, regime_label, call_volume_45d, put_volume_45d, call_oi_45d, put_oi_45d, max_pain_strike, max_pain_pct_from_spot, heaviest_oi_strikes (list of {strike, call_oi, put_oi}), pc_by_expiration (list of {exp, pc_ratio}).\n"
        "Interpretation rules:\n"
        "- Lead with the P/C z-score vs the TICKER-SPECIFIC baseline (payload provides pc_hist_mean — don't apply index thresholds to single names). |z| ≥ 1.5 is contrarian territory, |z| ≥ 2 is extreme.\n"
        "- Max Pain: within 1% of spot = pinning expected into expiry. > 2% from spot = directional pin; state which way dealers will hedge.\n"
        "- Cross-check P/C OI vs P/C Volume. OI >> Volume ratio = stale hedges still open; fresh flow may differ. Volume leading OI = new positioning.\n"
        "- Look at pc_by_expiration — one bulging put-heavy expiration stands out = event pricing into that date. Name the date from the payload (don't guess an event name unless you're certain of a known catalyst on that date).\n"
        "- heaviest_oi_strikes reveal dealer hedging targets: heavy call OI above spot = resistance, heavy put OI below = support. Name the specific strike levels.\n"
        "What to produce: (a) regime call in one sentence referencing the z-score AND its historical baseline, (b) pin expectation into the selected expiration (max pain direction + magnitude), (c) the single most glaring OI concentration that suggests a dealer-hedge level."
    ),
    "options-flow": (
        "Options Intelligence → Order Flow tab payload. The UI shows: (1) an 'Unusual Activity' table ranked by Vol/OI with metrics for call/put split and total volume, (2) a Vol-vs-OI scatter with bubble size = Vol/OI ratio, (3) a 'Volume by Strike' (±10% of spot) bar chart across all expirations, (4) a Block Trades table filtered by notional with a computed call-vs-put bias flag. Payload mirrors those: ticker, spot, unusual{count, call_count, put_count, total_vol}, top_unusual list (strike, type, exp, volume, oi, vol_oi_ratio, iv_pct, delta, last_price), blocks{count, call_count, put_count, call_notional, put_notional, total_notional, bias_label}, top_blocks list (same fields as top_unusual plus notional).\n"
        "Interpretation rules:\n"
        "- Separate the signals: Vol/OI spikes reveal NEW retail+institutional positioning; Blocks reveal institutional-sized single prints. Address both.\n"
        "- For unusual activity: clusters across adjacent strikes / same-date expirations = campaign (scale-in). Isolated single-strike vol spikes = event speculation or a whale lottery ticket. Name the pattern you see.\n"
        "- For blocks: the bias_label is computed from call vs put notional. Validate it by checking delta of each top_block — high-|δ| puts are often HEDGES protecting long stock, not bearish bets; that reverses a naive 'puts = bearish' read.\n"
        "- If bias_label is 'N/A' (no blocks surfaced), say so plainly and move to unusual-activity signals.\n"
        "- Flag the ONE most-interesting specific print: name exact strike + type + expiration + size.\n"
        "What to produce: (a) the flow story — accumulation / distribution / hedging / speculation — with one piece of evidence, (b) the single most actionable specific trade visible (exact leg), (c) whether flow confirms or contradicts what Positioning tab's P/C said (if payload includes pc_regime_label)."
    ),
    "options-greeks": (
        "Options Intelligence → Dealer Greeks tab payload. The UI shows: (1) net GEX metric cards (total GEX, max pin strike, min acceleration strike), (2) a Net GEX by Strike bar chart with the zero-line and spot marker, (3) a Call vs Put GEX split chart showing where each side concentrates, (4) a Delta heatmap across strike × expiration. Payload: ticker, spot, total_gex, gex_regime_label, max_gex_strike, min_gex_strike, gex_by_strike (list of {strike, call_gex, put_gex, net}), strikes_in_window (bounds). Delta-heatmap details are visually rendered, not in the payload.\n"
        "Interpretation rules:\n"
        "- Lead with the gamma regime: positive net = dealers LONG gamma → they SELL rallies / BUY dips → vol-suppressing, mean-reverting tape. Negative = SHORT gamma → BUY rallies / SELL dips → trending, vol-amplifying.\n"
        "- Magnitude: for SPY/QQQ-scale indices, |total_gex| > 1e9 is meaningful. For single names, ≥ 1e7 is notable. Below that = noise; say so.\n"
        "- Max GEX = gravitational pin. Price tends to be pulled there into expiration; cite the strike + distance from spot.\n"
        "- Min GEX = acceleration zone. A break below/above min_gex_strike triggers dealer chasing in the direction of the break — name the specific level as the 'breakout trigger'.\n"
        "- Check gex_by_strike: where does CALL-side concentrate vs PUT-side? Call-heavy above spot = dealers long upside calls (they sell stock on rallies to hedge); put-heavy below = dealers short puts (they buy stock on dips). These are the tradeable intraday reversal levels.\n"
        "What to produce: (a) regime call + magnitude read, (b) the two key strikes — pin (max_gex) and acceleration trigger (min_gex) — with their implications, (c) what today's tape looks like under this regime (quiet chop vs directional continuation)."
    ),
    "options-oi-changes": (
        "Options Intelligence → OI Changes tab payload. The UI shows: (1) a daily call vs put aggregate OI line chart over the lookback window, (2) a 'Biggest OI Builds' table sorted by delta_abs, (3) a 'Biggest OI Unwinds' table sorted by delta_abs (most negative). Payload: ticker, lookback_days, n_days_captured, dates{first, last}, daily_net (list of {date, call_oi, put_oi}), biggest_builds (list of {strike, type, exp, first_oi, last_oi, delta_abs, delta_pct}), biggest_unwinds (same fields, deltas negative).\n"
        "Interpretation rules:\n"
        "- Read the daily_net series: is aggregate call OI trending UP while put OI trends DOWN (net bullish accumulation) or reverse (net bearish hedging)? Cite the actual % change in each.\n"
        "- Cover the top build and the top unwind by delta_abs — that's the table order. Don't cherry-pick to fit a thematic narrative.\n"
        "- Cluster check: multiple builds at adjacent strikes = campaign/scale-in; cite the cluster range. Multiple builds concentrated at one expiration date = event positioning — cite the DATE from the payload; only name a specific event if you're certain of a known catalyst on that date.\n"
        "- OTM call builds = directional bullish bets; OTM put builds = hedges OR bearish bets (check whether spot has been rising or falling over the window to distinguish).\n"
        "- Big unwinds on a rising tape = profit-taking (still bullish-consistent); big unwinds on a falling tape = capitulation/stop-outs (bearish-consistent).\n"
        "What to produce: (a) the dominant flow theme over the window (accumulation / distribution / rotation), (b) the single most-informative build (name strike + type + exp + delta_pct) AND the single most-informative unwind, (c) what price action would confirm or invalidate the read."
    ),
    "options-chain": (
        "Options Intelligence → Chain tab payload. The UI shows the full chain table for the selected expiration (strike × type with bid/ask/IV/delta/gamma/theta/vega/OI/volume), highlighting the ATM row. Payload is a summary — the chain itself is too big to pass: ticker, spot, expiration, dte, atm_row (the closest-to-money strike: bid, ask, iv_pct, delta, gamma, theta, vega, oi, volume), heaviest_call_oi (top 5 strikes by call OI with their OI + volume), heaviest_put_oi (same for puts), heaviest_volume (top 5 strikes across both types by volume with their vol_oi_ratio), bid_ask_spread_pct_atm.\n"
        "Interpretation rules:\n"
        "- Start with the support/resistance structure implied by heavy OI. Heavy call OI ABOVE spot = resistance (dealers long calls, they short stock on rallies); heavy put OI BELOW spot = support (dealers short puts, they buy stock on dips). Name the SPECIFIC strike levels.\n"
        "- Identify today's FRESH positioning via heaviest_volume with vol_oi_ratio ≥ 1: strikes trading MORE than they already have open are where new money is. Name 1-2.\n"
        "- ATM bid/ask spread as a liquidity tell: tight (<2% of mid) = institutional liquidity, actionable. Wide (>5%) = retail-only, skip.\n"
        "- Gamma/delta at the ATM row tells you how the chain REACTS to moves: high gamma = dealers hedge aggressively near spot; low gamma = stale expiry.\n"
        "- If heavy call AND heavy put OI both near spot = pin-risk (trapped between walls). If OI is all far OTM = clear-sky (trendier tape).\n"
        "What to produce: (a) the concrete support + resistance levels from OI walls (name strikes), (b) where new money is positioning today (heaviest_volume entries with vol_oi ≥ 1), (c) whether spot is trapped or has room to run."
    ),
    "wsb": (
        "r/wallstreetbets ticker-mention scan (plus r/options and r/stocks for signal quality). Payload has top_tickers list, each with: mentions (raw post+comment count), sentiment (-1..1, based on bull/bear keyword weights), options_lean ('calls'|'puts'|'mixed'|'neutral'), calls/puts mention counts, dd_posts (number of Due Diligence posts).\n"
        "Interpretation rules:\n"
        "- This is NOISE with occasional signal. Retail sentiment is noisy — the value is in the SHIFTS and EXTREMES, not the average.\n"
        "- Heavily-mentioned tickers with strong directional lean (sentiment > 0.5 or < -0.5) AND matching options_lean are the confluence trade. Single-signal alone is usually wrong.\n"
        "- 'Calls lean + bullish sentiment + a DD post' is the high-quality bull signal. The reverse for bearish.\n"
        "- Meme-only tickers with zero options activity are usually wrong — retail piles in at the top.\n"
        "- Contrarian read: if WSB is unanimously bullish on an already-run-up name, it's usually near the local top. If unanimously bearish and the stock has been dumped, often near a local bottom.\n"
        "What to produce: 1-2 tickers the WSB crowd is most bullish on with supporting confluence; 1 ticker where WSB is bearish AND options flow agrees; optionally 1 contrarian read where the crowd is over-concentrated. Skip indices / ETFs in the main call-outs."
    ),
    "polymarket": (
        "Polymarket prediction-market snapshot — curated trading-relevant events. Payload is a list of events, each with: title, category (Fed Rates / Economy / Geopolitics / Politics / Crypto / Sports / Other), volume_24h ($), liquidity ($), outcomes[] with yes_pct (0-100), days_out, actionability (0-50, higher = nearer-term + more uncertain).\n"
        "Interpretation rules:\n"
        "- Focus on NEAR-TERM + UNCERTAIN. A 50/50 market resolving in 7 days is far more tradeable than a 95/5 resolving in 180 days.\n"
        "- Volume matters — liquidity < $10K means the odds are noisy. Only reference markets with volume_24h ≥ $5K as crowd signal.\n"
        "- Cross-asset reading: when Fed-Rates markets say cuts are priced, ask whether bonds / duration / gold agree or diverge.\n"
        "- Geopolitics often mispriced short-term — that's where the alpha lives; flag if a geopolitical event is priced differently than typical news coverage suggests.\n"
        "- Economy markets (recession, GDP, CPI) tend to track consensus — flag only when they're meaningfully away from consensus.\n"
        "What to produce: 1) The single biggest signal from the crowd right now (the 'story'). 2) The sharpest near-term tradeable (high actionability, decent volume). 3) Any divergence worth flagging between Polymarket and the typical narrative. Skip sports unless it's a top-volume market."
    ),
}


class InterpretRequest(BaseModel):
    page: str
    data: dict
    subject: str | None = None  # e.g., ticker, fund name, politician — for context


def _interpret_cache_key(page: str, data: dict, subject: str | None) -> str:
    """Deterministic hash over the inputs AND the prompt text. Including
    BASE_SYSTEM + PAGE_CONTEXT means any prompt edit automatically invalidates
    cached answers for the affected page — no manual cache-wipe needed. Old
    entries sit orphaned until their 24h TTL expires."""
    ctx = PAGE_CONTEXT.get(page, "")
    payload = json.dumps(
        {"page": page, "system": BASE_SYSTEM, "ctx": ctx, "data": data, "subject": subject},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"ai_interpret:{page}:{digest}"


# TTL for AI interpretations. Payloads change weekly at most for positioning;
# for other pages they change when the user's input changes. 24h is a sensible
# compromise — re-interprets after a day even if inputs haven't moved.
_AI_CACHE_TTL = timedelta(hours=24)


@router.post("/interpret")
@limiter.limit("20/minute;500/day")
async def interpret(
    request: Request,
    body: InterpretRequest,
    user: str = Depends(get_current_user),
):
    """Ask Claude to interpret the data on a page. Returns plain text.

    Results are cached in the shared Supabase cftc_cache table keyed by a
    hash of (page, data, subject). Saves Claude calls when the same page
    data gets viewed across multiple users or sessions. Also cuts first-load
    latency from ~3-8s to under 1s when the cache is hot."""
    # Auth gate: every Claude call costs real money, so reject anonymous
    # requests at the edge rather than relying on Cloud Run rate limits.
    if user == "anonymous":
        raise HTTPException(401, "Sign in required for AI interpretation")

    # Cache check — Supabase-backed. Stale entries (> 24h) fall through to
    # recompute + rewrite.
    cache_key = _interpret_cache_key(body.page, body.data, body.subject)
    try:
        from src._cache_util import _supabase_get
        entry = _supabase_get(cache_key)
        if entry and (datetime.utcnow() - entry[0]) < _AI_CACHE_TTL:
            cached_value = entry[1]
            if isinstance(cached_value, dict) and cached_value.get("ok"):
                return {**cached_value, "cache_hit": True}
    except Exception as e:
        logger.debug(f"ai interpret cache lookup failed: {e}")

    api_key = get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(503, "AI interpretation unavailable — ANTHROPIC_API_KEY not configured")

    ctx = PAGE_CONTEXT.get(body.page, "")
    subject = f"Subject: {body.subject}\n\n" if body.subject else ""

    user_message = (
        f"Page: {body.page}\n\n"
        f"What this page shows: {ctx}\n\n"
        f"{subject}"
        f"Current data:\n```json\n{json.dumps(body.data, default=str, indent=2)[:20000]}\n```\n\n"
        f"Interpret these results for me. What does it mean?"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=700,
            system=[
                {
                    "type": "text",
                    "text": BASE_SYSTEM,
                    # Prompt cache — system prompt is reused across every
                    # interpretation request, so cache hits cut input cost ~75%.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        text_blocks = [b.text for b in msg.content if hasattr(b, "text")]
        interpretation = "\n".join(text_blocks)
        grounding = _check_grounding(interpretation, body.data)
        result = {
            "ok": True,
            "model": MODEL,
            "interpretation": interpretation,
            "grounding": grounding,
            "cache_creation_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0),
            "cache_read_tokens": getattr(msg.usage, "cache_read_input_tokens", 0),
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        }

        # Persist to shared cache only if the interpretation looks good —
        # skip when the grounding check flagged everything unverified, since
        # we don't want to re-serve a hallucinated answer for 24h.
        grounded = grounding.get("grounded_count", 0) if isinstance(grounding, dict) else 0
        unverified = grounding.get("unverified_count", 0) if isinstance(grounding, dict) else 0
        if interpretation and (grounded + unverified == 0 or grounded >= unverified):
            try:
                from src._cache_util import _supabase_put
                _supabase_put(cache_key, result)
            except Exception as e:
                logger.debug(f"ai interpret cache write failed: {e}")
        return {**result, "cache_hit": False}
    except anthropic.BadRequestError as e:
        logger.warning(f"Claude rejected interpret request: {e}")
        raise HTTPException(400, f"Claude rejected the request: {e}")
    except anthropic.APIError as e:
        logger.warning(f"Claude API error: {e}")
        raise HTTPException(502, f"Claude API error: {e}")
    except Exception as e:
        logger.warning(f"interpret failed: {e}")
        raise HTTPException(500, f"Interpretation failed: {e}")
