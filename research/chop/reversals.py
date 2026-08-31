"""ER is a coin flip hourly. Is REVERSAL COUNT — what a flip trader feels — real?

Under a random walk the expected sign-change rate of returns is 1/2. Real tape at
one minute reverses more often (mean reversion plus bid-ask bounce). The question
is whether the EXCESS varies hour to hour beyond chance, which is what would make
it a measurement rather than a constant.

Also tested: a swing count at a tradeable threshold, which is nearer to what a
flip system actually whipsaws on than a raw tick-by-tick sign change.
"""
import numpy as np, pandas as pd
rng = np.random.default_rng(3)

d = pd.read_parquet(r"C:\Users\jdmey\spy5m_research\data\spy_1m.parquet").sort_index()
d = d[[(9*60+30) <= (t.hour*60+t.minute) < 16*60 for t in d.index]]
d["day"] = d.index.normalize()
B = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]
def bidx(ix):
    m = ix.hour.to_numpy()*60+ix.minute.to_numpy()
    o = np.full(len(m),-1); ok=(m>=570)&(m<960)
    o[ok]=np.minimum((m[ok]-570)//60,6); return o

def swings(p, thr):
    """Zigzag reversal count: direction changes that move at least `thr`."""
    if len(p) < 3: return 0
    n, last, direction = 0, p[0], 0
    for x in p[1:]:
        if direction >= 0 and x < last - thr:
            direction = -1; n += 1; last = x
        elif direction <= 0 and x > last + thr:
            direction = 1; n += 1; last = x
        else:
            last = max(last, x) if direction > 0 else (min(last, x) if direction < 0 else last)
    return n

rows=[]
for day,g in d.groupby("day"):
    if len(g) < 380: continue
    bb=bidx(g.index); c=g["close"].to_numpy(float)
    atr = np.median(np.abs(np.diff(c)))          # the hour's own scale
    for j,k in enumerate(B):
        cc=c[bb==j]
        if len(cc)<25: continue
        r=np.diff(cc)
        sgn = np.sign(r); sgn = sgn[sgn!=0]
        flips = float(np.mean(sgn[1:]!=sgn[:-1])) if len(sgn)>2 else np.nan
        rows.append({"day":day,"bucket":k,"flip_rate":flips,
                     "swings_1atr":swings(cc, atr*3),
                     "er":abs(r.sum())/np.abs(r).sum() if np.abs(r).sum()>0 else np.nan,
                     "n":len(cc)})
H=pd.DataFrame(rows).dropna()
print(f"sessions {H.day.nunique()}  hours {len(H)}\n")

print("1-minute SIGN-FLIP RATE of returns  (random walk = 0.500)")
print(f"{'bucket':>7} {'mean':>7} {'sd':>7} {'p10':>7} {'p90':>7}")
for k in B:
    s=H[H.bucket==k]
    print(f"{k:>7} {s.flip_rate.mean():>7.3f} {s.flip_rate.std():>7.3f} "
          f"{s.flip_rate.quantile(.1):>7.3f} {s.flip_rate.quantile(.9):>7.3f}")

# Is the cross-sectional spread real, or what binomial noise alone would give?
n_eff = H.n.median()-2
sd_null = np.sqrt(0.25/n_eff)
print(f"\n  observed sd {H.flip_rate.std():.4f} vs binomial-noise-only "
      f"{sd_null:.4f}  ->  ratio {H.flip_rate.std()/sd_null:.2f}")
print("  ratio > 1 means hours genuinely differ in how much they reverse.")

print("\nDOES IT PERSIST? (the thing ER could not do)")
piv = H.pivot_table(index="day", columns="bucket", values="flip_rate")
for i in range(len(B)-1):
    a,b = B[i],B[i+1]
    v = piv[[a,b]].dropna()
    if len(v)<100: continue
    r = v.corr().iloc[0,1]; t = r*np.sqrt(len(v)-2)/np.sqrt(1-r*r)
    print(f"  {a} -> {b}:  corr {r:+.3f}  t={t:+.2f}  n={len(v)}")
pe = H.pivot_table(index="day", columns="bucket", values="er")
print("\n  (for comparison, the same on ER:)")
for i in range(len(B)-1):
    a,b=B[i],B[i+1]
    v=pe[[a,b]].dropna()
    if len(v)<100: continue
    r=v.corr().iloc[0,1]; t=r*np.sqrt(len(v)-2)/np.sqrt(1-r*r)
    print(f"  {a} -> {b}:  corr {r:+.3f}  t={t:+.2f}")
H.to_parquet("research/chop/reversals.parquet")
