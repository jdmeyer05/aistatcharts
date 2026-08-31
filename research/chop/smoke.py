import sys, json; sys.path.insert(0,'.')
import pandas as pd
from src.es_chop import session_chop
f = pd.read_parquet('research/chop/spy_5m.parquet').sort_index()
r = session_chop(fine=f, now=pd.Timestamp('2026-08-31 11:40', tz='America/New_York'))
print(json.dumps(r, indent=1))
