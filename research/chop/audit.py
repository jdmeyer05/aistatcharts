"""Audit the shipped module: band bookkeeping, base rates, and edge cases."""
import sys, json; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import session_chop, _panel, _classes, _band, _EDGES, _MARKS

f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
today = f.index.normalize().max()
panel = _panel(f[f.index.normalize() != today])
lo, hi = _classes(panel)

print("=== 1. Is the reported band the band actually used? ===")
for mk in ("10:30","11:30","13:30","15:00"):
    col = panel[[mk,"final"]].dropna()
    hist = col[mk].to_numpy(); edges = np.quantile(hist, _EDGES)
    ok = True
    for v in np.quantile(hist, [0.02,0.15,0.25,0.5,0.7,0.85,0.95,0.999]):
        blo, bhi, bi = _band(v, edges)
        pct = (hist < v).mean()
        # the reported band must contain the reported percentile
        if not (blo - 1e-9 <= pct <= bhi + 1e-9): ok = False; print("  MISMATCH", mk, v, pct, blo, bhi)
    print(f"  {mk}: band label always contains the percentile -> {ok}")

print("\n=== 2. Measured base rates by mark (were asserted as 33.3) ===")
for mk in _MARKS:
    col = panel[[mk,"final"]].dropna()
    fin = col["final"].to_numpy()
    print(f"  {mk}  choppy {np.mean(fin<lo)*100:5.1f}%   trendy {np.mean(fin>=hi)*100:5.1f}%   n={len(fin)}")

print("\n=== 3. Do the two class probabilities ever both clear 'likely'? ===")
worst = 0
for mk in _MARKS:
    col = panel[[mk,"final"]].dropna()
    hist = col[mk].to_numpy(); fin = col["final"].to_numpy()
    edges = np.quantile(hist, _EDGES)
    for i in range(len(edges)-1):
        m = (hist>=edges[i]) & (hist<edges[i+1] if i < len(edges)-2 else np.ones_like(hist,bool))
        if m.sum() < 40: continue
        pc, pt = np.mean(fin[m]<lo), np.mean(fin[m]>=hi)
        worst = max(worst, min(pc,pt))
print(f"  max of min(p_choppy, p_trendy) across all cells = {worst:.3f}  (>=0.45 would be ambiguous)")

print("\n=== 4. Edge cases ===")
for label, kw in (("before 10:00", dict(now=pd.Timestamp(f"{today.date()} 09:45", tz="America/New_York"))),
                  ("weekend/no session", dict(now=pd.Timestamp("2026-08-30 12:00", tz="America/New_York")))):
    r = session_chop(fine=f, **kw)
    print(f"  {label:22} -> available={r.get('available')}  reason={r.get('reason')}")
