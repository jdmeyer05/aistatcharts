"""Confidence for a COMPLETED hour.

A finished hour has no forecast uncertainty and no sampling noise: efficiency is
a deterministic function of its returns, and it is invariant to permuting them,
so there is nothing to bootstrap. What CAN be wrong is the classification --
whether calling it "choppy" survives the hour being slightly different.

So: leave-one-return-out. Drop each bar in turn, reclassify, and count how often
the label holds. An hour whose character rests on one big bar is fragile; one
built from twelve consistent bars is not. The question this file has to answer
is whether that is a real second dimension or just the percentile wearing a hat.
"""
import numpy as np, pandas as pd

f = pd.read_parquet("research/chop/spy_5m.parquet").sort_index()
f["day"] = f.index.normalize()
B = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]

def bk(ts):
    m = ts.hour*60 + ts.minute
    return None if (m < 570 or m >= 960) else B[min((m-570)//60, 6)]

def er_from(r):
    s = np.abs(r).sum()
    return abs(r.sum())/s if s > 0 else np.nan

rows = []
last = f.day.max()
for d, g in f.groupby("day"):
    if d == last or len(g) < 76: continue
    bb = g.index.map(bk); c = g["Close"].to_numpy(float)
    for k in B:
        cc = c[bb == k]
        if len(cc) < 3: continue
        r = np.diff(cc)
        rows.append({"day": d, "bucket": k, "er": er_from(r), "n": len(r),
                     "jk": [er_from(np.delete(r, i)) for i in range(len(r))]})
h = pd.DataFrame(rows)

# Per-bucket tercile cuts, then leave-one-out agreement with the full-hour label.
agree = []
for k, g in h.groupby("bucket"):
    lo, hi = g.er.quantile([1/3, 2/3])
    cls = lambda v: "choppy" if v < lo else ("trendy" if v >= hi else "mixed")
    for _, row in g.iterrows():
        base = cls(row.er)
        a = np.mean([cls(v) == base for v in row.jk])
        agree.append({"bucket": k, "er": row.er, "label": base, "agree": a,
                      "pct": (g.er < row.er).mean()})
a = pd.DataFrame(agree)

print("Leave-one-out agreement, distribution:")
print("  " + "  ".join(f"p{q}={a.agree.quantile(q/100):.2f}" for q in (10,25,50,75,90)))
print(f"  share at 100% agreement: {(a.agree >= 0.999).mean():.0%}")

print("\nIs it just the percentile in disguise? Agreement by percentile band:")
print(f"{'pctile band':>14} {'n':>6} {'mean agree':>11} {'sd within band':>15}")
for loq, hiq in ((0,.10),(.10,.20),(.20,1/3),(1/3,2/3),(2/3,.80),(.80,.90),(.90,1.0)):
    s = a[(a.pct >= loq) & (a.pct < hiq)] if hiq < 1 else a[a.pct >= loq]
    print(f"{f'p{loq*100:.0f}-{hiq*100:.0f}':>14} {len(s):>6} {s.agree.mean():>11.2f} {s.agree.std():>15.2f}")
print("\n  Real spread WITHIN a band => fragility is a second axis, not a restatement.")

print("\nProposed cut: confident = every replicate agrees; likely = >=2/3 agree.")
for k in B:
    s = a[a.bucket == k]
    conf = (s.agree >= 0.999) & (s.label != "mixed")
    lik = (s.agree >= 2/3) & (s.agree < 0.999) & (s.label != "mixed")
    print(f"  {k}: confident {conf.mean():>5.0%}   likely {lik.mean():>5.0%}   "
          f"mixed/weak {1-conf.mean()-lik.mean():>5.0%}")

print("\n=== Is 'confident' just a synonym for 'trendy'? ===")
for lab in ("choppy","mixed","trendy"):
    s = a[a.label == lab]
    print(f"  {lab:>6}: n={len(s):>5}  agreement "
          + "  ".join(f"p{q}={s.agree.quantile(q/100):.2f}" for q in (25,50,75,90))
          + f"   share>=0.90: {(s.agree>=0.90).mean():.0%}   ==1.00: {(s.agree>=0.999).mean():.0%}")
print("\n  If 'confident' fires on choppy hours at a usable rate, absolute")
print("  thresholds work. If not, the word would just restate the label.")
