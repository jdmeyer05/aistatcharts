"""Generate `src/event_impact_tables.json` from the market-movers study.

WHY A GENERATED TABLE AND NOT A HAND-TYPED ONE. The calendar's `impact` field
is three words assigned by judgement. Every number this script emits was
measured in `research/market_movers/` over 3,677 sessions, and the whole point
of replacing the words is that the replacement must not itself be an opinion.
Transcribing 23 rows by hand is how a measured figure drifts into an assigned
one — the calendar's own Nonfarm-payrolls note said "~1.1-1.2x" while the study
that produced it says 1.39x. So the table is generated, the generator is
committed, and re-running it is the only supported way to change a number.

WHY A JSON FILE AND NOT A RUNTIME READ OF study.json. `study.json` is 382KB of
per-year detail the API never needs, and it lives under `research/`, which is
not part of the served surface. Same pattern as `candle_context_tables.json`.

Run:  py -3.14 scripts/gen_event_impact.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDY = os.path.join(HERE, "research", "market_movers", "output", "study.json")
OUT = os.path.join(HERE, "src", "event_impact_tables.json")

# Calendar display name -> study event name.
#
# `exact=False` means the calendar event is a SUBSET of what was measured and
# the study never separated it out. The distinction is carried through to the
# UI rather than dropped, because quoting a parent event's multiplier on a
# child is exactly the kind of quiet substitution this table exists to stop.
NAME_MAP: dict[str, tuple[str, bool]] = {
    "CPI": ("CPI", True),
    "Nonfarm payrolls": ("Nonfarm payrolls", True),
    "PCE / personal income": ("PCE price index", True),
    "PPI": ("PPI", True),
    "GDP": ("GDP", True),
    "Initial jobless claims": ("Initial jobless claims", True),
    "Retail sales": ("Retail sales", True),
    "JOLTS job openings": ("JOLTS job openings", True),
    "Industrial production": ("Industrial production", True),
    "Housing starts": ("Housing starts", True),
    "Trade balance": ("Trade balance", True),
    "Empire State manufacturing": ("Empire State manufacturing", True),
    "Philly Fed manufacturing": ("Philly Fed manufacturing", True),
    "ISM manufacturing PMI": ("ISM manufacturing", True),
    "ISM services PMI": ("ISM services", True),
    "U-Mich sentiment (final)": ("U. Michigan sentiment (final)", True),
    "Quad witching (OpEx)": ("Triple witching", True),
    "FOMC decision": ("FOMC decision", True),
    # The study pooled all 123 decisions; it never split the eight SEP meetings
    # out, so this is the parent's number quoted on a child.
    "FOMC decision + SEP/dot plot": ("FOMC decision", False),
}

# Calendar events with no measurement at all. Listed explicitly so that a NEW
# calendar event added later shows up as an unmapped name in the report below
# rather than silently rendering as "not measured" forever.
KNOWN_UNMEASURED = {
    "U-Mich sentiment (prelim)",
    "Consumer confidence",
    "EIA petroleum status",
}


def main() -> None:
    with open(STUDY, encoding="utf-8") as f:
        study = json.load(f)

    ranking = study["events"]["ranking"]
    stability = {s["event"]: s for s in study["events"]["stability"]}
    by_event = {r["event"]: r for r in ranking}
    n_events = len(ranking)

    # The quiet-day baseline is one number for the whole study, carried once
    # rather than repeated on 23 rows.
    quiet = ranking[0].get("quiet_day_median") if ranking else None

    rows: dict[str, dict] = {}
    for cal_name, (study_name, exact) in NAME_MAP.items():
        r = by_event.get(study_name)
        if r is None:
            raise SystemExit(
                f"'{cal_name}' maps to '{study_name}', which is not in the study. "
                "Fix NAME_MAP rather than dropping the row."
            )
        st = stability.get(study_name, {})
        rows[cal_name] = {
            "study_event": study_name,
            "exact": exact,
            "multiplier": round(r["rel_abs_median"], 3),
            "ci95": [round(r["rel_abs_ci95"][0], 3), round(r["rel_abs_ci95"][1], 3)],
            "n": r["n"],
            "p": round(r["p_rotation"], 4),
            "survives_fdr": bool(r["survives_fdr"]),
            "rank": r["rank"],
            # Next-session carry. Every one of these is near 1.0, which is the
            # finding: nothing persists past the print.
            "next_session": round(r["persistence"]["t+1"], 3),
            "share_over_1_5x": round(r["share_over_1_5x"], 3),
            # How much the yearly ranking moves. A 1.25x with rank sd 5.8 is not
            # a stable property of the event.
            "rank_sd": st.get("rank_sd"),
            "share_in_top_k": st.get("share_in_top_k"),
            "years": st.get("years"),
        }

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "study_generated": study.get("generated"),
        "sample": study.get("sample"),
        "n_events_ranked": n_events,
        "quiet_day_median": round(quiet, 4) if quiet is not None else None,
        "metric": (
            "Median absolute close-to-close move on the event date, divided by that "
            "session's trailing 60-session median absolute move — 'x a day that was "
            "normal at the time'. Magnitude only, no direction. Null distribution is "
            "the whole event calendar rotated to a random phase; BH-FDR at 0.10 "
            "across all events."
        ),
        "unmeasured": sorted(KNOWN_UNMEASURED),
        "events": rows,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)
        f.write("\n")

    survived = [k for k, v in rows.items() if v["survives_fdr"]]
    print(f"wrote {OUT}")
    print(f"  {len(rows)} calendar events mapped, {len(KNOWN_UNMEASURED)} unmeasured")
    print(f"  survives FDR: {', '.join(survived) if survived else 'none'}")
    for k, v in sorted(rows.items(), key=lambda kv: kv[1]["rank"]):
        flag = "FDR" if v["survives_fdr"] else "   "
        print(f"  {v['rank']:2d} {flag} {k:<32s} {v['multiplier']:.3f}x  p={v['p']:.4f}")


if __name__ == "__main__":
    main()
