"""Two-sided sign-flip test, at hour AND session level, plus today."""
import numpy as np, pandas as pd
rng = np.random.default_rng(7)
f = pd.read_parquet("research/chop/spy_5m.parquet").sort_index()
f["day"] = f.index.normalize()
B = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]
def bidx(ix):
    m = ix.hour.to_numpy()*60 + ix.minute.to_numpy()
    o = np.full(len(m), -1); ok = (m>=570)&(m<960)
    o[ok] = np.minimum((m[ok]-570)//60, 6); return o
def er(r):
    a = np.abs(r).sum(); return abs(r.sum())/a if a > 0 else np.nan
def two_sided(r, n=4000):
    e = er(r)
    S = rng.choice([-1.0,1.0], size=(n, len(r))) * r
    null = np.abs(S.sum(1)) / np.abs(r).sum()
    return e, float((null >= e).mean()), float((null <= e).mean())

hrows, srows, last = [], [], f.day.max()
for d, g in f.groupby("day"):
    partial = len(g) < 76
    bb = bidx(g.index); c = g["Close"].to_numpy(float)
    for j, k in enumerate(B):
        cc = c[bb == j]
        if len(cc) < 10: continue
        e, ph, pl = two_sided(np.diff(cc))
        hrows.append({"day": d, "bucket": k, "er": e, "p_trend": ph, "p_chop": pl,
                      "partial": partial})
    if not partial:
        e, ph, pl = two_sided(np.diff(c))
        srows.append({"day": d, "er": e, "p_trend": ph, "p_chop": pl})

H = pd.DataFrame(hrows); S = pd.DataFrame(srows)
hist = H[(H.day != last) & (~H.partial)]

print("=== HOURS: share clearing each tail (chance = 10%) ===")
print(f"  trendy p<0.10: {(hist.p_trend<0.10).mean():.1%}   "
      f"choppy p<0.10: {(hist.p_chop<0.10).mean():.1%}   n={len(hist)}")
print(f"  trendy p<0.05: {(hist.p_trend<0.05).mean():.1%}   "
      f"choppy p<0.05: {(hist.p_chop<0.05).mean():.1%}")
print("  => an hour is not distinguishable from a coin flip in either direction.")

print("\n=== SESSIONS (78 bars, not 12): same test ===")
print(f"  trendy p<0.10: {(S.p_trend<0.10).mean():.1%}   "
      f"choppy p<0.10: {(S.p_chop<0.10).mean():.1%}   n={len(S)}")
print(f"  trendy p<0.05: {(S.p_trend<0.05).mean():.1%}   "
      f"choppy p<0.05: {(S.p_chop<0.05).mean():.1%}")
print(f"  session ER median {S.er.median():.3f}")
print("  => if the choppy tail is FAT here, whole sessions really do mean-revert")
print("     even though single hours do not.")

print("\n=== TODAY, hour by hour ===")
td = H[H.day == last]
for _, r in td.iterrows():
    s = hist[hist.bucket == r.bucket]
    pct = (s.er < r.er).mean()*100
    verdict = ("genuinely TRENDED" if r.p_trend < 0.10 else
               "genuinely CHOPPED" if r.p_chop < 0.10 else "coin flip")
    print(f"  {r.bucket}  er {r.er:.3f}  pctile {pct:>4.0f}  "
          f"p_trend {r.p_trend:.2f}  p_chop {r.p_chop:.2f}   -> {verdict}")
