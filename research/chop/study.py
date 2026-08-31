"""Chop vs trend: is a live session-character label measurable, and does it persist?

Metric: Kaufman efficiency ratio on 5-minute closes.
    ER = |net move| / sum(|bar-to-bar move|)
0 = pure chop (all travel, no progress), 1 = a straight line.

ER falls mechanically with the number of bars (a random walk gives ~1/sqrt(n)),
so a reading is NEVER compared across clock times -- only against the historical
distribution AT THE SAME MARK. That per-mark percentile is the only unit used.
"""
import numpy as np, pandas as pd

f = pd.read_parquet("research/chop/spy_5m.parquet").sort_index()
f["day"] = f.index.normalize()

MARKS = ["10:00","10:30","11:00","11:30","12:00","12:30","13:00","13:30","14:00","14:30","15:00"]
FULL_BARS = 78          # 09:30-16:00 on a 5-minute grid

def er(closes: np.ndarray) -> float:
    """Efficiency ratio over a run of closes. Needs >= 3 points to mean anything."""
    if len(closes) < 3:
        return np.nan
    travel = np.abs(np.diff(closes)).sum()
    return abs(closes[-1] - closes[0]) / travel if travel > 0 else np.nan

rows = []
for day, g in f.groupby("day"):
    # Half days and today's partial session are dropped: a short session has a
    # different bar count at every mark, which is exactly the thing the per-mark
    # distribution assumes is constant.
    if len(g) < FULL_BARS - 2:
        continue
    c = g["Close"].to_numpy()
    t = g.index.strftime("%H:%M").to_numpy()
    rec = {"day": day, "er_final": er(c), "bars": len(g)}
    rec["range_pct"] = float((g["High"].max() - g["Low"].min()) / c[0] * 100)
    for m in MARKS:
        idx = np.where(t == m)[0]
        if not len(idx):
            rec[f"er_{m}"] = np.nan; rec[f"rest_{m}"] = np.nan; continue
        i = int(idx[0])
        rec[f"er_{m}"] = er(c[: i + 1])      # open -> this mark
        rec[f"rest_{m}"] = er(c[i:])         # this mark -> close  (disjoint travel)
    rows.append(rec)

d = pd.DataFrame(rows).set_index("day").sort_index()
print(f"sessions {len(d)}  {d.index.min().date()} -> {d.index.max().date()}")
print(f"final ER: median {d.er_final.median():.3f}  "
      f"p10 {d.er_final.quantile(.10):.3f}  p90 {d.er_final.quantile(.90):.3f}")

# Chop and range are supposed to be near-independent axes. Re-confirm on THIS set.
print(f"\ncorr(range_pct, er_final) = {d.range_pct.corr(d.er_final):+.3f}   "
      "(prior work on 30s bars: +0.276)")

# ---- Discovery / holdout, split by date. Never one pooled fit. ----
cut = d.index[int(len(d) * 0.6)]
disc, hold = d[d.index < cut], d[d.index >= cut]
print(f"\ndiscovery {len(disc)} (< {cut.date()})   holdout {len(hold)}")

print("\n=== 1. Does the mark's reading settle the WHOLE session's character? ===")
print("   (partly mechanical -- the elapsed part is inside er_final -- but it is")
print("    what 'is today a choppy day' actually means)")
print(f"{'mark':>6} {'bars':>5} {'corr_disc':>10} {'corr_hold':>10}")
for m in MARKS:
    a = disc[[f"er_{m}", "er_final"]].dropna()
    b = hold[[f"er_{m}", "er_final"]].dropna()
    if len(a) < 50 or len(b) < 50: continue
    nb = MARKS.index(m) * 6 + 7
    print(f"{m:>6} {nb:>5} {a.corr().iloc[0,1]:>10.3f} {b.corr().iloc[0,1]:>10.3f}")

print("\n=== 2. THE FORWARD TEST: does it predict the REMAINDER? ===")
print("   Disjoint windows -- no shared bars, so no mechanical overlap.")
print(f"{'mark':>6} {'corr_disc':>10} {'corr_hold':>10} {'t_hold':>8}")
for m in MARKS:
    a = disc[[f"er_{m}", f"rest_{m}"]].dropna()
    b = hold[[f"er_{m}", f"rest_{m}"]].dropna()
    if len(a) < 50 or len(b) < 50: continue
    r = b.corr().iloc[0, 1]
    t = r * np.sqrt(len(b) - 2) / np.sqrt(1 - r * r)
    print(f"{m:>6} {a.corr().iloc[0,1]:>10.3f} {r:>10.3f} {t:>8.2f}")

d.to_parquet("research/chop/panel.parquet")
