"""Can ANYTHING knowable at the top of the hour predict that hour's chop?

Prior work already killed 504 macro event flags, VIX1D, prior-day compression,
support/resistance and time-of-day windows. This is the compact version aimed at
exactly the question asked: predict the NEXT hour from what is on the screen now.
Fitted on the first 60% of sessions, scored on the last 40%, never pooled.
"""
import numpy as np, pandas as pd

H = pd.read_parquet("research/chop/reversals.parquet")
B = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30"]
er = H.pivot_table(index="day", columns="bucket", values="er")
fl = H.pivot_table(index="day", columns="bucket", values="flip_rate")

d = pd.read_parquet(r"C:\Users\jdmey\spy5m_research\data\spy_1m.parquet").sort_index()
d = d[[(9*60+30) <= (t.hour*60+t.minute) < 16*60 for t in d.index]]
d["day"] = d.index.normalize()
def bk(ix):
    m = ix.hour.to_numpy()*60+ix.minute.to_numpy()
    o=np.full(len(m),-1); ok=(m>=570)&(m<960); o[ok]=np.minimum((m[ok]-570)//60,6); return o

vol, rng_ = {}, {}
for day,g in d.groupby("day"):
    if len(g)<380: continue
    bb=bk(g.index); c=g["close"].to_numpy(float)
    for j,k in enumerate(B):
        cc=c[bb==j]
        if len(cc)<25: continue
        vol[(day,k)] = float(np.std(np.diff(cc)))
        rng_[(day,k)] = float((cc.max()-cc.min())/cc[0]*1e4)

rows=[]
for i in range(len(B)-1):
    a,b = B[i],B[i+1]
    for day in er.index:
        y = er.at[day,b] if b in er.columns else np.nan
        if not np.isfinite(y): continue
        f = {"day":day,"slot":i,"y":y,
             "er_prev":er.at[day,a] if a in er.columns else np.nan,
             "flip_prev":fl.at[day,a] if a in fl.columns else np.nan,
             "vol_prev":vol.get((day,a),np.nan),
             "rng_prev":rng_.get((day,a),np.nan),
             "er_cum":np.nanmean([er.at[day,x] for x in B[:i+1] if x in er.columns])}
        if all(np.isfinite(v) for k_,v in f.items() if k_ not in ("day",)):
            rows.append(f)
D = pd.DataFrame(rows).sort_values("day")
cut = D.day.quantile(0.6)
tr, te = D[D.day < cut], D[D.day >= cut]
print(f"train {len(tr)}  test {len(te)}  (predicting the NEXT hour's efficiency)\n")

X = ["er_prev","flip_prev","vol_prev","rng_prev","er_cum","slot"]
print("Univariate correlation with next hour's efficiency:")
for c in X:
    rt = np.corrcoef(tr[c], tr.y)[0,1]
    rs = np.corrcoef(te[c], te.y)[0,1]
    t = rs*np.sqrt(len(te)-2)/np.sqrt(max(1-rs*rs,1e-9))
    print(f"  {c:>10}: train {rt:+.3f}   TEST {rs:+.3f}  t={t:+.2f}")

# Multivariate, fitted on train only.
Xtr = np.column_stack([np.ones(len(tr))] + [tr[c].to_numpy() for c in X])
Xte = np.column_stack([np.ones(len(te))] + [te[c].to_numpy() for c in X])
beta, *_ = np.linalg.lstsq(Xtr, tr.y.to_numpy(), rcond=None)
pred = Xte @ beta
r = np.corrcoef(pred, te.y)[0,1]
ss_res = float(((te.y - pred)**2).sum())
ss_tot = float(((te.y - tr.y.mean())**2).sum())
print(f"\nMultivariate, out of sample: corr {r:+.3f}   R^2 {1-ss_res/ss_tot:+.4f}")
print("  (R^2 <= 0 means the fit predicts worse than the training mean)")

# The practical question: does it beat guessing at the tercile?
lo, hi = tr.y.quantile([1/3,2/3])
act = np.where(te.y<lo,"choppy",np.where(te.y>=hi,"trendy","mixed"))
prd = np.where(pred<lo,"choppy",np.where(pred>=hi,"trendy","mixed"))
print(f"\nClassifying the next hour: accuracy {np.mean(prd==act):.1%} "
      f"vs {max(pd.Series(act).value_counts(normalize=True)):.1%} for always "
      f"guessing the commonest class.")

print("\n=== Is 'slot' a market fact or the half-width 15:30 bucket? ===")
D2 = D[D.slot < len(B)-2]          # drop the 14:30 -> 15:30 transition
tr2, te2 = D2[D2.day < cut], D2[D2.day >= cut]
rs = np.corrcoef(te2.slot, te2.y)[0,1]
t = rs*np.sqrt(len(te2)-2)/np.sqrt(max(1-rs*rs,1e-9))
print(f"  slot vs next-hour ER, 15:30 EXCLUDED: corr {rs:+.3f}  t={t:+.2f}  n={len(te2)}")
X2 = np.column_stack([np.ones(len(tr2))] + [tr2[c].to_numpy() for c in X])
Xt2 = np.column_stack([np.ones(len(te2))] + [te2[c].to_numpy() for c in X])
b2,*_ = np.linalg.lstsq(X2, tr2.y.to_numpy(), rcond=None)
p2 = Xt2 @ b2
r2 = np.corrcoef(p2, te2.y)[0,1]
ss_res = float(((te2.y-p2)**2).sum()); ss_tot = float(((te2.y-tr2.y.mean())**2).sum())
print(f"  multivariate OOS without it: corr {r2:+.3f}  R^2 {1-ss_res/ss_tot:+.4f}")
lo2,hi2 = tr2.y.quantile([1/3,2/3])
a2 = np.where(te2.y<lo2,"choppy",np.where(te2.y>=hi2,"trendy","mixed"))
q2 = np.where(p2<lo2,"choppy",np.where(p2>=hi2,"trendy","mixed"))
print(f"  classification: {np.mean(q2==a2):.1%} vs "
      f"{max(pd.Series(a2).value_counts(normalize=True)):.1%} baseline")
