"""Turn the reading into the four labels, with a MEASURED confidence.

Class = tercile of the session's FINAL efficiency: choppy / mixed / trendy.
Confidence = P(the session finishes in the class the mark is showing), measured
on discovery sessions, then checked on the holdout. That probability is what
separates "likely" from "confident" -- it is not a word chosen by feel.
"""
import numpy as np, pandas as pd

d = pd.read_parquet("research/chop/panel.parquet")
MARKS = ["10:00","10:30","11:00","11:30","12:00","12:30","13:00","13:30","14:00","14:30","15:00"]
cut = d.index[int(len(d) * 0.6)]
disc, hold = d[d.index < cut], d[d.index >= cut]

# Final-class cuts are fitted on DISCOVERY only and applied unchanged to holdout.
lo, hi = disc.er_final.quantile([1/3, 2/3])
print(f"final-ER tercile cuts (discovery): choppy < {lo:.3f} <= mixed < {hi:.3f} <= trendy")

def klass(v):
    return "choppy" if v < lo else ("trendy" if v >= hi else "mixed")

for frame, name in ((disc, "discovery"), (hold, "holdout")):
    frame = frame.copy()
    frame["final_class"] = frame.er_final.map(klass)

print("\n=== P(final class == the class showing at this mark) ===")
print("The mark's class comes from the same tercile cuts applied to that mark's")
print("OWN historical distribution -- readings are never compared across clocks.")
hdr = f"{'mark':>6} | " + " | ".join(f"{c:>22}" for c in ("choppy now", "mixed now", "trendy now"))
print(hdr); print("-" * len(hdr))

rowsout = []
for m in MARKS:
    a = disc[[f"er_{m}", "er_final"]].dropna()
    b = hold[[f"er_{m}", "er_final"]].dropna()
    if len(a) < 60 or len(b) < 60: continue
    mlo, mhi = a[f"er_{m}"].quantile([1/3, 2/3])          # fitted on discovery
    def mk(v): return "choppy" if v < mlo else ("trendy" if v >= mhi else "mixed")
    cells = []
    for cl in ("choppy", "mixed", "trendy"):
        sa = a[a[f"er_{m}"].map(mk) == cl]; sb = b[b[f"er_{m}"].map(mk) == cl]
        pa = (sa.er_final.map(klass) == cl).mean() if len(sa) else np.nan
        pb = (sb.er_final.map(klass) == cl).mean() if len(sb) else np.nan
        cells.append(f"{pa:.0%} d / {pb:.0%} h  n={len(sb):>3}")
        rowsout.append({"mark": m, "cls": cl, "disc": pa, "hold": pb,
                        "lo": mlo, "hi": mhi, "n": len(sb)})
    print(f"{m:>6} | " + " | ".join(f"{c:>22}" for c in cells))

r = pd.DataFrame(rowsout)
print(f"\nunconditional base rate for any one class = 33%")
print(f"mean lift over base, holdout, choppy+trendy only: "
      f"{r[r.cls!='mixed'].hold.mean()/(1/3):.2f}x")

print("\n=== The forward statement, in the units the card will print ===")
print("P(the REMAINDER of the session is choppy) after a choppy reading vs after")
print("any reading -- disjoint bars, so this is the honest forward number.")
print(f"{'mark':>6} {'after choppy':>13} {'after trendy':>13} {'base':>8}")
for m in MARKS:
    b = hold[[f"er_{m}", f"rest_{m}"]].dropna()
    if len(b) < 60: continue
    a = disc[[f"er_{m}", f"rest_{m}"]].dropna()
    mlo, mhi = a[f"er_{m}"].quantile([1/3, 2/3])
    rlo = a[f"rest_{m}"].quantile(1/3)                     # "choppy remainder"
    base = (b[f"rest_{m}"] < rlo).mean()
    pc = (b[b[f"er_{m}"] < mlo][f"rest_{m}"] < rlo).mean()
    pt = (b[b[f"er_{m}"] >= mhi][f"rest_{m}"] < rlo).mean()
    print(f"{m:>6} {pc:>12.0%} {pt:>12.0%} {base:>7.0%}")
