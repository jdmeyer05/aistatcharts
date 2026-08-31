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

MODEL = "claude-opus-5"

# The interpretation system prompt is versioned data now — baseline lives in
# src/prompt_defaults.py, and the home page resolves a possibly-newer champion
# through src/prompt_registry.py at request time.
from src.prompt_defaults import BASE_SYSTEM  # noqa: E402


# Grounding primitives live in src/grounding.py so the prompt-loop worker can
# grade replayed outputs with the same check, without importing FastAPI.
from src.grounding import (  # noqa: E402
    _NUM_TOKEN,
    _normalize_num,
    _collect_payload_numbers,
    _check_grounding,
)

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
    "home_macro_pressure": (
        "Home-page macro pressure scorecard. Answers: what is the macro backdrop currently doing to "
        "US equities, and where is the pressure coming from.\n"
        "Payload: `net_score` (mean of per-factor scores; positive = equity-supportive), `net_label`, "
        "`counts` (how many factors in each verdict), `change_window_days`, `lookback`, and `rows` — each "
        "with `label`, `group`, `kind` ('technical' = market-priced daily, 'fundamental' = reported "
        "economic data), `level`+`unit`, `change` (in the factor's own units; `change_mode` 'abs' means "
        "points/percentage-points, 'pct' means a relative move), `change_z` (how unusual that change is "
        "vs the factor's own history), `pctile` (level's position in its range, 0..1), `score` "
        "(= -adverse * change_z, positive = supportive), `verdict`, and `stale`.\n"
        "How scoring works — do not re-derive or contradict it: each factor is scored on the z-score of "
        "its recent CHANGE, flipped by whether rising helps or hurts equities. Level percentile is "
        "context, NOT the verdict. A factor can sit at the 98th percentile and still score supportive if "
        "it is falling; say so plainly when it happens rather than treating the percentile as the signal.\n"
        "Interpretation rules:\n"
        "- Lead with the net read, then immediately name what is driving it. A 'balanced' composite is "
        "frequently the average of a genuine tug-of-war, not calm — if supportive and headwind counts are "
        "both material, say what is fighting what.\n"
        "- Weight |change_z| >= 1 as the factors actually moving the needle. Ignore near-zero scores.\n"
        "- Watch for DIVERGENCE between groups, which is usually the most valuable read on the board: "
        "credit tight while rates vol rises, or market-priced (technical) factors deteriorating while "
        "reported (fundamental) data still looks fine. Technical factors turn first and fundamental data "
        "confirms later, so a technical-vs-fundamental split is an early-warning pattern — name it.\n"
        "- A `stale: true` row has not had a new print inside the window. Its flat reading is absence of "
        "news, not absence of pressure. Do not describe it as stable or reassuring.\n"
        "- The composite weights every factor equally. If one factor dominates the regime, say the "
        "composite understates it.\n"
        "What to produce, for an equity trader:\n"
        "1) One line: what the macro backdrop is doing to equities right now, and whether the pressure is "
        "building or easing.\n"
        "2) The two or three factors carrying that read, with their numbers — and the transmission "
        "mechanism, not just the direction (why does THIS move equities?).\n"
        "3) The most important tension or divergence on the board.\n"
        "4) What would flip the read — the specific factor and the move in it that would matter.\n"
        "Do NOT list every row; the table is already on screen. Do NOT cite specific historical years or "
        "crash analogies. Do NOT invent macro data that is not in the payload."
    ),
    "home_cta_flows": (
        "Home-page CTA flow board for the S&P 500 E-mini. Answers one question: what will systematic "
        "trend-followers mechanically do to equities over the next week and month, and what does that "
        "imply for the index path.\n"
        "Payload fields: `last_price` (spot), `current_exposure` (signed -100..+100; +100 = max long), "
        "`bias_1w` / `bias_1m` (all_buying | all_selling | mixed | neutral), `terminal` (per horizon "
        "'1w'/'1m', each scenario's `delta_exposure` = change in exposure from today), `pivots` "
        "(short_term/medium_term/long_term trend levels with `level` and `distance_pct` from spot).\n"
        "SIGMAS ARE PER HORIZON: `sigma_1w_pct` describes the 1w scenarios, `sigma_1m_pct` the 1m ones. "
        "`sigma_1_pct` refers to the chart's own horizon (`horizon_days`, 20d) — do NOT use it to describe "
        "a 1-week move; that would overstate the weekly move by roughly 2x. When quoting a scenario's "
        "price move, pair the delta_exposure with the sigma for that same horizon.\n"
        "UNITS: exposure is model points, NOT dollars. Never convert to $bn, never invent a notional or "
        "AUM figure — we do not have that scalar. Speak in exposure points, percentages and direction.\n"
        "Interpretation rules:\n"
        "- CTA flow is mechanical and price-insensitive: it executes on trend signals regardless of "
        "valuation or news. That's why it matters for the short-horizon index path even when fundamentals "
        "haven't changed.\n"
        "- bias='all_selling' → systematic supply in every scenario; rallies get sold into, a headwind "
        "independent of direction. bias='all_buying' → the inverse, asymmetric tailwind.\n"
        "- bias='mixed' → flow AMPLIFIES whichever way price resolves: they sell into weakness and buy "
        "into strength. That is destabilizing, not neutral — say so. It widens the tails rather than "
        "picking a side.\n"
        "- Compare the magnitude of the down-scenario delta vs the up-scenario delta at the same sigma. "
        "If |down| materially exceeds |up|, flow is asymmetrically bearish: a selloff gets amplified harder "
        "than a rally. This asymmetry is usually the most important read on the board — lead with it when present.\n"
        "- current_exposure near +100 means CTAs are already near max long: little room left to buy, and "
        "the entire flow distribution skews to selling. Near -100 is the mirror image.\n"
        "- Pivot proximity is the trigger probability. |distance_pct| under ~1% means spot is effectively "
        "AT the level and it is likely to fire within a normal week (compare to sigma_1_pct — if the pivot "
        "is inside a 1σ move, it is in play). Under ~3% is within a normal month.\n"
        "- Breaching a pivot from above flips that trend component short, which turns projected selling "
        "into realized selling. Name the specific level and what breaking it sets off.\n"
        "What to produce, for an equity trader who already knows what a CTA is:\n"
        "1) One-line read on what systematic flow does to the index over the next 1-2 weeks — direction, "
        "or explicitly 'amplifies both tails' if the bias is mixed.\n"
        "2) The asymmetry: which side has more mechanical flow behind it, citing both delta_exposure figures.\n"
        "3) The single pivot that matters most (cite level + distance_pct) and what breaking it triggers.\n"
        "4) What this means for equity markets in practice — the effect on realized vol, on the durability "
        "of a rally or a dip, and whether flow supports or fights the prevailing trend. Be concrete about "
        "the mechanism, not generic.\n"
        "Do NOT explain what a CTA is. Do NOT cite specific historical years or crash analogies. Do NOT "
        "claim this reproduces any bank's published estimate — it is an independent reconstruction."
    ),
    "home_page": (
        "THE WHOLE HOME PAGE, in one payload, for a trader who trades E-MINI S&P FUTURES (ES) "
        "INTRADAY — in and out the same session. This replaces what used to be three separate "
        "per-card interpretations, so your job is the SYNTHESIS none of them could do: reconcile "
        "the blocks against each other and produce ONE coherent read of the session.\n"
        "Blocks, roughly in order of intraday importance:\n"
        "- `conditions` — a conditions-only gate on whether the session suits intraday trading, "
        "with each factor's contribution. Never directional.\n"
        "- `levels` — the reference ladder. `mode` is 'rth' (session open or finished today), "
        "'premarket' (no session yet — there are NO session levels and you must not imply any) or "
        "'last_session' (market shut; write in the past tense as preparation for the next open). "
        "`stale: true` means the quote lags a trading session, so every distance is measured off "
        "an old price — say that before anything else. `contract_roll_risk` means cross-session "
        "distances may be off by the roll spread.\n"
        "- `expected_move` — `sigma_handles` is a one-sigma close-to-close move; "
        "`expected_range_handles` is the expected HIGH-LOW and is ~1.6x larger. Do not confuse "
        "them. `consumed` is measured against the RANGE. An estimate whose `quote_source` is "
        "'settled' was priced with the market shut and is unreliable.\n"
        "- `dealer_gamma` — SPX. `regime` decides which playbook is correct and outranks nearly "
        "everything: LONG gamma means dealer hedging leans against moves, so breakouts fail and "
        "rotation is the base case; SHORT gamma means hedging amplifies, so trends extend and "
        "fading extremes loses. `flip_es` is where that inverts. Dealer inventory is INFERRED "
        "from open interest under a standard convention, never observed — use the flip level and "
        "the walls, never quote a gamma total as a dollar amount.\n"
        "- `intraday_structure` — day type, initial balance, relative volume (under ~0.8x means "
        "breaks fail more), overnight inventory (a lopsided Globex book often corrects in the "
        "first hour), naked POCs and unfilled gaps as magnets, cross-asset confirmation.\n"
        "- `base_rates` — MEASURED frequencies with `n` attached. Use them to put a number on a "
        "claim, and respect them when they contradict convention. Always cite `n`. They are "
        "unconditional priors, not forecasts.\n"
        "- `todays_schedule` / `upcoming_events_2w` — scheduled risk on the clock. Each upcoming "
        "event carries TWO separate facts that routinely disagree. `scheduled_discontinuity` is a "
        "TIMING judgement: a release lands at a known time and deserves attention on the clock. "
        "`measured_range_multiplier` is MEASURED magnitude over 3,677 sessions, where 1.00 is an "
        "ordinary day. CPI is a scheduled discontinuity AND measures 1.06x, ranked 12th of 23. "
        "Quad witching measures 0.94x — narrower than normal. Only Nonfarm payrolls survives the "
        "multiple-comparison correction, at 1.39x, so it is the ONLY event you may describe as "
        "reliably widening the range; for any other, state what was measured and that it is not "
        "established. Never infer a wide session from the timing flag alone. Never give a "
        "multiplier a direction — magnitude only was measured. Never carry an event's effect into "
        "the following session; every next-session multiplier is near 1.0. `never_measured: true` "
        "means the event was outside the study, which is a different statement from 'measured, "
        "and ordinary' — do not collapse the two.\n"
        "- `blocks_unavailable` / `blocks_stale` / `coverage_note` — what you could not see, and "
        "what you can see but should not describe in the present tense. `blocks_unavailable` names "
        "blocks that DID NOT LOAD: absent for a fetch reason, not because they had nothing to say, "
        "so do not characterise them and never read an absence as agreement or as a quiet tape. "
        "`blocks_stale` names blocks that loaded but have not refreshed inside their own cadence, "
        "with an age in minutes — their numbers are real and describe an earlier moment. When "
        "either list is non-empty, say so in one clause rather than writing around it.\n"
        "- `cta_positioning`, `macro_backdrop`, `sector_rotation_rrg`, `sectors_today`, "
        "`vol_landscape`, `sp_valuation` — all SWING horizon.\n"
        "- `sector_rotation_rrg` DESCRIBES the environment and forecasts nothing. Its state was "
        "tested against the next session's direction, range and trend-efficiency over 1,829 "
        "day-pairs and predicts none of them. The `regime` block (tilt, dispersion, correlation) "
        "carries a percentile against its own history plus the conditions that have ACCOMPANIED "
        "that level — co-occurrence, not causation, and partly definitional since defensive "
        "leadership is what a falling market looks like. Cite the level and its percentile. Never "
        "say rotation implies a direction or a range for today.\n"
        "HORIZONS MUST NOT MIX. This trader closes flat by the bell. Levels, gamma, expected move, "
        "structure and the clock are intraday. CTA flow, the macro scorecard, sector rotation and "
        "valuation resolve over weeks — they set which way to LEAN and nothing more. Turning any "
        "of them into an intraday entry is the single worst error you can make here. S&P valuation "
        "in particular has sat at extremes for years and is narrative context only, never a trade.\n"
        "RECONCILE, DON'T LIST. The value of one interpretation over several is that you can say "
        "when the blocks DISAGREE — long gamma against a wide expected move, bullish CTA flow "
        "against deteriorating breadth, a constructive macro backdrop against thin participation. "
        "Name the conflict and say which one governs for a session-length holding period. If they "
        "all agree, say that plainly and briefly; it is a stronger signal than any single block.\n"
        "What to produce, for someone who already knows what VWAP, a value area, gamma and a CTA "
        "are — around 250-400 words, no headings unless they genuinely help:\n"
        "1) What kind of session this is shaping up to be, and the two or three facts that decide "
        "it. Lead with location relative to value and the gamma regime.\n"
        "2) The levels that actually matter today, with their numbers, and what price doing "
        "something at each would tell you.\n"
        "3) Scheduled risk and how it should change timing or size.\n"
        "4) The most important DISAGREEMENT between blocks, or an explicit note that they align.\n"
        "5) The swing-horizon lean, labelled as such in one short sentence.\n"
        "DESCRIBE WHAT IS PRICED; NEVER INSTRUCT A TRADE. This overrides the general style rule "
        "about naming a tradeable implication — that rule is right for the options and flow "
        "pages and wrong here, and this surface is the one with a scored record to protect. "
        "Write what the market is pricing, what has been measured, and what would confirm or "
        "invalidate a read. Do NOT write imperatives aimed at a position: no 'fade the edges', "
        "'buy/sell/short the ...', 'wait for acceptance', 'trim into', 'stay flat', 'size up', "
        "'front-load', 'don't chase'. 'Long gamma, so breakouts have tended to fail' is right; "
        "'fade the edges' is the same claim wearing an instruction, and only the second one is "
        "unfalsifiable. A closing 'Bottom line:' is still expected — as a statement of the "
        "session's character, not a recommendation.\n"
        "Do NOT explain what any of these instruments are. Do NOT give entry, stop or target "
        "prices or position sizes. Do NOT convert CTA exposure or gamma into dollars. Do NOT "
        "invent levels, times or events not in the payload. Do NOT simply walk the blocks in "
        "order — that is the failure mode this consolidation exists to fix.\n"
        "`drivers`, when present, is MEASURED and replaces any impression you might form about "
        "what is moving the tape: a rolling 126-session regression of SPY daily returns on rates, "
        "the dollar, oil and gold. `share_of_variance` is each market's incremental share, "
        "`corr_with_spy` carries the sign, and `rank_a_year_ago` is the same measurement twelve "
        "months earlier — the rotation between the two is the part worth a sentence, because it "
        "is the one thing on this page a snapshot cannot show. It is SAME-DAY co-movement whose "
        "next-day correlations were measured at essentially zero, so say the tape HAS MOVED WITH "
        "a market; never that a market is driving, caused, or will move equities. Below 0.20 "
        "`explained_share` the four barely account for the tape and the honest read is that the "
        "move has been idiosyncratic, not the top of a weak list."
    ),
    "home_es_briefing": (
        "Home-page session briefing for someone trading E-mini S&P futures (ES) INTRADAY — in and out "
        "the same session. Answers one question: what should I expect from this session, right now.\n"
        "Payload: `session` (current phase + what that phase implies), `levels` (`last` = spot, and a "
        "`levels` list each with `label`, `group`, `value`, `distance` = last - level in handles, "
        "`distance_pct`, `side`), `nearest` (closest reference level), `schedule` (this session's "
        "catalysts with `time_et`, `impact`, `minutes_away`, `status`, `before_open`, `derived`, plus "
        "`kind`/`affects`/`time_approx`/`market_cap` on single-name earnings rows), `next_event` "
        "(intraday only — after-the-bell events are deliberately excluded from it), `after_close` "
        "(what lands after this session's bell) and `event_premium` (what SPX options charge for the "
        "overnight containing it), `cta` "
        "(systematic flow: `bias_1w`, `current_exposure` in model points, `pivots`), `macro` (multi-factor "
        "backdrop scorecard: `net_label`, `counts`), and `news` headlines.\n"
        "CHECK `levels.mode` BEFORE WRITING A WORD. 'premarket' = the cash session has NOT opened; "
        "there are no session high/low/VWAP levels and you must not invent or imply them — the frame "
        "is the developing overnight range and the gap to the prior close. 'rth' = a cash session is "
        "open or finished today; `rth_complete` tells you which, so do not call a finished range "
        "'developing'. 'last_session' = the market is shut and these are the last completed session's "
        "final values — write about it in the past tense and frame it as preparation for the next "
        "session, not as a live read. If `stale` is true the quote is `bar_age_min` minutes behind a "
        "trading session, so every distance is measured from an old price — say so before anything else.\n"
        "THE NEWER BLOCKS, AND HOW TO USE THEM:\n"
        "- `gamma` is SPX dealer gamma. `regime` decides which playbook is correct and outranks "
        "almost everything else: LONG gamma means dealer hedging leans against moves, so breakouts "
        "fail and rotation is the base case; SHORT gamma means hedging amplifies, so trends extend "
        "and fading extremes is the losing side. `flip_es` is where that flips — say which side "
        "price is on. Dealer inventory is INFERRED from open interest under a standard convention, "
        "not observed, so treat the flip level and the shape as the signal and never quote "
        "`total_gex` as a dollar amount of anything.\n"
        "- QUOTE THE HEADLINE EXPECTED MOVE, not whichever estimate suits the story. `expected_move` "
        "carries a chosen `headline` plus the alternatives in `estimates`. A live run quoted the ATR "
        "figure while the headline was the implied one — defensible ONLY if you say the estimates "
        "disagree and why. Lead with the headline; reach for a second estimate only when the gap "
        "between them is itself the point.\n"
        "- NAME A LEVEL, NAME ITS REACH. Every level carries `reach`. A target the session cannot "
        "plausibly get to is not a target: listing something marked `a stretch` or `beyond a typical "
        "session` as a downside magnet, with no qualifier, reads as authoritative and is the error a "
        "trader spots first.\n"
        "- `expected_move`: `expected_handles` is a one-sigma close-to-close move; `expected_range` "
        "is the expected HIGH-LOW and is ~1.6x larger. Do not confuse them. `consumed.pct` measures "
        "the session's range against `expected_range` — high means the day's room is largely spent "
        "and chasing pays up; low means the move is still ahead. An estimate marked "
        "`quote_source: settled` was priced with the market shut and is unreliable.\n"
        "- `intraday`: `day_type` in market-profile terms, `opening_range.ib` (the first hour, which "
        "frames the day), `relative_volume` (participation vs the same point of a normal session — "
        "under ~0.8x means breaks fail more), `overnight_inventory` (a lopsided Globex book often "
        "gets corrected in the first hour), plus naked POCs and unfilled gaps as magnets.\n"
        "- `base_rates` are MEASURED frequencies on the cash index, with `n` attached. Use them to "
        "put a number on a claim instead of asserting it, and respect them when they contradict "
        "convention — CPI days, for instance, measure at only ~1.0x a normal session range. Always "
        "cite `n` alongside a rate. They are unconditional priors, not forecasts.\n"
        "- `session_path` says WHEN a session gets where it is going, measured on hourly cash bars. "
        "`where_we_are` is the live pointer — how much of a typical session's range is already "
        "covered by this hour and how often the high or low is already in. Use it to temper targets "
        "late in the day: a level a full range away at 14:00 is a different proposition than the "
        "same level at 10:00. `first_hour` is the initial balance; price leaves it on almost every "
        "session, so 'it extended' means nothing on its own — ONE-sided versus BOTH-sided is the "
        "information, and `ib_break_follow_through` shows a marginal break is close to a coin flip "
        "while a decisive one holds far more often. Its window is shorter than `base_rates` and it "
        "says so; do not merge the two sample sizes.\n"
        "- `breadth` is how many stocks are going with the index. `divergence` is the headline: an "
        "index up on negative net advancers is a narrow move carried by its largest members, and "
        "narrow moves are the ones that retrace — say so when it fires. `trin` above 1 means volume "
        "is concentrated in decliners. `equal_vs_cap` is an INDEPENDENT check built from two ETFs "
        "rather than from the counts, so when it disagrees with the counts, doubt the counts. All of "
        "it is reconstructed on a liquid US universe, NOT NYSE issues, so never present it as 'the "
        "NYSE advance-decline' and never claim it ties out against a terminal. NYSE TICK is not in "
        "the payload because no wired source provides it — do not refer to TICK at all.\n"
        "- Each level carries `reach` and `pct_of_expected_range` — its distance as a share of the "
        "expected session range. USE IT when naming the levels that matter: one marked `routine` is "
        "in play today, `a stretch` needs a trend day, and `beyond a typical session` must not be "
        "offered as a target at all, however important it is structurally. Naming an unreachable "
        "level as today's objective is the fastest way to sound wrong to a trader.\n"
        "- `candle_context` is what the last daily bar says about tomorrow's RANGE, measured on "
        "434,624 bars. Geometry forecasts range strongly (rank IC 0.158, t=75) and direction barely "
        "(IC -0.016), so use it for how much room the session has and whether a stop sits inside the "
        "noise — NEVER as a directional read. `measured_vs_implied` sets it against the options-"
        "implied range; the two are independent estimates of the same quantity, so a gap says premium "
        "is rich or cheap against recent behaviour, not which way price goes. Cash index, not ES.\n"
        "- `conditions` is a conditions-only gate on whether the session suits intraday trading. It "
        "is never directional; do not turn it into a bias. It does NOT account for breadth, so weigh "
        "breadth yourself rather than assuming the gate already has.\n"
        "HORIZON IS THE WHOLE POINT. This trader closes flat by the bell. Anything that resolves over "
        "weeks is context for which way to lean, never a trade. The CTA board and macro scorecard are "
        "SWING-horizon inputs — use them for directional lean only, and say so. Do not turn a macro "
        "reading into an intraday entry.\n"
        "Level vocabulary — use it precisely, these are not interchangeable:\n"
        "- VWAP is the session's most-watched bias line; acceptance above or below it is the read, a "
        "single poke through it is not.\n"
        "- POC is the fairest price and behaves as a magnet. Value area high/low bound the 70% of volume; "
        "OUTSIDE the value area price is in discovery and trends, INSIDE it price rotates and mean-reverts. "
        "That distinction should drive whether you frame the session as trend or chop.\n"
        "- Overnight (Globex) levels are made on thin volume and break more easily than RTH levels made on "
        "size. Never treat an overnight high as equivalent to a prior-day RTH high.\n"
        "- Prior-day high/low/close frame the gap and the day's opening bias.\n"
        "Interpretation rules:\n"
        "- Lead with LOCATION: where price sits relative to value and VWAP. That single fact sets whether "
        "the session is more likely to trend or rotate, and everything else is secondary to it.\n"
        "- Scheduled risk dominates the clock. A high-impact 08:30 print means the pre-open range is "
        "unreliable and the real session starts after it. An event `minutes_away` inside ~30 usually means "
        "liquidity thins and spreads widen INTO it — say that plainly. `before_open: true` sets the tone "
        "for the day; a mid-session print interrupts an already-established range instead.\n"
        "- A `derived: true` event came from a scheduling rule rather than a published calendar and can "
        "slip a day. Hedge the wording on those; never state a derived time as certain.\n"
        "- Schedule events carry `kind`. A `kind: \"earnings\"` row is a single company, and its "
        "`affects` field — NOT its date — says which session it can touch. `next_session_gap` lands "
        "after this bell and CANNOT move today's range: never let it change the intraday framing, the "
        "levels or the timing of an entry. It bears on one decision only, whether to carry a position "
        "through the close. `this_session_gap` already printed overnight, so the gap IS the reaction "
        "and the overnight range is not an ordinary one — say so rather than reading that range as "
        "normal Globex behaviour. `this_session_open` is pre-open and sets the tone like an 08:30 "
        "print does.\n"
        "- `time_approx: true` means the clock time is a convention, not a published minute — "
        "companies report across a half-hour window. Never quote an approximate time as exact.\n"
        "- `event_premium` is what SPX options CHARGE for the close-to-close segment containing an "
        "after-the-bell event, from two straddles. `segment_handles` is that priced move and is "
        "valid at any hour. `vs_session` is how many ordinary sessions of movement are priced into "
        "that one overnight, so 1.0 is a normal night and above ~1.3 is a genuinely expensive one. "
        "This is the ONLY number here that sizes such an event — use it instead of adjectives, and "
        "compare it to the scale the card already uses elsewhere. `vs_session` is NULL once the "
        "session is under way, because the straddle it divides by then covers only the hours that "
        "remain; when it is null, quote `segment_handles` alone and never reconstruct a multiple "
        "from the two straddle values yourself — that is the exact error the field was withheld to "
        "prevent. When `quote_source` is \"settled\" both straddles came from a closed book; call "
        "the multiple indicative. When `available` is false, say the premium was not measured and "
        "why; do not substitute a guess.\n"
        "- An earnings row's `also_reporting` lists names in the same window that were below the "
        "size cut and have no row of their own. If it is present, say the list is the largest few "
        "rather than implying it is everything reporting.\n"
        "- An earnings row's `market_cap` is the SELECTION criterion and nothing more. There is no "
        "index-weight feed on this stack, so you must NEVER convert a market cap into index points, a "
        "share of the index, or a contribution to the expected move. Size ranks the name; "
        "`event_premium` prices the event.\n"
        "- Respect the phase. The opening hour carries the widest ranges, midday compresses and is where "
        "chop risk is highest, and the closing drive builds MOC imbalances. The same level means different "
        "things at 09:45 and at 12:30.\n"
        "- Distances are in ES handles. Put them in context of the session's own developing range rather "
        "than quoting a bare number.\n"
        "What to produce, for a trader who already knows what VWAP and a value area are:\n"
        "1) One line on what kind of session this is shaping up to be — trend or rotation — and why, from "
        "location relative to value.\n"
        "2) The two or three levels that actually matter today, with their numbers and what price doing "
        "something at each one would tell you.\n"
        "3) The scheduled risk on the clock and how it should change position sizing or timing.\n"
        "4) The directional lean from CTA flow and macro, explicitly labelled as swing-horizon context "
        "rather than an intraday signal.\n"
        "Do NOT explain what VWAP, a value area or a POC is. Do NOT give entry/stop/target prices or "
        "position sizes — describe conditions and what would confirm or invalidate a read, and let the "
        "trader take the trade. Do NOT convert CTA exposure into dollars or notional; it is model points "
        "and no AUM scalar exists. Do NOT invent levels, times or events that are not in the payload."
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
    "ai-infra-grid": (
        "AI Infrastructure → Grid Reality payload. The single question: is electricity demand ACTUALLY growing where data centers are being announced? Payload: window{recent[start,end], prior[start,end]}, aggregate{all, dc_flagged, not_flagged, spread_pp, n_flagged, n_not_flagged} (all demand-weighted % growth), rows[] each {ba, name, region, dc_note, dc_flagged, trailing_12m_twh, prior_12m_twh, growth_pct, delta_twh, coverage}, excluded[].\n"
        "Data: EIA-930 metered daily demand per balancing authority, complete calendar months only, trailing 12m vs prior 12m.\n"
        "Interpretation rules:\n"
        "- This is REALISED metered demand. It is the ground truth against which announced and queued data center load should be judged. Announced GW is not demand; a queue position is not a generator.\n"
        "- `dc_flagged` is EDITORIAL — EIA-930 does not separately meter data center load. NEVER describe the flagged aggregate as 'data center demand'. It is total demand in balancing authorities where data center activity is publicly concentrated. Say this plainly if you cite the split.\n"
        "- `spread_pp` (flagged minus not-flagged growth) is the headline. A LARGE positive spread supports the thesis that data center load is showing up in metered demand. A SMALL spread is a genuine finding and must be stated as one — it means the announcements are not yet visible in aggregate metered load. Do not manufacture significance from a small spread.\n"
        "- Absolute TWh matters more than percentage for the large BAs: 2% on PJM is a bigger physical event than 11% on a small BA. Always pair a growth rate with delta_twh.\n"
        "- `coverage` below ~0.98 means gaps in the EIA feed; treat that BA's growth as low confidence and say so.\n"
        "- Total system demand growth is normally ~0-1%/yr. Anything sustained above ~3% is historically unusual and worth naming.\n"
        "What to produce: (a) the aggregate picture in one sentence, including the flagged-vs-unflagged spread and whether it is large enough to mean anything; (b) the 2-3 BAs carrying the most absolute TWh growth, named with both % and TWh; (c) any BA that is flagged but SHRINKING, which is evidence against the flag. End with a 'Bottom line:' stating what the metered data does and does not yet confirm about the build-out."
    ),
    "ai-infra-capacity": (
        "AI Infrastructure → Capacity Additions payload. The supply-side counterpart to Grid Reality: is generation actually being built where load is growing? Payload: snapshot (YYYY-MM), years[], by_ba[] each {ba, name, region, dc_flagged, added_mw, by_year{}, planned_retirement_mw, net_mw, operating_mw}, by_technology[] each {technology, by_year{}, total_mw}.\n"
        "Data: EIA operable-generator inventory (Form EIA-860M), each unit bucketed on the month it entered service.\n"
        "Interpretation rules:\n"
        "- BACKWARD-LOOKING ONLY. EIA's API exposes operable units; planned and under-construction units are NOT included. Never describe these numbers as a pipeline, forecast, or anything forward-looking.\n"
        "- `net_mw` = added minus planned retirements, and it is the number that matters. A BA adding 20GW while retiring 12GW is not adding 20GW of headroom. Lead with net, not gross.\n"
        "- Cross-reference the load side when the user has it: a BA with strong load growth and weak NET additions is the tightening case; strong additions against flat load is the opposite. This gap is the point of the page.\n"
        "- Technology mix is diagnostic. Solar and storage interconnect fastest but are not firm capacity; gas combined-cycle is firm but slow to build. A mix dominated by solar against firm-load growth is a reliability story worth naming.\n"
        "- Survivorship: units retired before the snapshot are absent, which slightly understates older years. Do not over-read year-on-year changes in the earliest year shown.\n"
        "- The final year in `years` is partial (the snapshot is mid-year). Never annualise it or compare it like-for-like against complete years.\n"
        "What to produce: (a) which BAs are adding real NET capacity and which are treading water; (b) the technology mix and what it implies for firm capacity; (c) the sharpest add-versus-retire divergence in the data. End with a 'Bottom line:' naming where the supply response is and is not keeping up."
    ),
    "ai-infra-capital": (
        "AI Infrastructure → Capital Commitment payload. Curated, not computed — disclosed capex guidance against observable AI revenue at explicitly stated scopes. Payload: capex{entities[], non_additive[], subtotal_low_usd_bn, subtotal_high_usd_bn, pct_of_us_gdp_low/high, prior_year_partial_usd_bn}, revenue_scopes[] each {scope, value_usd_bn, detail, source, as_of, double_counts, preferred, coverage_low_pct, coverage_high_pct}, us_nominal_gdp_usd_bn.\n"
        "Interpretation rules:\n"
        "- The coverage ratio (revenue ÷ capex) ranges roughly 5% to 58% ACROSS SCOPES. There is no single correct number and you must not present one. Always name the scope with the ratio.\n"
        "- The scope marked `preferred: true` (frontier laboratory run-rates) is the cleanest test of END DEMAND. The scope marked `double_counts: true` includes purchases of the very infrastructure being capitalised and is not a valid coverage measure — say so if you cite it.\n"
        "- Capex is an annual FLOW; laboratory revenue is a RUN-RATE. This flatters the capital side. State the mismatch.\n"
        "- Low coverage during a build-out is NOT by itself evidence of mispricing — every infrastructure cycle runs negative coverage while building. What matters is the DIRECTION over time and how much of the gap is debt-funded. Be explicit about this; do not treat a low ratio as a verdict.\n"
        "- Entities report on different bases. Oracle is in `non_additive` because it uses a May fiscal year and must never be summed into the calendar-year subtotal.\n"
        "- Gross capex as a share of GDP includes foreign spending and imported content, so it is NOT comparable to AI investment as measured in the US national accounts (~0.8-1.4% of GDP). Do not conflate them.\n"
        "What to produce: (a) the coverage ratio at the preferred scope, with the scope named and the run-rate-vs-flow caveat; (b) how much the answer moves across scopes, as evidence that the headline is a definitional choice; (c) what would have to be true for coverage to improve. End with a 'Bottom line:' on what the capital-versus-revenue gap does and does not tell you today."
    ),
    "causality-ccf": (
        "Causality → Tab 1 (CCF Lead/Lag) payload. The user is exploring whether one macro series leads another at the daily frequency. The page is macro-only — no single-name equities. Two modes:\n"
        "(A) PAIR mode payload: x{symbol, transform, adf_p}, y{symbol, transform, adf_p}, lookback ('1Y'|'3Y'|'5Y'|'10Y'), max_lag, result{lags[], ccf[], conf_band, n, peak{lag, rho}, x_leads{lag, rho}, y_leads{lag, rho}, contemp_rho}.\n"
        "(B) SCAN mode payload: target, lookback, max_lag, target_meta, rows[] each with {driver, label, category, x_leads_lag, x_leads_rho, y_leads_lag, y_leads_rho, peak_lag, peak_rho, contemp_rho, n, conf_band, transform}. Sorted by |x_leads_rho| descending — drivers leading target most strongly at positive lag.\n"
        "Sign convention: lag > 0 ⇒ X leads Y. lag < 0 ⇒ Y leads X. ρ sign indicates direction (positive = same-direction co-movement, negative = inverse).\n"
        "Interpretation rules:\n"
        "- This is PRECEDENCE, not causation. CCF is bivariate so it cannot rule out a hidden common driver. Say so explicitly when |contemp_rho| is comparable to peak |ρ| at small lag — that's a co-mover, not a leader.\n"
        "- Conf band: |ρ| < conf_band is statistical noise. Don't cite a peak if it's inside the band — say the relationship is not significant at the daily horizon.\n"
        "- Lead/lag interpretation: a meaningful peak ρ at lag = +N means X today is correlated with Y N days later. Frame this in trading terms (e.g., 'DXY moves ahead of EM equities by 2-3 days at ρ=-0.42').\n"
        "- Contemporaneous (lag=0) dominance with weak lagged ρ ⇒ co-movement, not lead/lag — flag this so the user knows it's not a timing signal.\n"
        "- For SCAN mode: name the top 2-3 drivers by |x_leads_rho| outside the conf band, name their lag in days and sign, and note any cluster (e.g., 'all rates points lead XLF by ~3 days at similar ρ' = consistent macro story).\n"
        "- Stationarity transform applied is in the payload — if a series ended up at a different transform than its default (e.g., level → diff), don't surface it unless the user asked; treat it as a clean input.\n"
        "What to produce: (a) one sentence stating the strongest finding and its lag/sign with conf-band context; (b) a one-line caveat mapping CCF→Granger→TE→VAR escalation ('Granger and Transfer Entropy will test whether this lead carries predictive content under linearity / nonlinearity'); (c) for SCAN mode only, the 2-3 highest-conviction leaders ranked, each with its lag and ρ. End with a 'Bottom line:' that names the cleanest tradeable lead/lag pair from the data."
    ),
    "causality-granger": (
        "Causality → Tab 2 (Granger Causality) payload. The user is testing whether past values of one macro series improve prediction of another, BEYOND the target's own past. Granger requires stationarity; auto-stationarization is applied. The page is macro-only.\n"
        "(A) PAIR mode payload: x{symbol, transform, adf_p}, y{symbol, transform, adf_p}, lookback, max_lag, x_to_y{by_lag[{lag, f_stat, p_value}], best{lag, p_value}, verdict, n}, y_to_x{same}. Verdict labels at α: 'strong' p<0.001, 'moderate' p<0.01, 'weak' p<0.05, 'none' p≥0.05.\n"
        "(B) SCAN mode payload: target, lookback, max_lag, n_drivers_tested, bonferroni_m, top_rows[] each with {driver, label, category, xy_best_lag, xy_best_p, xy_p_bonf, yx_best_lag, yx_best_p, yx_p_bonf, transform}. Sort default: by xy_best_p ascending (drivers most strongly causing target first). bonferroni_m = number of family tests run; xy_p_bonf = xy_best_p × m.\n"
        "Interpretation rules:\n"
        "- Granger ≠ structural causation. It says 'past X helps predict Y' under a LINEAR VAR. A nonlinear or hidden-confounder relationship can pass or fail spuriously. Say so when relevant.\n"
        "- For PAIR: address BOTH directions. Bidirectional significance ⇒ feedback loop (e.g., rates ⇄ equities). Unidirectional ⇒ a true lead/lag candidate. Cite the best lag in each direction.\n"
        "- Confluence with CCF: if user has run CCF and that peak-lag aligns with the Granger best-lag at p<0.01, flag it as 'CCF-Granger confluence' — strong precedence + predictive content.\n"
        "- For SCAN: only call out drivers with xy_p_bonf < 0.05 as Bonferroni-significant. Anything above is suggestive, not robust to multiple-testing. Don't oversell raw p-values.\n"
        "- Cluster check: if multiple drivers from the SAME category (all rates, all credit) survive Bonferroni at similar lags, that's a category-level macro story — name it.\n"
        "- Feedback loops in scan: drivers where BOTH xy_p_bonf and yx_p_bonf are <0.05 indicate bidirectional dynamics. Flag the top one — a key trader insight.\n"
        "What to produce: (a) one-line summary of the strongest finding (direction, lag, p) with Bonferroni context for SCAN; (b) one-line caveat naming the linearity assumption — Tab 3 (Transfer Entropy) will test the nonlinear case; (c) for SCAN, the top 2-3 Bonferroni-significant drivers ranked, named with their best lag. End with a 'Bottom line:' that picks the one most-actionable lead/lag relationship."
    ),
    "causality-te": (
        "Causality → Tab 3 (Transfer Entropy) payload. The user is testing nonlinear, asymmetric information flow between two macro series. TE captures dependencies Granger's linear-VAR test misses (classic case: VIX → SPX during stress). Symbolic TE with rank-based discretization, history length l=1, permutation p-values.\n"
        "(A) PAIR mode payload: x{symbol, transform, adf_p}, y{symbol, transform, adf_p}, lookback, n, n_perm, x_to_y{te_bits, p_value, null_95th}, y_to_x{same}, net_te = TE(X→Y)−TE(Y→X), dominant (string label).\n"
        "(B) SCAN mode payload: target, lookback, n_perm, n_drivers_tested, target_meta, top_rows[] each with {driver, label, category, te_xy, p_xy, p_xy_bonf, te_yx, p_yx, p_yx_bonf, net_te, null_95th, transform}. Sort default: by te_xy (raw TE driver→target) descending.\n"
        "Interpretation rules:\n"
        "- TE units = bits per sample. Magnitudes are typically tiny (0.005-0.05) for daily macro data. What matters is the comparison to the permutation null and the asymmetry between directions.\n"
        "- A direction is 'significant' when te > null_95th AND p < 0.05. Both conditions: above noise empirically AND in the lower 5% of the null distribution.\n"
        "- The minimum resolvable p with N permutations is 1/(N+1). Default scan n_perm = 100 ⇒ min p ≈ 0.01. So Bonferroni at m=100 is too strict here — surface raw p with the null_95th comparison, not Bonferroni.\n"
        "- For PAIR: address Net TE first. Sign of Net TE = direction of dominant info flow. Magnitude tells you how asymmetric the relationship is. Then state both directions' significance.\n"
        "- Confluence with prior tabs: if Granger said 'X→Y strong' AND TE says 'X→Y above null AND p<0.05', that's strong confluence — robust under both linear and nonlinear assumptions. If TE catches a relationship Granger missed, flag it: 'nonlinear-only driver'.\n"
        "- For SCAN: top drivers by te_xy with p_xy < 0.05. Cluster by category (all rates, all credit) — that's a sector-level macro story, not just one ticker.\n"
        "- Symmetric drivers (similar TE both directions) are FEEDBACK LOOPS — name them as such. The trader-actionable ones are the ASYMMETRIC drivers.\n"
        "What to produce: (a) one-line summary of the dominant info-flow direction with significance language; (b) confluence-or-divergence note vs Granger findings if available; (c) for SCAN, the top 2-3 drivers above null_95th with their net_te values; end with a 'Bottom line:' that picks the cleanest nonlinear lead the data supports."
    ),
    "causality-var": (
        "Causality → Tab 4 (VAR + IRF) payload. The user fit a Vector Autoregression on a 2-8 series macro basket and is looking at orthogonalized impulse responses (IRF) and forecast error variance decomposition (FEVD). This tab gives MAGNITUDE and DECAY of shocks — what the previous three tabs couldn't.\n"
        "Payload: symbols (Cholesky order — most exogenous first), lookback, n, ic ('aic'|'bic'), selected_lag, best_aic_lag, best_bic_lag, irf_horizon, transforms (per-symbol stationarization), fevd_targets[] each with horizons[] of {horizon, contributions{symbol: share 0-1}}, shocks_summary[] each with origin and responses[] each with sparse [{h, v}] coefficients at h ∈ [0,1,2,5,10,horizon].\n"
        "Interpretation rules:\n"
        "- Cholesky ordering matters. Earlier-listed series can contemporaneously affect all later ones; later series can't shock earlier ones at h=0. Default ordering by category exogeneity — note any oddities (e.g., if the user put SPX before VIX, the IRF will look weird).\n"
        "- IRF: a shock to X is +1σ in X's residual. Read the response's SIGN (direction), MAGNITUDE (in stationarized units — log returns or first-diff), and DECAY (how fast it returns to zero). At h=0 only the shock origin and series ordered AFTER it in Cholesky show non-zero responses.\n"
        "- FEVD: at horizon h, what % of the target's forecast variance comes from each shock origin. Sums to 100%. The own-shock share at long horizons reveals how 'self-driven' the variable is.\n"
        "- Trader stories to look for: (1) 'X explains Y% of Z's variance at 20d' — the headline driver. (2) IRF that peaks at h>0 = LAGGED transmission, the trader's lead/lag setup. (3) IRF that decays slowly = persistent shocks, longer holding periods justified. (4) IRF flips sign across horizons = overshooting/correction dynamics.\n"
        "- Lag selection: AIC tends to pick richer models, BIC parsimonious. Honest rule: use BIC unless AIC is materially better and you have lots of data. If AIC=BIC selected lag, that's robustness.\n"
        "- Confluence: if Granger said 'X→Y at lag k', the IRF response of Y to X-shock should peak around h=k. If they disagree, name it.\n"
        "What to produce: (a) one-sentence read of the FEVD for the most-endogenous series (last in Cholesky order) — name the top 2 contributors with their %. (b) the most striking IRF result — pick a shock-response pair where magnitude × persistence is largest, name origin → response with magnitude at peak h. (c) tradeable implication: which shock would I most want to position around given this VAR? End with a 'Bottom line:' that names the cleanest variance-decomposition trade idea."
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


def _interpret_cache_key(page: str, data: dict, subject: str | None,
                         system_text: str | None = None) -> str:
    """Deterministic hash over the inputs AND the prompt text. Including the
    system prompt + PAGE_CONTEXT means any prompt edit automatically invalidates
    cached answers for the affected page — no manual cache-wipe needed. Old
    entries sit orphaned until their 24h TTL expires.

    `system_text` is passed explicitly rather than read from BASE_SYSTEM so that
    a promoted home-page prompt busts its own cache. Hashing the baseline while
    serving a challenger would have re-served the OLD answer under the NEW
    prompt's name for a full day — which would show up as a mysteriously
    unchanged score right after a promotion."""
    ctx = PAGE_CONTEXT.get(page, "")
    payload = json.dumps(
        {"page": page, "system": system_text or BASE_SYSTEM, "ctx": ctx, "data": data, "subject": subject},
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
    # The home page is the surface the prompt loop measures and edits; every
    # other page stays on the git baseline until a version has earned its way
    # past evidence gathered on that page's own traffic.
    if body.page == "home_page":
        from src.prompt_registry import active as _active_prompt
        system_text, interp_version = _active_prompt("home_interpret")
    else:
        system_text, interp_version = BASE_SYSTEM, 0

    cache_key = _interpret_cache_key(body.page, body.data, body.subject, system_text)
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
        msg = client.beta.messages.create(
            model=MODEL,
            # Opus 5 thinks by default and thinking counts against max_tokens,
            # so this budget covers reasoning + prose — not the ~700 tokens of
            # interpretation we actually want back. Effort caps the reasoning
            # spend; this endpoint is high-volume and user-facing.
            max_tokens=4000,
            output_config={"effort": "medium"},
            # Safety classifiers can decline; "default" re-serves the request on
            # Anthropic's recommended model for the refusal category, so there's
            # no fallback model list to keep up to date here.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[
                {
                    "type": "text",
                    "text": system_text,
                    # Prompt cache — system prompt is reused across every
                    # interpretation request, so cache hits cut input cost ~75%.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        # Opus 5 and whatever model the fallback routes to can both decline.
        # That returns a 200 with stop_reason="refusal" and no text blocks —
        # without this the endpoint would serve an empty interpretation.
        if msg.stop_reason == "refusal":
            category = getattr(getattr(msg, "stop_details", None), "category", None)
            logger.warning(f"Claude declined interpret page={body.page} category={category}")
            raise HTTPException(502, "Claude declined to interpret this data.")

        text_blocks = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
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
        # MEASURE EVERY PAGE, EDIT ONLY WHERE THERE IS EVIDENCE. The home page is
        # the surface the loop versions and rewrites, but the same prompt writes
        # an interpretation on twenty-odd other pages and none of them were
        # recorded — so a defect there had no way to surface except by someone
        # reading it. Every page now snapshots under `interpret:<page>` and is
        # graded by the same rules; only `home_interpret` has versions and a
        # critic, because that is the only page with a record to argue from.
        try:
            from src import prompt_snapshots
            surface = "home_interpret" if body.page == "home_page" else f"interpret:{body.page}"
            prompt_snapshots.record(
                surface, body.data, interpretation,
                prompt_version=interp_version, model=MODEL,
                meta={"page": body.page, "subject": body.subject or "",
                      "output_tokens": result.get("output_tokens")},
            )
        except Exception as e:
            logger.debug(f"interpret snapshot skipped: {e}")

        return {**result, "cache_hit": False}
    except HTTPException:
        raise
    except anthropic.BadRequestError as e:
        logger.warning(f"Claude rejected interpret request: {e}")
        raise HTTPException(400, f"Claude rejected the request: {e}")
    except anthropic.APIError as e:
        logger.warning(f"Claude API error: {e}")
        raise HTTPException(502, f"Claude API error: {e}")
    except Exception as e:
        logger.warning(f"interpret failed: {e}")
        raise HTTPException(500, f"Interpretation failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
# /chat — ask questions about the home page
# ══════════════════════════════════════════════════════════════════════════
#
# WHY THIS IS NOT THE INTERPRET ENDPOINT WITH A HISTORY ARRAY. Three things
# differ, and each one is a correctness issue rather than a preference:
#
# 1. NO ANSWER CACHE. `/interpret` is keyed on (page, data, prompt) because the
#    same page produces the same interpretation for every user. A chat answer
#    depends on the question and the turns before it, so the same cache would
#    serve one user's answer to another's question. Prompt caching (input) is
#    still used, and that is where the saving actually is.
# 2. THE SNAPSHOT IS FROZEN BY THE CALLER, not re-read per turn. If the page
#    refreshed between turns, turn 3 would answer off different numbers than
#    turn 1 and the conversation would quietly contradict itself — and the
#    cached prefix would invalidate every time, so it would cost more as well.
#    The client sends the same snapshot for the life of a conversation and its
#    `as_of` travels with it.
# 3. A DIFFERENT PROMPT. The panel's failure mode is surveying; a chat's is
#    answering anyway. See `HOME_CHAT_SYSTEM`.
# SIZED FOR A SINGLE OPERATOR, deliberately. An earlier pass tuned these for a
# multi-tenant worst case that does not exist yet — the site has one user. The
# binding constraint here is answer quality, not aggregate spend, and every
# number below is cheap to walk back if that changes.
_CHAT_MAX_TURNS = 40          # user+assistant messages retained, newest kept
_CHAT_MAX_QUESTION = 8000     # characters — room to paste a table or a log
# MEASURED, not guessed. The ES brief ALONE serialises to 65,548 characters
# indented and 43,056 compact, so the first version of this — a 60,000 cap with
# indent=2 — truncated on EVERY request, and truncation cuts the TAIL: every
# block after the brief (driver, vol, sectors, calendar, macro, valuation) was
# silently dropped from every conversation. The chat would have claimed to
# answer about a page it could only half see.
#
# Compact separators cost nothing and save 34% — the model does not need
# pretty-printed JSON. The cap is then sized to the real payload rather than to
# a round number: ~120k characters is roughly 30k tokens, well inside the 1M
# window, ~$0.15 on a first turn and about a tenth of that once cached.
#
# RAISED to 400k. The cap only decides when blocks get DROPPED — cost tracks the
# bytes actually sent, not the ceiling — so a high cap is free unless the payload
# really grows, and dropping a block is the outcome worth avoiding. At today's
# ~43k the limit never binds; at 400k it would not bind even if the page tripled.
_CHAT_MAX_SNAPSHOT = 400000   # characters of COMPACT JSON (~100k tokens)
# Kept whole when the payload must be cut, in this order. Anything not named
# here ranks below everything that is.
_CHAT_BLOCK_PRIORITY = (
    "as_of", "es_brief", "market_driver", "vol_landscape", "sectors",
    "calendar", "macro_pressure", "cta_flows", "sector_rrg", "sp_valuation",
)


def _fit_snapshot(data: dict) -> tuple[str, list[str]]:
    """Serialise the snapshot, dropping WHOLE blocks if it will not fit.

    Cutting a JSON string at a byte offset hands the model malformed JSON and
    an unmarked absence — the failure this platform cares most about. Dropping
    named blocks and telling the model which ones went is strictly better: it
    can then say "that block is not in the snapshot" instead of guessing at a
    half-parsed object.
    """
    def dump(d: dict) -> str:
        return json.dumps(d, default=str, sort_keys=True, separators=(",", ":"))

    out = dump(data)
    if len(out) <= _CHAT_MAX_SNAPSHOT:
        return out, []

    rank = {k: i for i, k in enumerate(_CHAT_BLOCK_PRIORITY)}
    # Lowest priority first; unknown keys rank after every known one.
    order = sorted(data.keys(), key=lambda k: (-rank.get(k, len(rank)), str(k)))
    kept, dropped = dict(data), []
    for key in order:
        if len(out) <= _CHAT_MAX_SNAPSHOT or key in ("as_of", "es_brief"):
            break
        kept.pop(key, None)
        dropped.append(str(key))
        out = dump(kept)
    # Last resort: the brief alone still overflows. Cut, and say so loudly.
    return out[:_CHAT_MAX_SNAPSHOT], dropped


class ChatTurn(BaseModel):
    role: str                 # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    data: dict                # the frozen page snapshot
    question: str
    history: list[ChatTurn] = []


@router.post("/chat")
# Keyed per user, not per IP (see api/rate_limit._key_fn). Generous because the
# site has one operator: a new conversation is ~11k input tokens at today's
# payload (~$0.06) and follow-ups read that back from cache. This is a runaway
# guard, not a budget.
@limiter.limit("40/minute;1000/day")
async def chat(
    request: Request,
    body: ChatRequest,
    user: str = Depends(get_current_user),
):
    """Answer a question about the current home-page snapshot."""
    if user == "anonymous":
        raise HTTPException(401, "Sign in required")

    question = (body.question or "").strip()
    if not question:
        raise HTTPException(400, "Empty question")
    if len(question) > _CHAT_MAX_QUESTION:
        raise HTTPException(400, f"Question too long (max {_CHAT_MAX_QUESTION} characters)")

    api_key = get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(503, "Chat unavailable — ANTHROPIC_API_KEY not configured")

    from src.prompt_defaults import HOME_CHAT_SYSTEM
    ctx = PAGE_CONTEXT.get("home_page", "")
    snapshot, dropped = _fit_snapshot(body.data or {})
    truncated = bool(dropped)

    # ORDER MATTERS FOR THE CACHE. Rendering is tools -> system -> messages, and
    # a prefix match means any byte change invalidates everything after it. The
    # two stable things — the prompt and the frozen snapshot — go first and
    # carry the breakpoints; the volatile turns follow. `sort_keys=True` above
    # is part of that: an unsorted dict re-serialises in a different order and
    # silently misses the cache on every turn.
    context_turn = (
        f"Here is the home page the trader is looking at.\n\n"
        f"What the blocks mean:\n{ctx}\n\n"
        f"Page snapshot:\n```json\n{snapshot}\n```"
        + (f"\n\nNOT INCLUDED, because the snapshot did not fit: "
           f"{', '.join(dropped)}. If the trader asks about any of those, say "
           f"that block is not in this snapshot — do not infer it."
           if dropped else "")
    )

    messages: list[dict] = [
        {"role": "user", "content": [{"type": "text", "text": context_turn,
                                      "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": "Understood. Ask me about it."},
    ]
    # ROLES MUST ALTERNATE and the history follows a synthetic ASSISTANT turn,
    # so a history that begins with an assistant message produces two in a row
    # and a 400 from the API. The client always sends complete pairs, but
    # `[-12:]` on an odd-length history would still slice into the middle of
    # one — an invariant held by the caller is not an invariant.
    hist = [t for t in body.history
            if t.role in ("user", "assistant") and (t.content or "").strip()]
    hist = hist[-_CHAT_MAX_TURNS:]
    while hist and hist[0].role == "assistant":
        hist.pop(0)
    prev = "assistant"
    for t in hist:
        if t.role == prev:          # drop a repeat rather than send a 400
            continue
        messages.append({"role": t.role, "content": t.content[:_CHAT_MAX_QUESTION]})
        prev = t.role
    if prev == "user":              # never two user turns in a row
        messages.append({"role": "assistant", "content": "…"})
    messages.append({"role": "user", "content": question})

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.beta.messages.create(
            model=MODEL,
            # Opus 5 thinks by default and thinking counts against max_tokens,
            # so this is reasoning plus a short answer, not the answer alone.
            max_tokens=16000,
            # "high", not "medium". The medium answers in testing were already
            # good, and the standing preference on this platform is accuracy
            # over speed — a chat that reasons harder about which block answers
            # the question is worth a couple of seconds here.
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{"type": "text", "text": HOME_CHAT_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        if msg.stop_reason == "refusal":
            category = getattr(getattr(msg, "stop_details", None), "category", None)
            logger.warning(f"Claude declined home chat category={category}")
            raise HTTPException(502, "Claude declined to answer that.")

        answer = "\n".join(b.text for b in msg.content
                           if getattr(b, "type", None) == "text").strip()
        if not answer:
            raise HTTPException(502, "Empty answer from the model.")

        return {
            "ok": True,
            "model": MODEL,
            "answer": answer,
            # Same grounding check the interpretation panel runs. A chat can
            # invent a number as easily as a panel can, and this is the only
            # automated thing standing between the two.
            "grounding": _check_grounding(answer, body.data),
            "snapshot_truncated": truncated,
            "cache_read_tokens": getattr(msg.usage, "cache_read_input_tokens", 0),
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        }
    except HTTPException:
        raise
    except anthropic.BadRequestError as e:
        logger.warning(f"Claude rejected chat request: {e}")
        raise HTTPException(400, f"Claude rejected the request: {e}")
    except anthropic.RateLimitError as e:
        logger.warning(f"Claude rate limited chat: {e}")
        raise HTTPException(429, "Rate limited — try again shortly.")
    except anthropic.APIError as e:
        logger.warning(f"Claude API error on chat: {e}")
        raise HTTPException(502, f"Claude API error: {e}")
