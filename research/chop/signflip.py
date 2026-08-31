"""Is an hour labelled "trendy" actually distinguishable from a coin flip?

The percentile scores an hour against OTHER HOURS. If the population of hours is
itself mostly random walks, the 70th percentile of it is still a random walk and
the label is ranking noise against noise.

The right null flips the SIGN of each return: it destroys direction while keeping
the magnitude of every move, so ER_null = |sum(+/-r)| / sum|r|. (Permuting the
returns instead is degenerate — ER is invariant to it, since both the sum and the
sum of absolute values are unchanged.)
"""
import numpy as np, pandas as pd

rng = np.random.default_rng(0)
f = pd.read_parquet("research/chop/spy_5m.parquet").sort_index()
f["day"] = f.index.normalize()
B = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]

def bidx(ix):
    m = ix.hour.to_numpy()*60 + ix.minute.to_numpy()
    o = np.full(len(m), -1); ok = (m>=570)&(m<960)
    o[ok] = np.minimum((m[ok]-570)//60, 6); return o

def er(r):
    a = np.abs(r).sum()
    return abs(r.sum())/a if a > 0 else np.nan

rows, last = [], f.day.max()
for d, g in f.groupby("day"):
    if len(g) < 76: continue
    bb = bidx(g.index); c = g["Close"].to_numpy(float)
    for j, k in enumerate(B):
        cc = c[bb == j]
        if len(cc) < 5: continue
        r = np.diff(cc)
        # p = share of sign-flipped worlds that trend at least this much
        S = rng.choice([-1.0, 1.0], size=(2000, len(r))) * r
        null = np.abs(S.sum(1)) / np.abs(r).sum()
        rows.append({"day": d, "bucket": k, "er": er(r), "n": len(r),
                     "p": float((null >= er(r)).mean()),
                     "null_med": float(np.median(null))})
h = pd.DataFrame(rows)
hist = h[h.day != last]

print("Observed hourly efficiency vs its own SIGN-FLIP null:")
print(f"{'bucket':>7} {'obs med':>9} {'null med':>9} {'p<0.10':>8} {'p<0.05':>8}")
for k in B:
    s = hist[hist.bucket == k]
    print(f"{k:>7} {s.er.median():>9.3f} {s.null_med.median():>9.3f} "
          f"{(s.p<0.10).mean():>7.1%} {(s.p<0.05).mean():>7.1%}")
print(f"\n  If the label were meaningful, far more than 10% of hours would clear p<0.10.")
print(f"  ALL buckets pooled: p<0.10 in {(hist.p<0.10).mean():.1%}, "
      f"p<0.05 in {(hist.p<0.05).mean():.1%} of hours.")

print("\nWhat the CURRENT percentile-based label calls trendy, vs the null:")
for k in B:
    s = hist[hist.bucket == k]
    hi = s.er.quantile(2/3)
    t = s[s.er >= hi]
    print(f"  {k}: hours labelled trendy clear p<0.10 only {(t.p<0.10).mean():>5.1%} "
          f"of the time (median p={t.p.median():.2f})")

print("\n=== TODAY ===")
td = h[h.day == last]
for _, r in td.iterrows():
    s = hist[hist.bucket == r.bucket]
    pct = (s.er < r.er).mean()*100
    print(f"  {r.bucket}  er {r.er:.3f}  pctile {pct:>4.0f}  sign-flip p = {r.p:.2f}"
          f"   {'REAL trend' if r.p < 0.10 else 'indistinguishable from a coin flip'}")
