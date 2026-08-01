"""Bake the conditional distributions the context module serves at request time."""
import warnings, os, glob, json
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, talib
from scipy import stats

PRICES = r"C:\Users\jdmey\strategy_discovery\data\prices"
frames = []
for fp in sorted(glob.glob(os.path.join(PRICES, "*.parquet"))):
    d = pd.read_parquet(fp)
    if len(d) < 400: continue
    o, h, l, c = (d[x].values.astype(float) for x in ("open", "high", "low", "close"))
    v = d["volume"].values.astype(float)
    atr = talib.ATR(h, l, c, 14); rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        f = pd.DataFrame({
            "date": d.index,
            "range_atr": rng / atr,
            "clv": np.where(rng > 0, (c - l) / rng, 0.5),
            "vol_rel": v / pd.Series(v).rolling(20).mean().values,
            "fwd_range_atr": np.roll(rng, -1) / atr,
            "fwd_ret": np.roll(c, -1) / c - 1,
        })
    frames.append(f.iloc[20:-1])

P = pd.concat(frames, ignore_index=True).replace([np.inf, -np.inf], np.nan).dropna()
P["date"] = pd.to_datetime(P["date"])
print(f"panel {len(P):,} bars, {P.date.nunique():,} sessions")

# Range bins are FIXED cut points, not quantiles — the module must be able to
# place a live bar into a bin without the rest of the cross-section.
RANGE_EDGES = [0.0, 0.6, 0.8, 1.0, 1.3, 99.0]
VOL_EDGES = [0.0, 0.8, 1.2, 99.0]
CLV_EDGES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0001]

P["rb"] = pd.cut(P.range_atr, RANGE_EDGES, labels=False, include_lowest=True)
P["vb"] = pd.cut(P.vol_rel, VOL_EDGES, labels=False, include_lowest=True)
P["cb"] = pd.cut(P.clv, CLV_EDGES, labels=False, include_lowest=True)

range_tbl = {}
for (rb, vb), g in P.groupby(["rb", "vb"]):
    if len(g) < 500: continue
    q = g.fwd_range_atr.quantile([.25, .5, .75, .9])
    range_tbl[f"{int(rb)}|{int(vb)}"] = dict(
        n=int(len(g)), p25=round(float(q[.25]), 3), p50=round(float(q[.5]), 3),
        p75=round(float(q[.75]), 3), p90=round(float(q[.9]), 3),
        gt1atr=round(float((g.fwd_range_atr > 1).mean() * 100), 1))

clv_tbl = {}
for cb, g in P.groupby("cb"):
    daily = g.groupby("date").fwd_ret.mean()
    t, p = stats.ttest_1samp(daily.dropna().values, 0.0)
    clv_tbl[str(int(cb))] = dict(
        n=int(len(g)), up=round(float((g.fwd_ret > 0).mean() * 100), 1),
        med_ret=round(float(g.fwd_ret.median() * 100), 4),
        mean_ret=round(float(g.fwd_ret.mean() * 100), 4),
        t=round(float(t), 1), p=round(float(p), 4),
        fwd_range=round(float(g.fwd_range_atr.median()), 3))

# Fama-MacBeth ICs for the honest effect-size disclosure.
ics = {}
for feat, tgt in [("range_atr", "fwd_range_atr"), ("vol_rel", "fwd_range_atr"),
                  ("clv", "fwd_ret"), ("range_atr", "fwd_ret")]:
    s = P.groupby("date").apply(lambda x: stats.spearmanr(x[feat], x[tgt])[0] if len(x) > 20 else np.nan).dropna()
    t, p = stats.ttest_1samp(s.values, 0.0)
    ics[f"{feat}->{tgt}"] = dict(ic=round(float(s.mean()), 4), t=round(float(t), 1),
                                 p=float(f"{p:.2e}"), sessions=int(len(s)))

out = dict(
    meta=dict(generated="2026-08-01", bars=int(len(P)), sessions=int(P.date.nunique()),
              names=192, sample_from=str(P.date.min().date()), sample_to=str(P.date.max().date()),
              range_edges=RANGE_EDGES, vol_edges=VOL_EDGES, clv_edges=CLV_EDGES,
              method="Fama-MacBeth: daily cross-sectional rank IC, t-tested across sessions"),
    ics=ics, range_table=range_tbl, clv_table=clv_tbl)
json.dump(out, open(r"C:\Users\jdmey\aistatcharts\src\candle_context_tables.json", "w"), indent=0)
print(f"range cells {len(range_tbl)} | clv cells {len(clv_tbl)}")
print(json.dumps(ics, indent=1))
print("\nrange table sample:")
for k in list(range_tbl)[:6]:
    print(" ", k, range_tbl[k])
print("\nclv table:")
for k, vv in clv_tbl.items(): print(" ", k, vv)
