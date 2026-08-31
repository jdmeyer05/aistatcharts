"""Is the efficiency distribution drifting? If so the fit lags, and the fix is a
rolling training window rather than a threshold move."""
import sys; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import _panel

f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
panel = _panel(f[f.index.normalize() != f.index.normalize().max()])
panel = panel.sort_index()

print("Final-session efficiency by year — is it moving?")
print(f"{'year':>6} {'n':>5} {'p33':>8} {'median':>8} {'p67':>8}")
for y, g in panel.groupby(panel.index.year):
    if len(g) < 60: continue
    q = g['final'].quantile([1/3, .5, 2/3])
    print(f"{y:>6} {len(g):>5} {q.iloc[0]:>8.3f} {q.iloc[1]:>8.3f} {q.iloc[2]:>8.3f}")

# Direct test: the share of RECENT sessions that a cut fitted on OLDER sessions
# calls choppy. If the distribution is stationary this sits at 33%.
print("\nShare of each test block called choppy by cuts fitted on all PRIOR data")
print("(stationary => 33%; above => the fit's cuts are stale and over-call choppy)")
shares = []
for start in range(500, len(panel), 63):
    tr, te = panel.iloc[:start], panel.iloc[start:start+63]
    if len(te) < 30: break
    lo = tr['final'].quantile(1/3)
    shares.append(((te['final'] < lo).mean(), te.index[0].date(), te.index[-1].date()))
for sh, a, b in shares:
    print(f"  {a} -> {b}: {sh:>5.0%}")
print(f"\n  mean {np.mean([s[0] for s in shares]):.1%} across {len(shares)} blocks "
      f"(33.3% if stationary)")
