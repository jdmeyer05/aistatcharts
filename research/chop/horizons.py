"""Chop is a coin flip within an hour and arithmetic within a session.
Is it real at ANY horizon, or predictable from anything knowable in advance?"""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import _panel

f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
f = f[f.index.normalize() != f.index.normalize().max()]
P = _panel(f).sort_index()
y = P['final'].to_numpy(float); idx = P.index

def tstat(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = np.isfinite(a) & np.isfinite(b)
    r = np.corrcoef(a[m], b[m])[0,1]
    return r, r*np.sqrt(m.sum()-2)/np.sqrt(max(1-r*r,1e-12)), int(m.sum())

print("1. DAY-TO-DAY: does yesterday's character predict today's?")
for lag in (1,2,3,5):
    r,t,n = tstat(y[:-lag], y[lag:]); print(f"   lag {lag}: corr {r:+.3f}  t={t:+.2f}  n={n}")

print("\n   Same effect, split by date (does it hold out?)")
h = len(y)//2
for name, sl in (("first half", slice(0,h)), ("second half", slice(h,None))):
    yy = y[sl]; r,t,n = tstat(yy[:-1], yy[1:])
    print(f"     {name:>12}: corr {r:+.3f}  t={t:+.2f}  n={n}")

print("\n   Is it just a REGRESSION artifact? Same test on the sign-randomised control:")
rng = np.random.default_rng(5)
ctrl = []
for day, g in f.groupby(f.index.normalize()):
    if len(g) < 76: continue
    c = g['Close'].to_numpy(float); r_ = np.diff(c)
    s = rng.choice([-1.,1.], size=len(r_))*np.abs(r_)
    a = np.abs(s).sum(); ctrl.append(abs(s.sum())/a if a>0 else np.nan)
ctrl = np.array(ctrl)
r,t,n = tstat(ctrl[:-1], ctrl[1:]); print(f"     control lag 1: corr {r:+.3f}  t={t:+.2f}  n={n}")

print("\n2. Rolling mean of recent character -> next session")
s_ = pd.Series(y, index=idx)
for w in (5,10,20):
    r,t,n = tstat(s_.rolling(w).mean().shift(1).to_numpy(), y)
    print(f"   trailing {w:>2}d: corr {r:+.3f}  t={t:+.2f}  n={n}")

print("\n3. Range vs efficiency (independent axes?), and range as a PREDICTOR")
rp = []
for day, g in f.groupby(f.index.normalize()):
    if len(g) < 76: continue
    c = g['Close'].to_numpy(float)
    rp.append((g['High'].max()-g['Low'].min())/c[0]*100)
rp = np.array(rp)
r,t,_ = tstat(rp, y);          print(f"   same-day range vs efficiency:   corr {r:+.3f}  t={t:+.2f}")
r,t,_ = tstat(rp[:-1], y[1:]); print(f"   PRIOR-day range -> efficiency:  corr {r:+.3f}  t={t:+.2f}  <- knowable in advance")

print("\n4. Day of week")
g_ = pd.Series(y, index=idx).groupby(idx.dayofweek)
base = np.nanmean(y)
for d, gg in g_:
    nm = ['Mon','Tue','Wed','Thu','Fri'][d]
    print(f"   {nm}: mean {gg.mean():.4f} vs {base:.4f}  z={(gg.mean()-base)/(gg.std()/np.sqrt(len(gg))):+.2f}  n={len(gg)}")

print("\n5. Opening hour -> the REST of the day (disjoint windows)")
o, rest = [], []
for day, g in f.groupby(f.index.normalize()):
    if len(g) < 76: continue
    c = g['Close'].to_numpy(float); t_ = g.index.strftime('%H:%M').to_numpy()
    i = np.where(t_=='10:30')[0]
    if not len(i): continue
    a_ = np.diff(c[:int(i[0])+1]); b_ = np.diff(c[int(i[0]):])
    o.append(abs(a_.sum())/np.abs(a_).sum() if np.abs(a_).sum()>0 else np.nan)
    rest.append(abs(b_.sum())/np.abs(b_).sum() if np.abs(b_).sum()>0 else np.nan)
r,t,n = tstat(o, rest); print(f"   09:30-10:30 vs 10:30-close: corr {r:+.3f}  t={t:+.2f}  n={n}")
