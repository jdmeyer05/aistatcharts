"""What the cross-asset vol scan implies for the ES session.

The landscape card answers "where is vol rich and where is fear concentrated"
across twenty ETFs. It does not answer the question the trader reading it
actually has, which is what any of that means for the index they trade. This
turns the scan into a session read.

DELIBERATELY NOT A SECOND EXPECTED MOVE. `es_expected_move` already prices the
session range off the SPX chain, and a second estimate from SPY's ATM IV would
sit beside it disagreeing in the third decimal for no gain. What this scan knows
that nothing else on the page does is the CROSS-ASSET picture: whether the index
will travel or its parts will cancel, whether equity vol is rich against its
peers, and whether the near-term event premium is in SPY itself or somewhere
else entirely.

Context, not signals — see the note on the regime line for why the playbook
string above it is a different kind of claim.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Implied correlation from the dispersion formula: index variance against the
# average component variance. High means sectors move together and the index
# travels with them; low means they offset and the index chops while the parts
# are busy. Cut from what the measure actually does, not from round numbers.
_CORR_HIGH = 0.55
_CORR_LOW = 0.35

# Put skew as a ratio of downside to upside IV. Below ~1.05 the smile is close
# to symmetric; above ~1.25 the downside is being paid for in size.
_SKEW_STEEP = 1.25
_SKEW_FLAT = 1.05

_IVHV_RICH = 1.15
_IVHV_CHEAP = 0.90


def _row(metrics: list[dict], ticker: str) -> dict | None:
    for r in metrics or []:
        if r.get("Ticker") == ticker:
            return r
    return None


def es_vol_read(metrics: list[dict], impl_corr: float | None,
                summary: dict | None = None) -> dict | None:
    """Translate the cross-asset scan into what it says about the ES session.

    Returns None when SPY is missing — every read here is anchored on it, and a
    cross-asset picture with no equity leg has nothing to say about ES.
    """
    spy = _row(metrics, "SPY")
    if not spy:
        return None
    summary = summary or {}
    reads: list[dict] = []

    # 1. Will the index actually move? This is the read nothing else on the
    #    page provides. Sector vol can be high while the index sits still,
    #    because the moves cancel — that is a chop session with busy internals,
    #    and it looks identical to a quiet one from the ES chart alone.
    if impl_corr is not None:
        if impl_corr >= _CORR_HIGH:
            verdict = ("sectors are priced to move together — index moves should carry, "
                       "and a break is less likely to be absorbed by rotation")
        elif impl_corr <= _CORR_LOW:
            verdict = ("sectors are priced to move independently — their moves tend to "
                       "cancel at the index, so ES can chop while single names run")
        else:
            verdict = "sectors priced neither unusually together nor apart"
        reads.append({
            "label": "Index vs its parts",
            "value": f"implied correlation {impl_corr:.2f}",
            "note": verdict,
        })

    # 2. Is the near-term event premium in SPY, or somewhere else? The headline
    #    count of inverted names cannot answer this, and the difference is the
    #    whole point: XLE inverted is an energy story, SPY inverted is an ES one.
    ts = spy.get("TS_Slope")
    n_inv = summary.get("n_inverted")
    if ts is not None:
        spy_inverted = ts < 0
        if spy_inverted:
            note = ("front-month vol above back — the event premium is in the index "
                    "itself, so near-dated risk is ES risk")
        elif n_inv:
            note = (f"SPY's curve is normal while {n_inv} of {summary.get('n_tickers', '?')} "
                    "are inverted — the near-term fear is in those assets, not the index")
        else:
            note = "front-month vol below back — no near-term event premium in the index"
        reads.append({
            "label": "Where the near-term fear sits",
            "value": f"SPY term structure {'inverted' if spy_inverted else 'normal'}",
            "note": note,
        })

    # 3. Is credit more frightened than equity? HYG leading SPY on downside skew
    #    is the classic tell, and it is visible on this card already — as a
    #    pairwise row that never says which of the two is the index.
    hyg = _row(metrics, "HYG")
    s_skew, h_skew = spy.get("Put_Skew"), (hyg or {}).get("Put_Skew")
    if s_skew and h_skew:
        if h_skew > s_skew * 1.15:
            note = ("credit is pricing more downside than equity — historically a warning "
                    "that reads early rather than a confirmation")
        elif s_skew > h_skew * 1.15:
            note = "equity is pricing more downside than credit — the fear is equity-specific"
        else:
            note = "credit and equity agree on downside pricing"
        reads.append({
            "label": "Credit vs equity",
            "value": f"HYG skew {h_skew:.2f}x vs SPY {s_skew:.2f}x",
            "note": note,
        })

    # 4. Symmetry. Steep skew means a down move is expected to travel faster
    #    than an up move of the same provocation — which is a stop-placement
    #    fact, not a directional one.
    if s_skew:
        if s_skew >= _SKEW_STEEP:
            note = ("downside is bid — expect a decline to travel faster than a rally of "
                    "the same size, and size stops accordingly")
        elif s_skew <= _SKEW_FLAT:
            note = "smile is near symmetric — no premium being paid for downside"
        else:
            note = "ordinary downside premium"
        reads.append({"label": "Symmetry", "value": f"SPY put skew {s_skew:.2f}x", "note": note})

    # 5. Is the option market paying up relative to what has actually happened?
    #    Directly comparable to the realised range on the levels card.
    ivhv = spy.get("IV_HV")
    if ivhv:
        if ivhv >= _IVHV_RICH:
            note = ("options are pricing more movement than ES has recently delivered — "
                    "the expected range is an ask, not a forecast")
        elif ivhv <= _IVHV_CHEAP:
            note = ("options are pricing less movement than ES has recently delivered — "
                    "recent range beats the implied one")
        else:
            note = "implied and realised are in line"
        reads.append({
            "label": "Priced vs delivered",
            "value": f"SPY IV/HV {ivhv:.2f}x",
            "note": note,
        })

    if not reads:
        return None

    return {
        "available": True,
        "anchor": "SPY",
        "spy": {
            "front_iv_pct": round(spy["Front_IV"] * 100, 2) if spy.get("Front_IV") else None,
            "iv_hv": round(ivhv, 3) if ivhv else None,
            "put_skew": round(s_skew, 3) if s_skew else None,
            "ts_slope": round(ts, 4) if ts is not None else None,
            "iv_percentile": spy.get("IV_Pctile"),
        },
        "reads": reads,
        # The regime line above this card names a playbook ("sell front, buy
        # back"). These do not, on purpose: they describe the session ES is
        # likely to have, and what to expect is a different claim from what to do.
        "note": ("Read as session context. These say what kind of session the vol market "
                 "is priced for — how far ES should travel, which direction moves faster, "
                 "and whether the index will follow its parts — not what to put on."),
    }
