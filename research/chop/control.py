"""NEGATIVE CONTROL: run the whole pipeline on sessions that cannot contain signal.

The session read is validated at 77% delivered out of sample. But it predicts a
session's FINAL efficiency from a partial reading of the SAME session, and those
windows overlap — by 15:00 most of the final number is already observed. So some
of that 77% is arithmetic, not insight, and the only way to see how much is to
feed the identical pipeline sessions built to hold no information at all.

Synthetic sessions preserve each real session's MAGNITUDE sequence exactly and
randomise only the signs. That keeps volatility clustering, the intraday
volatility smile and the fat tails, and destroys direction and any real
persistence. Anything the pipeline still "predicts" there is arithmetic.
"""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import _panel, _MARKS, _EDGES, _CONFIDENT, _LIKELY, _MIN_CELL, _FIT_WINDOW
from src.es_chop_record import _label_for

rng = np.random.default_rng(20260831)
real = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
real = real[real.index.normalize() != real.index.normalize().max()]

def synth(frame):
    """Same magnitudes, random signs, same grid."""
    out = []
    for day, g in frame.groupby(frame.index.normalize()):
        if len(g) < 76: continue
        c = g["Close"].to_numpy(float)
        r = np.diff(c)
        s = rng.choice([-1.0, 1.0], size=len(r)) * np.abs(r)
        out.append(pd.DataFrame({"Close": np.concatenate([[c[0]], c[0] + np.cumsum(s)])},
                                index=g.index))
    return pd.concat(out)

def walk(panel):
    rows = []
    for start in range(500, len(panel), 21):
        tr = panel.iloc[max(0, start-_FIT_WINDOW):start]; te = panel.iloc[start:start+21]
        if te.empty or len(tr) < 400: break
        q = tr['final'].dropna().quantile([1/3, 2/3])
        lo_f, hi_f = float(q.iloc[0]), float(q.iloc[1])
        for mark in _MARKS:
            c = tr[[mark,'final']].dropna()
            if len(c) < 200: continue
            th, tf = c[mark].to_numpy(float), c['final'].to_numpy(float)
            for _, r_ in te.iterrows():
                e, fin = r_.get(mark), r_.get('final')
                if not (np.isfinite(e) and np.isfinite(fin)): continue
                lab, side, p = _label_for(e, th, tf, lo_f, hi_f, _EDGES,
                                          _CONFIDENT, _LIKELY, _MIN_CELL)
                act = 'choppy' if fin < lo_f else ('trendy' if fin >= hi_f else 'mixed')
                rows.append({'mark': mark, 'label': lab, 'p': p,
                             'hit': (side == act) if lab != 'mixed' else (act == 'mixed')})
    return pd.DataFrame(rows).dropna(subset=['p'])

print("Building panels...")
P_real = _panel(real)
P_ctrl = _panel(synth(real))
print(f"  real {len(P_real)} sessions, control {len(P_ctrl)}\n")

R, C = walk(P_real), walk(P_ctrl)
print(f"{'label':>18} {'REAL n':>7} {'REAL':>7} | {'CTRL n':>7} {'CTRL':>7} | {'diff':>6}")
print('-'*66)
for lab in ("confident trendy","likely trendy","mixed","likely choppy","confident choppy"):
    a, b = R[R.label==lab], C[C.label==lab]
    if a.empty or b.empty:
        print(f"{lab:>18} {'—':>7}"); continue
    d = a.hit.mean()*100 - b.hit.mean()*100
    print(f"{lab:>18} {len(a):>7} {a.hit.mean()*100:>6.1f}% | {len(b):>7} "
          f"{b.hit.mean()*100:>6.1f}% | {d:>+5.1f}")

print(f"\nOVERALL accuracy   real {R.hit.mean()*100:.1f}%   control {C.hit.mean()*100:.1f}%")
print(f"non-mixed coverage real {(R.label!='mixed').mean()*100:.1f}%   "
      f"control {(C.label!='mixed').mean()*100:.1f}%")

print("\nBy mark — where, if anywhere, does real beat its own arithmetic?")
print(f"{'mark':>6} {'real':>8} {'control':>9} {'diff':>7} {'n':>6}")
for mk in _MARKS:
    a, b = R[R.mark==mk], C[C.mark==mk]
    if a.empty or b.empty: continue
    print(f"{mk:>6} {a.hit.mean()*100:>7.1f}% {b.hit.mean()*100:>8.1f}% "
          f"{a.hit.mean()*100-b.hit.mean()*100:>+6.1f} {len(a):>6}")
