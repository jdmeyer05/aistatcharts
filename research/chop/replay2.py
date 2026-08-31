"""Re-validate the SHIPPED code across every mark, after the audit changes."""
import sys, collections; sys.path.insert(0,'.')
import numpy as np, pandas as pd
from src.es_chop import session_chop, _panel, _classes, _MARKS

f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
last = f.index.normalize().max()
panel = _panel(f[f.index.normalize() != last])
lo, hi = _classes(panel)
def klass(v): return "choppy" if v < lo else ("trendy" if v >= hi else "mixed")
days = [d for d in sorted(f.index.normalize().unique())[-320:-1] if d in panel.index]

tally = collections.defaultdict(lambda: [0, 0])
for d in days:
    for mk in _MARKS:
        r = session_chop(fine=f, now=pd.Timestamp(f"{d.date()} {mk}", tz="America/New_York")
                         + pd.Timedelta(minutes=5))
        if not r or not r.get("available"): continue
        actual, lab = klass(panel.loc[d, "final"]), r["label"]
        hit = (lab.split()[-1] == actual) if lab != "mixed" else (actual == "mixed")
        tally[lab][0] += hit; tally[lab][1] += 1

print(f"{'label':>18} {'n':>6} {'was right':>10}   claimed floor")
print("-" * 56)
for lab in ("confident trendy","likely trendy","mixed","likely choppy","confident choppy"):
    if lab not in tally: print(f"{lab:>18} {'never fired':>17}"); continue
    h, n = tally[lab]
    floor = "65%+" if lab.startswith("confident") else ("45-65%" if lab != "mixed" else "—")
    print(f"{lab:>18} {n:>6} {h/n:>9.0%}   {floor}")
