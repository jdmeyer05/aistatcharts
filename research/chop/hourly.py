"""Per-HOUR character: is an hour's efficiency a measurable thing, or noise?

The session-level read is cumulative (open -> mark). This asks a different
question: what did EACH hour do on its own? An hour is 12 five-minute bars, so
the first thing to establish is whether hourly efficiency separates at all, and
whether the buckets differ enough to need their own distributions.
"""
import numpy as np, pandas as pd

f = pd.read_parquet("research/chop/spy_5m.parquet").sort_index()
f["day"] = f.index.normalize()
BUCKETS = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]

def er(c):
    if len(c) < 3: return np.nan
    t = np.abs(np.diff(c)).sum()
    return abs(c[-1]-c[0])/t if t > 0 else np.nan

def bucket_of(ts):
    m = ts.hour*60 + ts.minute
    if m < 570 or m >= 960: return None
    return BUCKETS[min((m-570)//60, 6)]

rows = []
last = f.day.max()
for d, g in f.groupby("day"):
    if d == last or len(g) < 76: continue
    rec = {"day": d}
    b = g.index.map(bucket_of)
    for k in BUCKETS:
        c = g["Close"].to_numpy()[b == k]
        rec[k] = er(c)
    rec["final"] = er(g["Close"].to_numpy())
    rows.append(rec)
h = pd.DataFrame(rows).set_index("day")
print(f"sessions {len(h)}\n")

print("Hourly efficiency distribution by bucket (do buckets differ? -> own cuts)")
print(f"{'bucket':>7} {'n':>5} {'p10':>7} {'p33':>7} {'median':>8} {'p67':>7} {'p90':>7}")
for k in BUCKETS:
    v = h[k].dropna()
    print(f"{k:>7} {len(v):>5} " + " ".join(f"{v.quantile(q):>7.3f}" for q in (.10,1/3,.5,2/3,.90)))

print("\nDoes an hour's character say anything about the SESSION's final class?")
lo, hi = h["final"].quantile([1/3, 2/3])
print(f"  (session cuts: choppy < {lo:.3f}, trendy >= {hi:.3f}; base 33%)")
print(f"{'bucket':>7} {'choppy hr -> choppy day':>24} {'trendy hr -> trendy day':>24}")
for k in BUCKETS:
    v = h[[k,"final"]].dropna()
    klo, khi = v[k].quantile([1/3, 2/3])
    pc = (v[v[k] < klo]["final"] < lo).mean()
    pt = (v[v[k] >= khi]["final"] >= hi).mean()
    print(f"{k:>7} {pc:>23.0%} {pt:>23.0%}")

print("\nAre consecutive hours related? (does an hour predict the NEXT hour?)")
for i in range(len(BUCKETS)-1):
    a, b2 = BUCKETS[i], BUCKETS[i+1]
    v = h[[a,b2]].dropna()
    r = v.corr().iloc[0,1]
    t = r*np.sqrt(len(v)-2)/np.sqrt(1-r*r)
    print(f"  {a} -> {b2}:  corr {r:+.3f}  t={t:+.2f}  n={len(v)}")
h.to_parquet("research/chop/hourly.parquet")
