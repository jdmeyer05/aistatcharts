"""Does an EXTREME reading settle the day earlier than a merely low one?

If confidence is to mean anything at 11:30, it has to rise with how far the
reading sits from the middle -- not only with how much of the clock has run.
"""
import numpy as np, pandas as pd

d = pd.read_parquet("research/chop/panel.parquet")
MARKS = ["10:30","11:00","11:30","12:00","12:30","13:00","13:30","14:00","14:30","15:00"]
cut = d.index[int(len(d) * 0.6)]
disc, hold = d[d.index < cut], d[d.index >= cut]
lo, hi = disc.er_final.quantile([1/3, 2/3])
def klass(v): return "choppy" if v < lo else ("trendy" if v >= hi else "mixed")

BANDS = [(0,.10),(.10,.20),(.20,.3333),(.3333,.6667),(.6667,.80),(.80,.90),(.90,1.0)]
NAMES = ["p0-10","p10-20","p20-33","p33-67","p67-80","p80-90","p90-100"]

print("P(session FINISHES choppy | reading is in this percentile band at this mark)")
print("holdout only; base rate 33%.  Percentile cuts fitted on discovery.\n")
hdr = f"{'mark':>6} " + " ".join(f"{n:>9}" for n in NAMES)
print(hdr); print("-"*len(hdr))
for m in MARKS:
    a = disc[[f"er_{m}","er_final"]].dropna(); b = hold[[f"er_{m}","er_final"]].dropna()
    if len(b) < 60: continue
    cuts = a[f"er_{m}"].quantile([x for lo_,hi_ in BANDS for x in (lo_,)] + [1.0]).to_numpy()
    edges = a[f"er_{m}"].quantile([0.0,.10,.20,.3333,.6667,.80,.90,1.0]).to_numpy()
    out=[]
    for i in range(len(BANDS)):
        loq, hiq = edges[i], edges[i+1]
        s = b[(b[f"er_{m}"]>=loq)&(b[f"er_{m}"]<hiq)] if i<len(BANDS)-1 else b[b[f"er_{m}"]>=loq]
        p = (s.er_final.map(klass)=="choppy").mean() if len(s) else np.nan
        out.append(f"{p:.0%}/{len(s):<3}" if len(s) else "  -  ")
    print(f"{m:>6} " + " ".join(f"{o:>9}" for o in out))

print("\nSame, for P(session FINISHES trendy):\n")
print(hdr); print("-"*len(hdr))
for m in MARKS:
    a = disc[[f"er_{m}","er_final"]].dropna(); b = hold[[f"er_{m}","er_final"]].dropna()
    if len(b) < 60: continue
    edges = a[f"er_{m}"].quantile([0.0,.10,.20,.3333,.6667,.80,.90,1.0]).to_numpy()
    out=[]
    for i in range(len(BANDS)):
        loq, hiq = edges[i], edges[i+1]
        s = b[(b[f"er_{m}"]>=loq)&(b[f"er_{m}"]<hiq)] if i<len(BANDS)-1 else b[b[f"er_{m}"]>=loq]
        p = (s.er_final.map(klass)=="trendy").mean() if len(s) else np.nan
        out.append(f"{p:.0%}/{len(s):<3}" if len(s) else "  -  ")
    print(f"{m:>6} " + " ".join(f"{o:>9}" for o in out))
