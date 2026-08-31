"""Pull the same 5-minute SPY series the live card uses and cache it locally."""
import os, sys
sys.path.insert(0, os.path.abspath("."))
import pandas as pd
from src.es_baserates import _polygon_5m, _INTRADAY_SYMBOL, _INTRADAY_YEARS

out = "research/chop/spy_5m.parquet"
if os.path.exists(out):
    f = pd.read_parquet(out)
else:
    f = _polygon_5m(_INTRADAY_SYMBOL, _INTRADAY_YEARS)
    if f.empty:
        raise SystemExit("EMPTY fetch")
    f.to_parquet(out)
print(_INTRADAY_SYMBOL, _INTRADAY_YEARS, "years")
print("bars", len(f), "sessions", f.index.normalize().nunique())
print(f.index.min(), "->", f.index.max())
