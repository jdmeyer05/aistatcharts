"""Expanding vs rolling training window. Drift says the fit is stale; a shorter
window tracks it but estimates each cell on less data. Measure the trade."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import _panel, _MARKS, _EDGES, _CONFIDENT, _LIKELY, _MIN_CELL
from src.es_chop_record import _label_for

f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
panel = _panel(f[f.index.normalize() != f.index.normalize().max()]).sort_index()
MIN, STEP = 500, 21

def run(window):
    """window=None -> expanding; else keep only the most recent `window`."""
    rows = []
    for start in range(MIN, len(panel), STEP):
        tr = panel.iloc[:start] if window is None else panel.iloc[max(0, start-window):start]
        te = panel.iloc[start:start+STEP]
        if te.empty or len(tr) < 400: break
        q = tr['final'].dropna().quantile([1/3, 2/3])
        lo_f, hi_f = float(q.iloc[0]), float(q.iloc[1])
        for mark in _MARKS:
            c = tr[[mark,'final']].dropna()
            if len(c) < 200: continue
            th, tf = c[mark].to_numpy(float), c['final'].to_numpy(float)
            for _, r in te.iterrows():
                e, fin = r.get(mark), r.get('final')
                if not (np.isfinite(e) and np.isfinite(fin)): continue
                lab, side, p = _label_for(e, th, tf, lo_f, hi_f, _EDGES,
                                          _CONFIDENT, _LIKELY, _MIN_CELL)
                act = 'choppy' if fin < lo_f else ('trendy' if fin >= hi_f else 'mixed')
                rows.append({'label': lab, 'side': side, 'p': p, 'actual': act,
                             'hit': (side == act) if lab != 'mixed' else (act == 'mixed')})
    d = pd.DataFrame(rows)
    d = d[d.p.notna()]
    # Calibration error weighted by n, plus the worst single label.
    gaps, worst = [], 0.0
    for lab, g in d.groupby('label'):
        gap = (g.hit.mean() - g.p.mean()) * 100
        gaps.append((abs(gap), len(g)))
        worst = max(worst, abs(gap))
    wmae = sum(a*n for a, n in gaps) / sum(n for _, n in gaps)
    # Does it still call a third of sessions choppy?
    share = (d[d.label != 'mixed'].side == 'choppy').mean()
    return {'n': len(d), 'cal_mae_pp': wmae, 'worst_pp': worst,
            'acc': d.hit.mean()*100, 'choppy_share': share*100,
            'coverage': (d.label != 'mixed').mean()*100}

print(f"{'window':>12} {'obs':>7} {'cal MAE':>9} {'worst':>7} {'accuracy':>9} {'coverage':>9} {'choppy%':>8}")
print('-'*68)
for w, name in ((None,'expanding'), (1250,'roll 1250'), (1000,'roll 1000'),
                (750,'roll 750'), (600,'roll 600')):
    try:
        r = run(w)
        print(f"{name:>12} {r['n']:>7} {r['cal_mae_pp']:>8.2f}p {r['worst_pp']:>6.1f}p "
              f"{r['acc']:>8.1f}% {r['coverage']:>8.1f}% {r['choppy_share']:>7.1f}%")
    except Exception as e:
        print(f"{name:>12}  failed: {e}")

print("\n=== Does roll-750 beat expanding in BOTH halves of the scored window? ===")
def run_split(window):
    rows = []
    for start in range(MIN, len(panel), STEP):
        tr = panel.iloc[:start] if window is None else panel.iloc[max(0, start-window):start]
        te = panel.iloc[start:start+STEP]
        if te.empty or len(tr) < 400: break
        q = tr['final'].dropna().quantile([1/3, 2/3])
        lo_f, hi_f = float(q.iloc[0]), float(q.iloc[1])
        for mark in _MARKS:
            c = tr[[mark,'final']].dropna()
            if len(c) < 200: continue
            th, tf = c[mark].to_numpy(float), c['final'].to_numpy(float)
            for day, r in te.iterrows():
                e, fin = r.get(mark), r.get('final')
                if not (np.isfinite(e) and np.isfinite(fin)): continue
                lab, side, p = _label_for(e, th, tf, lo_f, hi_f, _EDGES,
                                          _CONFIDENT, _LIKELY, _MIN_CELL)
                act = 'choppy' if fin < lo_f else ('trendy' if fin >= hi_f else 'mixed')
                rows.append({'day': day, 'label': lab, 'p': p,
                             'hit': (side == act) if lab != 'mixed' else (act == 'mixed')})
    return pd.DataFrame(rows).dropna(subset=['p']).sort_values('day')

def mae(d):
    gaps = [(abs((g.hit.mean()-g.p.mean())*100), len(g)) for _, g in d.groupby('label')]
    return sum(a*n for a,n in gaps)/sum(n for _,n in gaps)

for w, name in ((None,'expanding'), (750,'roll 750')):
    d = run_split(w); h = len(d)//2
    a, b = d.iloc[:h], d.iloc[h:]
    print(f"  {name:>10}: first half {mae(a):.2f}pp   second half {mae(b):.2f}pp")
