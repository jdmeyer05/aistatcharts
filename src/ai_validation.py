"""AI Output Accuracy Validation — shared instructions appended to all AI prompts.

Every AI call on the platform should append ACCURACY_CHECK to the end of its
prompt (system or user, whichever is last). This ensures all models self-verify
before returning output.

Usage:
    from src.ai_validation import ACCURACY_CHECK, wrap_prompt

    # Option 1: Append directly
    prompt = f"{your_prompt}\n\n{ACCURACY_CHECK}"

    # Option 2: Use wrapper (for system + user prompt pairs)
    system, user = wrap_prompt(system_prompt, user_prompt)
"""

ACCURACY_CHECK = """
BEFORE YOU RESPOND — MANDATORY SELF-VALIDATION:

1. NUMERICAL ACCURACY: Review every number in your response (prices, percentages,
   ratios, scores, probabilities). Cross-check each against the data provided.
   If a number cannot be derived from the data, flag it as an estimate.

2. INTERNAL CONSISTENCY: Verify that your scores match your recommendation,
   your price targets match your thesis, your probabilities sum correctly,
   and your risk/reward descriptions are mathematically consistent with
   the P&L numbers you quote.

3. STALE DATA CHECK: If any data point you reference seems outdated or
   contradicts other data provided (e.g., a price target that is below
   the current price for a "Buy" recommendation), correct it before responding.

4. HALLUCINATION GUARD: Do not invent specific prices, dates, analyst names,
   news events, or statistics that are not present in the data provided to you.
   If you need to estimate (e.g., option prices), clearly label them as estimates
   with a tilde (~) prefix.

5. FINAL PASS: After drafting your full response, re-read it once. Fix any
   contradictions, missing sections, or truncated output before returning.
"""

# Shorter version for lightweight calls (tweet scoring, chat, etc.)
ACCURACY_CHECK_LIGHT = """
Before responding: verify all numbers match the provided data. Do not invent
facts, prices, or events not present in the input. Label estimates with ~.
"""


# ── Institutional Vol Surface Analysis Framework ──
# Distilled from professional quant manual. Injected into vol surface AI prompts
# so models analyze surfaces with institutional rigor, not generic options knowledge.

VOL_SURFACE_EXPERT_CONTEXT = """
INSTITUTIONAL VOLATILITY SURFACE ANALYSIS FRAMEWORK:

You must analyze this surface using the following professional frameworks. Do not
use generic options commentary. Reference specific data points from the surface.

1. SKEW INTERPRETATION (the "smirk"):
   - Equity skew exists because institutions systematically buy OTM puts for crash
     protection and sell OTM calls (covered call overwriting). This is structural, not anomalous.
   - STEEP skew (25D put IV >> ATM): Market is paying heavy fear premium. Quantify how
     much above historical norms. If skew is at >75th percentile, it is rich — consider
     selling the wing via put spreads or risk reversals. If <25th percentile, skew is
     complacent — cheap to buy crash protection.
   - FLAT skew: Dangerous complacency. Historically precedes sharp selloffs because
     downside protection is being underpriced.
   - Butterfly metric (25D_put_IV + 25D_call_IV - 2*ATM_IV): Measures tail risk pricing.
     Positive = fat tails priced in. Negative = thin tails, market underpricing extremes.
   - Risk Reversal (25D_call_IV - 25D_put_IV): Negative = normal equity skew (puts richer).
     Positive = unusual call demand (squeeze risk, speculative fervor).

2. TERM STRUCTURE INTERPRETATION:
   - CONTANGO (front < back): Normal. Compensates sellers for long-horizon uncertainty.
     Front-month credit spreads are structurally safe. LEAPS are expensive.
   - BACKWARDATION (front > back): Immediate distress or imminent catalyst. The market is
     bracing for a near-term shock. Quantify the inversion spread. Calendar spreads (sell
     front, buy back) exploit this — but ONLY if you believe the event will underdeliver.
   - EVENT HUMPS: Localized IV spikes at specific expirations = binary event pricing
     (earnings, FOMC, ex-div). Options expiring BEFORE the event have baseline vol;
     options spanning the event carry a discrete jump premium.
   - IV CRUSH WARNING: After the catalyst passes, the event hump collapses violently.
     Never buy naked long options into an event hump unless you are explicitly trading
     the event magnitude. The IV crush can destroy 30-50% of premium overnight.

3. VARIANCE RISK PREMIUM (VRP = IV - HV):
   - This is the CORE edge metric. VRP > 0 means options are overpriced vs realized —
     premium sellers have the edge. VRP < 0 means options are cheap — gamma buyers win.
   - IV/HV Ratio: <1.0 = cheap gamma (buy vol). >1.3 = expensive gamma (sell vol).
     Between 1.0-1.3 = fair, look elsewhere for edge.
   - Reference the VRP percentile rank (252-day) to contextualize: is current VRP
     historically extreme or normal?

4. GAMMA SCALPING ECONOMICS:
   - Break-even daily move = sqrt(2 * |theta| / gamma). If the stock routinely moves
     more than this, long gamma is profitable. If not, short gamma wins.
   - Gamma/Theta ratio > 1.0: scalp-profitable if realized vol exceeds implied.
   - Dollar gamma tells you how much P&L per 1% move. Dollar theta tells you the daily
     bleed. The ratio determines if owning the straddle is worth it.

5. SURFACE DISLOCATIONS:
   - Compare each contract's IV to its expiration's ATM IV baseline. Contracts trading
     significantly above baseline are "rich" — candidates to sell. Below baseline = "cheap"
     — candidates to buy.
   - CRITICAL: Only consider dislocations on contracts with real open interest (OI > 0).
     Zero-OI contracts have stale, unreliable IV from wide bid-ask spreads.
   - The best trades exploit dislocations: sell the richest contracts, buy the cheapest,
     at the same expiration. This is pure relative value with no directional risk.

6. REGIME IDENTIFICATION:
   - STICKY STRIKE regime (range-bound markets): IV stays constant at fixed strikes.
     Standard delta hedging works. Gamma is predictable.
   - STICKY DELTA regime (trending/panic markets): The entire skew curve shifts with spot.
     A down-move raises ATM vol because the high-IV left tail slides into the ATM position.
     Requires "shadow delta" adjustment. Vanna exposure becomes critical.
   - Failing to identify the current regime causes systematic hedging errors.

7. COMMON PROFESSIONAL TRAPS TO AVOID:
   - High IV ≠ directional forecast. It measures expected MAGNITUDE, not direction.
   - Never sell vol into earnings unless explicitly structured as an earnings play.
   - Skew flattening during low-vol regimes is a WARNING, not an all-clear.
   - A "cheap" OTM put by absolute premium may be extremely expensive by IV if skew is steep.
   - Calendar spreads in backwardation = the "inversion trap". The front leg's IV crush
     destroys the spread faster than the back leg gains value.
"""


# ── Higher-Order Greeks Expert Context ──
# Distilled from institutional derivatives risk management manual.
# Injected into Higher Greeks AI prompts for market microstructure awareness.

HIGHER_GREEKS_EXPERT_CONTEXT = """
INSTITUTIONAL HIGHER-ORDER GREEKS FRAMEWORK:

DEALER POSITIONING AND HEDGING FLOWS:
- Options dealers hedge continuously. Their aggregate Greek exposure creates predictable
  market flows that MOVE the underlying price independent of fundamentals.
- SHORT GAMMA regime (dealers net short options): Dealers must buy into strength and
  sell into weakness = AMPLIFIES momentum. Creates "gamma squeezes."
- LONG GAMMA regime (dealers net long options): Dealers buy weakness and sell strength
  = DAMPENS volatility. Acts as a liquidity buffer.
- The NET GAMMA EXPOSURE (GEX) at each strike determines support/resistance levels.

VANNA FLOWS (the hidden force):
- Before events (FOMC, CPI, earnings): institutions buy OTM puts, giving dealers
  positive vanna exposure. Rising IV forces dealers to short futures to hedge.
- After events: IV crush causes put deltas to collapse toward zero. Dealers must
  aggressively BUY BACK their short futures = the "Post-FOMC Squeeze."
- This is entirely mechanical, not fundamental. It happens every time.

CHARM FLOWS (the time force):
- If OTM puts are held through the day without the underlying crashing, charm causes
  put deltas to bleed toward zero. Dealers unwind short hedges = buying pressure.
- This creates the "Afternoon Melt-up" — persistent buying into the close driven
  purely by options time decay mechanics, not news.
- Weekend charm: accelerated decay Friday afternoon forces pre-weekend hedge adjustments.

0DTE DYNAMICS:
- 0DTE options have EXTREME gamma, charm, and speed profiles.
- A small intraday move causes violent delta repricing, forcing massive dealer flows.
- 0DTE gamma exhaustion defines intraday support/resistance, not price levels.

GAMMA SCALPING OPTIMIZATION:
- Speed tells you the EXACT tick-intervals to scalp profitably before transaction costs erode margin.
- Zomma tells you how your gamma profile shifts during a vol shock — critical for
  adjusting scalp volume dynamically. Without Zomma, you're flying blind.
- Break-even move = sqrt(2 * |theta| / gamma). If realized move > break-even, long gamma wins.

VANNA-VOLGA PRICING (institutional smile pricing):
- Three instruments hedge the smile: ATM straddle (vega), risk reversal (vanna), butterfly (volga).
- VV price = BS price + vanna cost + volga cost. The difference is the "smile premium."
- Options trading BELOW VV fair value are cheap (buy). ABOVE = rich (sell).
- For barrier/exotic options, multiply vanna/volga costs by no-touch probability.

RISK MANAGEMENT HIERARCHY:
- Delta-Gamma-Vega (DGV) approximation dangerously truncates the Taylor series.
- Ignoring vanna and volga understates convexity risk by an ORDER OF MAGNITUDE.
- A "vomma bomb" = short strangle book where a vol spike causes vega to multiply
  exponentially, triggering forced liquidation at worst prices.
- Speed + Color near expiration = "pin risk." Gamma concentrates into a narrow peak,
  forcing extreme-frequency hedge adjustments.
"""


def wrap_prompt(system_prompt: str, user_prompt: str, light: bool = False) -> tuple[str, str]:
    """Append accuracy check to the user prompt (keeps system prompt clean for caching).

    Returns (system_prompt, user_prompt_with_validation).
    """
    check = ACCURACY_CHECK_LIGHT if light else ACCURACY_CHECK
    return system_prompt, f"{user_prompt}\n\n{check}"
