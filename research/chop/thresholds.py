"""Under the PRODUCTION fit (full sample, not the holdout split), at which mark
does each label first become reachable? The docstring makes this claim."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import _panel, _classes, _EDGES, _MARKS, _CONFIDENT, _LIKELY

f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
panel = _panel(f[f.index.normalize() != f.index.normalize().max()])
lo, hi = _classes(panel)
NAMES = ["p0-10","p10-20","p20-33","p33-67","p67-80","p80-90","p90-100"]

print(f"{'mark':>6} | {'best p_choppy':>14} {'band':>9} | {'best p_trendy':>14} {'band':>9} | reachable")
print("-"*82)
for mk in _MARKS:
    col = panel[[mk,"final"]].dropna()
    hist, fin = col[mk].to_numpy(), col["final"].to_numpy()
    edges = np.quantile(hist, _EDGES)
    bc = bt = (-1, "")
    for i in range(len(edges)-1):
        m = (hist>=edges[i]) & (hist<edges[i+1] if i < len(edges)-2 else np.ones_like(hist,bool))
        if m.sum() < 40: continue
        pc, pt = float(np.mean(fin[m]<lo)), float(np.mean(fin[m]>=hi))
        if pc > bc[0]: bc = (pc, NAMES[i])
        if pt > bt[0]: bt = (pt, NAMES[i])
    tags = []
    if bc[0] >= _CONFIDENT: tags.append("CONFIDENT CHOPPY")
    elif bc[0] >= _LIKELY:  tags.append("likely choppy")
    if bt[0] >= _CONFIDENT: tags.append("CONFIDENT TRENDY")
    elif bt[0] >= _LIKELY:  tags.append("likely trendy")
    print(f"{mk:>6} | {bc[0]:>13.0%} {bc[1]:>9} | {bt[0]:>13.0%} {bt[1]:>9} | {', '.join(tags) or 'mixed only'}")
