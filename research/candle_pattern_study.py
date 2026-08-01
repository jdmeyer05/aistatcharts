"""Produce the final evidence table: every pattern x tranche x direction that
clears geometry-matching, FDR, AND an out-of-sample sign check.

Emits Python source to paste into src/candle_patterns.py, because the study is
190k path simulations over 197 names — it cannot run inside a request.
"""
import warnings, os, glob, json
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, talib
from scipy import stats
from statsmodels.stats.multitest import multipletests

PRICES = r"C:\Users\jdmey\strategy_discovery\data\prices"
TRANCHES = r"C:\Users\jdmey\strategy_discovery\data\universe\tranches.csv"
TARGET_R, HOLD, MIN_R_ATR, MIN_N = 2.0, 20, 0.25, 300
tr = pd.read_csv(TRANCHES).set_index("symbol")["tranche_label"].to_dict()
CDL = [f for f in talib.get_functions() if f.startswith("CDL")]

rows, brows = [], []
for fp in sorted(glob.glob(os.path.join(PRICES, "*.parquet"))):
    sym = os.path.basename(fp).replace(".parquet", "")
    d = pd.read_parquet(fp)
    if len(d) < 400: continue
    o, h, l, c = (d[x].values.astype(float) for x in ("open", "high", "low", "close"))
    dates = d.index.values; atr = talib.ATR(h, l, c, 14)
    tag = tr.get(sym, "?")
    if tag == "?": continue

    def sim(i, dr):
        n = len(c)
        if i + 1 >= n: return np.nan, False, np.nan
        entry = o[i]
        if dr > 0: stop = min(l[i-1], l[i-2]) if i >= 2 else l[i-1]; R = entry - stop
        else: stop = max(h[i-1], h[i-2]) if i >= 2 else h[i-1]; R = stop - entry
        if not np.isfinite(R) or R <= 0 or R < MIN_R_ATR * atr[i-1]: return np.nan, False, np.nan
        ra = R / atr[i-1]; tgt = entry + dr * TARGET_R * R
        for j in range(i, min(i + HOLD, n)):
            if dr > 0:
                if l[j] <= stop: return -1.0, True, ra
                if h[j] >= tgt: return TARGET_R, True, ra
            else:
                if h[j] >= stop: return -1.0, True, ra
                if l[j] <= tgt: return TARGET_R, True, ra
        j = min(i + HOLD, n) - 1
        return dr * (c[j] - entry) / R, True, ra

    for i in range(20, len(c) - 1):
        if not np.isfinite(atr[i-1]): continue
        for dr in (1, -1):
            r, ok, ra = sim(i, dr)
            if ok: brows.append((dr, r, ra, dates[i], tag))
    for name in CDL:
        sg = getattr(talib, name)(o, h, l, c)
        for t in np.nonzero(sg)[0]:
            i = t + 1
            if i < 20 or i >= len(c) - 1 or not np.isfinite(atr[i-1]): continue
            dr = 1 if sg[t] > 0 else -1
            r, ok, ra = sim(i, dr)
            if ok: rows.append((name[3:], dr, r, ra, dates[i], tag, sym))

S = pd.DataFrame(rows, columns=["pattern", "dir", "R", "r_atr", "date", "tranche", "sym"])
B = pd.DataFrame(brows, columns=["dir", "R", "r_atr", "date", "tranche"])
S["date"] = pd.to_datetime(S["date"]); B["date"] = pd.to_datetime(B["date"])
split = S["date"].median()
edges = pd.qcut(B["r_atr"], 10, retbins=True, duplicates="drop")[1]
B["b"] = pd.cut(B["r_atr"], edges, include_lowest=True, labels=False)
S["b"] = pd.cut(S["r_atr"], edges, include_lowest=True, labels=False)
print(f"signals {len(S):,} baseline {len(B):,} split {split.date()}")


def mexp(g, Bsub):
    look = Bsub.groupby(["dir", "b"])["R"].mean()
    key = g.groupby(["dir", "b"]).size(); tot = key.sum(); ex = 0.0
    for k, cnt in key.items():
        if k in look.index: ex += look.loc[k] * cnt
        else: tot -= cnt
    return ex / tot if tot else np.nan


def mwin(g, Bsub):
    look = Bsub.groupby(["dir", "b"])["R"].apply(lambda x: (x > 0).mean())
    key = g.groupby(["dir", "b"]).size(); tot = key.sum(); ew = 0.0
    for k, cnt in key.items():
        if k in look.index: ew += look.loc[k] * cnt
        else: tot -= cnt
    return ew / tot if tot else np.nan


out = []
# Per-trade EXCESS R: the trade's outcome minus what the matched baseline
# (same tranche, same direction, same R/ATR decile) returns on average. A
# binomial test on WIN RATE cannot see this edge at all - at a 2R target a
# pattern can win the same fraction of the time and still make money by paying
# more when it is right, which is precisely what expectancy measures.
#
# Significance is CLUSTERED BY DATE. Signals fire across dozens of names on the
# same session, so trades are nowhere near independent; treating 5,000
# correlated trades as 5,000 observations manufactures significance. Testing the
# mean of DAILY mean-excess is the conservative, standard fix.
for tag in sorted(S["tranche"].unique()):
    Bt = B[B.tranche == tag]; B1 = Bt[Bt.date <= split]; B2 = Bt[Bt.date > split]
    look = Bt.groupby(["dir", "b"])["R"].mean()
    for (pat, dr), g in S[S.tranche == tag].groupby(["pattern", "dir"]):
        if len(g) < MIN_N: continue
        g1, g2 = g[g.date <= split], g[g.date > split]
        if len(g1) < 80 or len(g2) < 80: continue
        bpt = np.asarray(g.set_index(["dir", "b"]).index.map(look), dtype=float)
        ok = np.isfinite(bpt)
        if ok.sum() < MIN_N: continue
        gg = g[ok].copy(); gg["excess"] = gg["R"].values - bpt[ok]
        daily = gg.groupby("date")["excess"].mean()
        if len(daily) < 60: continue
        t, p = stats.ttest_1samp(daily.values, 0.0)
        bw = mwin(g, Bt); be = mexp(g, Bt)
        e1 = g1["R"].mean() - mexp(g1, B1); e2 = g2["R"].mean() - mexp(g2, B2)
        out.append(dict(pattern=pat, tranche=tag, dir=int(dr), n=int(ok.sum()),
                        names=int(g["sym"].nunique()), days=int(len(daily)),
                        top1=float(g["sym"].value_counts(normalize=True).iloc[0] * 100),
                        win=(g["R"] > 0).mean()*100, mwin=bw*100,
                        exp=g["R"].mean(), mexp=be,
                        edge=float(gg["excess"].mean()), e1=e1, e2=e2, p=float(p)))

R = pd.DataFrame(out)
rej, q, _, _ = multipletests(R["p"].values, alpha=0.05, method="fdr_bh")
R["q"], R["fdr"] = q, rej
R["oos"] = (np.sign(R.e1) == np.sign(R.e2)) & (R.edge.abs() > 0.03)
R["keep"] = R.fdr & R.oos
R = R.sort_values("edge", ascending=False)
R.to_csv(os.path.join(os.path.dirname(__file__), "evidence.csv"), index=False)

print(f"\ncells {len(R)} | FDR survivors {int(R.fdr.sum())} | +OOS stable {int(R.keep.sum())}")
print("\n" + "=" * 104)
print("VALIDATED — survives geometry-matching, FDR, and an out-of-sample sign check")
print("=" * 104)
print(f"{'pattern':<18s}{'tranche':<12s}{'dir':<6s}{'n':>6s}{'nm':>4s}{'win%':>7s}{'base':>7s}{'expR':>8s}{'edgeR':>8s}{'H1':>8s}{'H2':>8s}{'q':>7s}")
for _, r in R[R.keep].iterrows():
    print(f"{r['pattern']:<18s}{r['tranche']:<12s}{'long' if r['dir']>0 else 'short':<6s}{r['n']:>6,}{r['names']:>4d}"
          f"{r['win']:>6.1f}%{r['mwin']:>6.1f}%{r['exp']:>+8.3f}{r['edge']:>+8.3f}{r['e1']:>+8.3f}{r['e2']:>+8.3f}{r['q']:>7.3f}")

# Emit python source
recs = []
for _, r in R[R.keep].iterrows():
    recs.append({"pattern": r["pattern"], "tranche": r["tranche"], "dir": int(r["dir"]),
                 "n": int(r["n"]), "names": int(r["names"]), "win": round(r["win"], 1),
                 "base_win": round(r["mwin"], 1), "exp_r": round(r["exp"], 3),
                 "base_r": round(r["mexp"], 3), "edge_r": round(r["edge"], 3),
                 "h1_r": round(r["e1"], 3), "h2_r": round(r["e2"], 3), "q": round(r["q"], 4)})
with open(os.path.join(os.path.dirname(__file__), "evidence.json"), "w") as f:
    json.dump({"cells_tested": int(len(R)), "fdr_survivors": int(R.fdr.sum()),
               "validated": len(recs), "split": str(split.date()),
               "sample_from": str(S["date"].min().date()), "sample_to": str(S["date"].max().date()),
               "signals": int(len(S)), "baseline_trades": int(len(B)),
               "records": recs}, f, indent=1)
print(f"\nwrote evidence.json ({len(recs)} validated records)")
