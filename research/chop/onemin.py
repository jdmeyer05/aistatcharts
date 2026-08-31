"""Does hourly character become measurable at 1-minute resolution?

12 bars cannot separate a trend from noise. 60 can, in principle. This also
measures the reversals that happen INSIDE a 5-minute bar, which is what a reader
watching a 30-second chart actually experiences as chop.
"""
import numpy as np, pandas as pd
rng = np.random.default_rng(11)

d = pd.read_parquet(r"C:\Users\jdmey\spy5m_research\data\spy_1m.parquet").sort_index()
d = d[[(9*60+30) <= (t.hour*60+t.minute) < 16*60 for t in d.index]]
d["day"] = d.index.normalize()
B = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]
def bidx(ix):
    m = ix.hour.to_numpy()*60 + ix.minute.to_numpy()
    o = np.full(len(m), -1); ok = (m>=570)&(m<960)
    o[ok] = np.minimum((m[ok]-570)//60, 6); return o
def er(r):
    a = np.abs(r).sum(); return abs(r.sum())/a if a>0 else np.nan
def sf(r, n=2000):
    e = er(r)
    S = rng.choice([-1.0,1.0], size=(n,len(r)))*r
    nu = np.abs(S.sum(1))/np.abs(r).sum()
    return e, float((nu>=e).mean()), float((nu<=e).mean()), float(np.median(nu))

rows=[]
for day, g in d.groupby("day"):
    if len(g) < 380: continue
    bb = bidx(g.index); c = g["close"].to_numpy(float)
    for j,k in enumerate(B):
        cc = c[bb==j]
        if len(cc) < 25: continue
        e,ph,pl,nm = sf(np.diff(cc))
        rows.append({"day":day,"bucket":k,"er":e,"p_trend":ph,"p_chop":pl,
                     "null_med":nm,"n":len(cc)})
H = pd.DataFrame(rows)
print(f"sessions {H.day.nunique()}  hours {len(H)}\n")

print("1-MINUTE hourly efficiency vs its sign-flip null (chance = 10% per tail)")
print(f"{'bucket':>7} {'bars':>5} {'obs med':>9} {'null med':>9} {'trend<.10':>10} {'chop<.10':>9}")
for k in B:
    s = H[H.bucket==k]
    print(f"{k:>7} {s.n.median():>5.0f} {s.er.median():>9.3f} {s.null_med.median():>9.3f} "
          f"{(s.p_trend<0.10).mean():>9.1%} {(s.p_chop<0.10).mean():>8.1%}")
pooled = H
print(f"\n  POOLED: trend<.10 {(pooled.p_trend<0.10).mean():.1%}   "
      f"chop<.10 {(pooled.p_chop<0.10).mean():.1%}")
print(f"          trend<.05 {(pooled.p_trend<0.05).mean():.1%}   "
      f"chop<.05 {(pooled.p_chop<0.05).mean():.1%}")
print("\n  A fat CHOP tail at 1-min is partly real mean reversion and partly")
print("  bid-ask bounce; a fat TREND tail cannot be bounce and is the honest signal.")

# 5-minute comparison on the SAME sessions, so the difference is resolution only.
d5 = d.copy()
d5 = d5.resample("5min").agg({"close":"last"}).dropna()
d5 = d5[[(9*60+30) <= (t.hour*60+t.minute) < 16*60 for t in d5.index]]
d5["day"] = d5.index.normalize()
r5=[]
for day,g in d5.groupby("day"):
    if len(g) < 76: continue
    bb = bidx(g.index); c = g["close"].to_numpy(float)
    for j,k in enumerate(B):
        cc = c[bb==j]
        if len(cc) < 10: continue
        e,ph,pl,nm = sf(np.diff(cc))
        r5.append({"bucket":k,"p_trend":ph,"p_chop":pl})
R5 = pd.DataFrame(r5)
print(f"\n  same sessions at 5-MIN: trend<.10 {(R5.p_trend<0.10).mean():.1%}  "
      f"chop<.10 {(R5.p_chop<0.10).mean():.1%}   (n={len(R5)})")
H.to_parquet("research/chop/hourly_1m.parquet")
