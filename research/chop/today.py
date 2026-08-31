import numpy as np, pandas as pd
f = pd.read_parquet("research/chop/spy_5m.parquet").sort_index()
f["day"] = f.index.normalize()
def er(c):
    if len(c) < 3: return np.nan
    t = np.abs(np.diff(c)).sum()
    return abs(c[-1]-c[0])/t if t>0 else np.nan
today = f[f.day == f.day.max()]
print("today", f.day.max().date(), "bars", len(today), "last", today.index[-1].strftime("%H:%M"))
hist = []
for day, g in f.groupby("day"):
    if day == f.day.max() or len(g) < 76: continue
    c = g["Close"].to_numpy(); t = g.index.strftime("%H:%M").to_numpy()
    i = np.where(t == "11:30")[0]
    if len(i): hist.append(er(c[:int(i[0])+1]))
hist = np.array([h for h in hist if np.isfinite(h)])
i = np.where(today.index.strftime("%H:%M") == "11:30")[0]
cur = er(today["Close"].to_numpy()[:int(i[0])+1])
pct = (hist < cur).mean()*100
print(f"today's efficiency through 11:30 = {cur:.4f}")
print(f"historical median at 11:30       = {np.median(hist):.4f}   (n={len(hist)})")
print(f"today's percentile               = {pct:.0f}th")
