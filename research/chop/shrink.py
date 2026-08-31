"""Empirical-Bayes shrinkage of the band frequencies.

The top of the reliability curve runs 3 points hot, which is what happens when a
cell's observed rate is partly high BECAUSE it was the highest — the winner's
curse. The fix is to shrink each band toward the base rate by an amount set by
how noisy that cell is relative to how much the bands genuinely differ. The
weight is ESTIMATED (empirical Bayes), not chosen, so nothing here is tuned.
"""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import _panel, _MARKS, _EDGES, _CONFIDENT, _LIKELY, _MIN_CELL, _FIT_WINDOW

f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
panel = _panel(f[f.index.normalize() != f.index.normalize().max()]).sort_index()
MIN, STEP = 500, 21

def label_of(er_now, th, tf, lo_f, hi_f, shrink):
    edges = np.quantile(th, _EDGES)
    bi = 0
    for i in range(len(edges)-1):
        last = i == len(edges)-2
        if edges[i] <= er_now < edges[i+1] or (last and er_now >= edges[i]):
            bi = i; break
    # all band rates for this mark, needed to estimate the shrinkage weight
    ps, ns = [], []
    for i in range(len(edges)-1):
        m = (th >= edges[i]) & (np.ones_like(th,bool) if i>=len(edges)-2 else (th < edges[i+1]))
        if m.sum() == 0: ps.append((np.nan,np.nan)); ns.append(0); continue
        fr = tf[m]
        ps.append((float((fr<lo_f).mean()), float((fr>=hi_f).mean()))); ns.append(int(m.sum()))
    base_c = float((tf < lo_f).mean()); base_t = float((tf >= hi_f).mean())

    def _shrunk(vals, base):
        v = np.array([x for x in vals if np.isfinite(x)], float)
        nn = np.array([n for x, n in zip(vals, ns) if np.isfinite(x)], float)
        if len(v) < 3 or not shrink: return None
        between = float(v.var(ddof=1))
        within = float(np.mean(base*(1-base)/np.maximum(nn,1)))
        if between <= within or between <= 0: return 1e9          # all noise -> full shrink
        return within/(between-within)                             # prior weight, in observations

    k_c = _shrunk([p[0] for p in ps], base_c) if shrink else 0.0
    k_t = _shrunk([p[1] for p in ps], base_t) if shrink else 0.0
    n_b = ns[bi]
    if n_b < _MIN_CELL:
        m = (th < np.quantile(th,1/3)) if er_now < float(np.median(th)) else (th >= np.quantile(th,2/3))
        fr = tf[m]; p_c, p_t, n_b = float((fr<lo_f).mean()), float((fr>=hi_f).mean()), int(m.sum())
    else:
        p_c, p_t = ps[bi]
    if shrink and k_c is not None:
        p_c = (p_c*n_b + base_c*k_c)/(n_b + k_c)
        p_t = (p_t*n_b + base_t*k_t)/(n_b + k_t)
    p_best, side = (p_t,'trendy') if p_t >= p_c else (p_c,'choppy')
    if not np.isfinite(p_best) or p_best < _LIKELY:
        return 'mixed','mixed', max(0.0, 1.0-p_c-p_t)
    return ('confident ' if p_best >= _CONFIDENT else 'likely ')+side, side, p_best

def run(shrink):
    rows=[]
    for start in range(MIN, len(panel), STEP):
        tr = panel.iloc[max(0,start-_FIT_WINDOW):start]; te = panel.iloc[start:start+STEP]
        if te.empty or len(tr) < 400: break
        q = tr['final'].dropna().quantile([1/3,2/3]); lo_f,hi_f = float(q.iloc[0]),float(q.iloc[1])
        for mark in _MARKS:
            c = tr[[mark,'final']].dropna()
            if len(c) < 200: continue
            th,tf = c[mark].to_numpy(float), c['final'].to_numpy(float)
            for day,r in te.iterrows():
                e,fin = r.get(mark), r.get('final')
                if not (np.isfinite(e) and np.isfinite(fin)): continue
                lab,side,p = label_of(e,th,tf,lo_f,hi_f,shrink)
                act = 'choppy' if fin<lo_f else ('trendy' if fin>=hi_f else 'mixed')
                rows.append({'day':day,'label':lab,'p':p,
                             'hit':(side==act) if lab!='mixed' else (act=='mixed')})
    return pd.DataFrame(rows).dropna(subset=['p']).sort_values('day')

def mae(d):
    g = [(abs((x.hit.mean()-x.p.mean())*100), len(x)) for _,x in d.groupby('label')]
    return sum(a*n for a,n in g)/sum(n for _,n in g)

for shrink,name in ((False,'raw'),(True,'shrunk')):
    d = run(shrink); h=len(d)//2
    print(f"{name:>7}: overall {mae(d):.2f}pp | first half {mae(d.iloc[:h]):.2f}pp | "
          f"second half {mae(d.iloc[h:]):.2f}pp | acc {d.hit.mean()*100:.1f}% | "
          f"coverage {(d.label!='mixed').mean()*100:.1f}%")
