"""Replay the live module over history: do the four labels appear, and are they right?"""
import sys, collections; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import session_chop, _panel, _classes

f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
days = sorted(f.index.normalize().unique())[-320:-1]   # last ~year, excl. today
panel = _panel(f[f.index.normalize() != f.index.normalize().max()])
lo, hi = _classes(panel)
def klass(v): return "choppy" if v < lo else ("trendy" if v >= hi else "mixed")

tally = collections.defaultdict(lambda: [0,0])
first_seen = {}
for d in days:
    for mk in ("11:30","13:30","15:00"):
        r = session_chop(fine=f, now=pd.Timestamp(f"{d.date()} {mk}", tz="America/New_York")
                         + pd.Timedelta(minutes=5))
        if not r or not r.get("available"): continue
        lab = r["label"]
        actual = klass(panel.loc[d,"final"]) if d in panel.index else None
        if actual is None: continue
        hit = (lab.split()[-1] == actual) if lab != "mixed" else (actual == "mixed")
        tally[(mk, lab)][0] += hit; tally[(mk, lab)][1] += 1
        first_seen.setdefault(lab, mk)

print(f"{'mark':>6} {'label':>18} {'n':>5} {'was right':>10}")
print("-"*44)
for (mk, lab) in sorted(tally, key=lambda x: (x[0], x[1])):
    h, n = tally[(mk, lab)]
    print(f"{mk:>6} {lab:>18} {n:>5} {h/n:>9.0%}")
print("\nearliest mark each label appears at:", dict(first_seen))
