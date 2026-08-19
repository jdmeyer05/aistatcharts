"""Run the whole market-movers study and write the artefacts.

    py -3.14 -m research.market_movers.run_study

Produces, under research/market_movers/output/:
  study.json   every number, including the ones that did not survive
  REPORT.md    the readable version, with the nulls kept in

WHY THE NULLS STAY IN. Twenty-three events were tested and two survived
multiple-testing control. A report that showed only those two would imply the
other twenty-one were never asked about, and the next person to wonder whether
CPI moves the tape would run the same test again. The failures are the expensive
part of the result.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("market_movers")

OUT = Path(__file__).parent / "output"


def _load_secrets() -> None:
    if os.environ.get("FRED_API_KEY"):
        return
    try:
        import toml
        for k, v in toml.load(".streamlit/secrets.toml").items():
            if isinstance(v, str):
                os.environ.setdefault(k, v)
    except Exception:
        pass


def _fmt_pct(x) -> str:
    return "—" if x is None else f"{x:+.3f}"


def build_report(res: dict) -> str:
    ev = res["events"]
    dr = res["drivers"]
    lines: list[str] = []
    A = lines.append

    A("# What actually moves the tape")
    A("")
    A(f"Generated {res['generated']}. SPY daily bars, "
      f"{res['sample']['sessions']} sessions from {res['sample']['first']} to {res['sample']['last']}.")
    A("")
    A("**The metric.** Every session is divided by the trailing 60-session median absolute")
    A("move, so a value of 1.40 means \"1.40x a day that was normal AT THE TIME\". Without")
    A("that normalisation the ranking would partly be a ranking of which events happened")
    A("to fall in 2022. Close-to-close, not range: the releases that matter land at 08:30,")
    A("an hour before the bell, and an RTH range cannot see that reaction.")
    A("")
    A("**No direction is claimed anywhere below.** The question is how big, not which way.")
    A("")

    A("## 1. Scheduled events, ranked over the full sample")
    A("")
    A("`xNormal` is the median relative move on those sessions. `p` comes from rotating the")
    A("whole event calendar to a random phase — a null that preserves both the calendar's")
    A("spacing and the way volatility clusters. `FDR` is Benjamini-Hochberg at 0.10 across")
    A("all events tested, not across the survivors.")
    A("")
    A("| # | Event | n | xNormal | 95% CI | vs quiet day | p | FDR | ≥1.5x | t+1 |")
    A("|---|---|---:|---:|---|---:|---:|:--:|---:|---:|")
    for r in ev["ranking"]:
        ci = f"[{r['rel_abs_ci95'][0]:.2f}, {r['rel_abs_ci95'][1]:.2f}]"
        t1 = r["persistence"].get("t+1")
        A(f"| {r['rank']} | {r['event']} | {r['n']} | {r['rel_abs_median']:.2f} | {ci} | "
          f"{(r['vs_quiet'] or float('nan')):.2f} | {r['p_rotation']:.4f} | "
          f"{'**yes**' if r['survives_fdr'] else 'no'} | {r['share_over_1_5x']:.0%} | "
          f"{t1 if t1 is not None else '—'} |")
    A("")
    survivors = [r["event"] for r in ev["ranking"] if r["survives_fdr"]]
    A(f"**Survived FDR: {', '.join(survivors) if survivors else 'nothing'}.** "
      f"Everything else is inside the noise of its own calendar.")
    A("")
    A("Two things the table is easy to misread:")
    A("")
    A("- **FOMC is third and does not survive.** Its raw p is 0.0325, which would pass any")
    A("  single test and does not pass twenty-three of them. Read it as \"probably real,")
    A("  not established here\" rather than as a null — its confidence interval spans 0.79")
    A("  to 1.62, which is the honest width at n=123 on a distribution this heavy-tailed.")
    A("- **Triple witching is a subset of monthly opex,** not an independent row. Every")
    A("  witching Friday is also an opex Friday, so the two lines share 58 sessions.")
    A("")
    A("### Persistence: the move does not carry")
    A("")
    A("The `t+1` column is the same metric on the following session. Nothing here stays")
    A("elevated, and the biggest event in the table is followed by a QUIETER-than-normal")
    A("day: payrolls run 1.39x on the print and 0.84x the session after. Whatever these")
    A("events do, they do it once and it is over by the next close.")
    A("")

    A("## 2. The ranking is not stable, and that is the finding")
    A("")
    A("Mean rank across calendar years, with the spread. An event with a good average and a")
    A("wide spread is a different proposition from one that sits in the same place every")
    A("year, and the pooled table above cannot tell them apart.")
    A("")
    A("| Event | years | mean rank | best | worst | rank sd | in top 10 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for r in ev["stability"]:
        A(f"| {r['event']} | {r['years']} | {r['mean_rank']:.1f} | {r['best_rank']} | "
          f"{r['worst_rank']} | {r['rank_sd'] if r['rank_sd'] is not None else '—'} | "
          f"{r['share_in_top_k']:.0%} |")
    A("")

    A("### Top five by year")
    A("")
    for yr, rows in ev["by_year"].items():
        top = " · ".join(f"{r['event']} {r['rel_abs_median']:.2f}" for r in rows[:5])
        A(f"- **{yr}** — {top}")
    A("")

    A("## 3. Continuous drivers: what share of the daily move they explain")
    A("")
    cur = dr["macro"]["current"]
    A(f"Rolling {dr['window_sessions']}-session regression of SPY daily returns on four macro")
    A(f"markets. Latest window to {cur['as_of']}: total R² **{cur['r2_total']:.3f}**.")
    A("")
    A("| Driver | incremental R² (latest) | same-day corr | next-day corr |")
    A("|---|---:|---:|---:|")
    for nm in cur["ranking"]:
        c = dr["macro"]["correlations"]["full"][nm]
        A(f"| {nm} | {cur['shares'][nm]:+.4f} | {c['same_day']:+.3f} | {c['next_day']:+.3f} |")
    A("")
    A("**Every next-day correlation is inside noise.** That column is the control, and it")
    A("says these relationships account for what happened rather than anticipate it. None of")
    A("this is a signal.")
    A("")
    A("### Which macro driver mattered, by year")
    A("")
    A("| Year | total R² | ranking |")
    A("|---|---:|---|")
    for yr, v in dr["macro"]["by_year"].items():
        order = " > ".join(f"{nm.split(' (')[0]} {v['shares'][nm]:+.3f}" for nm in v["ranking"])
        A(f"| {yr} | {v['r2_total_mean']:.3f} | {order} |")
    A("")
    rc = dr["risk_comovement"]["current"]
    A("### Credit, held out on purpose")
    A("")
    A(f"Adding high yield against duration takes the latest window from R² "
      f"{rc['r2_macro_only']:.3f} to {rc['r2_with_credit']:.3f} — an incremental "
      f"{rc['credit_incremental_r2']:+.4f}, larger than any macro driver.")
    A("")
    A(dr["risk_comovement"]["note"])
    A("")
    A("### Composition — what kind of tape, not what drove it")
    A("")
    A("| Spread | same-day corr | next-day corr |")
    A("|---|---:|---:|")
    for nm, c in dr["composition"]["correlations"]["full"].items():
        A(f"| {nm} | {c['same_day']:+.3f} | {c['next_day']:+.3f} |")
    A("")
    A(dr["composition"]["note"])
    A("")

    A("## What this does not say")
    A("")
    A("- **Nothing here is predictive.** Event magnitude is measured on the day the event")
    A("  lands; driver attribution is same-day and its next-day column is flat.")
    A("- **Magnitude is not direction.** A 1.4x session is a wider session, not an up one.")
    A("- **Rule-derived dates are softer than fetched ones.** ISM, FOMC minutes, opex and")
    A("  month end come from calendar rules, not a publisher's calendar.")
    A("- **Events overlap.** Claims land on 20% of all sessions, NFP is always a Friday, and")
    A("  triple witching is a strict subset of monthly opex. The quiet-day column is the")
    A("  contrast that accounts for this; the raw ratio does not.")
    A("- **The event ranking and the driver ranking are separate questions.** One says which")
    A("  dates are wide, the other says which market SPY co-moved with. Neither implies the")
    A("  other, and nothing here connects them.")
    A("- **The regional surveys start late** — Empire in 2014, Philly in 2015 — because that")
    A("  is where FRED's release calendar begins for them.")
    return "\n".join(lines)


def main() -> int:
    _load_secrets()
    sys.path.insert(0, os.getcwd())
    from research.market_movers.events import build_universe
    from research.market_movers.movers import (
        load_sessions, rank_universe, rank_by_year, rank_stability)
    from research.market_movers import drivers as drivers_mod

    start_events, today = "2012-01-01", date.today().isoformat()

    logger.warning("fetching event dates…")
    universe = build_universe(start_events, today)
    logger.warning(f"  {len(universe)} event types")

    logger.warning("loading sessions…")
    df = load_sessions("SPY", "2011-06-01")
    df = df[df.index >= start_events]

    logger.warning("ranking events…")
    ranking = rank_universe(df, universe)
    by_year = rank_by_year(df, universe)
    stability = rank_stability(by_year, top_k=10)

    logger.warning("running driver attribution…")
    drv = drivers_mod.run(start="2011-06-01")

    res = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sample": {"ticker": "SPY", "sessions": int(len(df)),
                   "first": str(df.index[0].date()), "last": str(df.index[-1].date())},
        "events": {
            "universe": {k: {"n": len(v["dates"]), "family": v["family"],
                             "source": v["source"], "cadence": v.get("cadence"),
                             "release_time_et": v.get("release_time_et")}
                         for k, v in universe.items()},
            "ranking": ranking,
            "by_year": by_year,
            "stability": stability,
        },
        "drivers": drv,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "study.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    (OUT / "REPORT.md").write_text(build_report(res), encoding="utf-8")
    logger.warning(f"wrote {OUT / 'study.json'} and {OUT / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
