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
# component cross terms. High means sectors move together and the index travels
# with them; low means they offset and the index chops while the parts are busy.
#
# THESE CUTS ARE NOT VALIDATED. They were first set at 0.55/0.35 while the scan
# fed SPY's 1-DTE IV into a formula built from everyone else's monthlies, which
# pinned the reading near 0.10 — so they were fitted to a number that was wrong,
# under a comment claiming they were not. With the tenor bug and the Jensen bug
# both fixed the measure prints about 0.32, which the old 0.35 cut would still
# have called "independent".
#
# Widened to where the label is defensible rather than where it told a story.
# Anything between reports as middling, and the raw index-vs-sector vol gap is
# printed alongside so the arithmetic stays checkable without trusting the cut
# at all. Validating these needs a stored history of the measure — see the
# note in the correlation read.
_CORR_HIGH = 0.60
_CORR_LOW = 0.25

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
    # Front_IV is a fraction here; avg_sector_iv already arrives as percent.
    spy_iv = spy.get("Front_IV")
    spy_iv = spy_iv * 100 if spy_iv else None
    sec_iv = summary.get("avg_sector_iv")

    # The vol GAP leads, not the correlation. The gap is arithmetic the reader
    # can redo from two numbers on the card. The correlation is the same
    # information pushed through a formula that needs index weights nobody here
    # has: equal-weighting the eleven sectors prints ~0.32 on a chain where
    # approximate cap weights print ~0.08, because XLK is a third of SPX and
    # carries the highest IV in the group. A figure that swings fourfold on an
    # invisible assumption cannot be the headline on a card a human has to act
    # from, so it is reported second and labelled for what it is.
    if spy_iv and sec_iv:
        spread = sec_iv - spy_iv
        if spread > 0:
            mech = ("the index is calmer than the average of its parts, which is what "
                    "offsetting sector moves look like when they are priced in — it is "
                    "the normal state, and only the size of the gap carries information")
        else:
            mech = ("the index is priced at or above the average of its parts, which is "
                    "unusual — it takes sectors expected to move together, or an index-"
                    "level event the sectors are not individually exposed to")
        reads.append({
            "label": "Index vs its parts",
            "value": f"SPY {spy_iv:.1f}% vs sectors {sec_iv:.1f}%",
            "note": f"{spread:+.1f} vol points; {mech}.",
        })

    if impl_corr is not None:
        if impl_corr >= _CORR_HIGH:
            verdict = ("sectors priced to move together — index moves should carry, and a "
                       "break is less likely to be absorbed by rotation")
        elif impl_corr <= _CORR_LOW:
            verdict = ("sectors priced to move independently — moves tend to cancel at the "
                       "index, so ES can chop while single names run")
        else:
            verdict = ("neither unusually together nor apart — the middle of the range, "
                       "which says little on its own about whether ES travels")
        reads.append({
            "label": "Same gap, as a correlation",
            "value": f"~{impl_corr:.2f} equal-weighted",
            "note": verdict + ".",
            "caveat": ("Equal-weighted across the 11 sectors because live SPX index weights "
                       "need a constituent feed this project does not carry. Cap-weighting "
                       "this same chain gives roughly 0.08 against 0.32 here, so treat the "
                       "level as indicative only — the vol gap above is the number to trust, "
                       "and the thresholds behind this verdict are not yet validated."),
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
