import sys, json; sys.path.insert(0,'.')
import pandas as pd
import src.es_cockpit          # confirms the new import resolves in situ
from src.es_chop import session_chop
r = session_chop()             # real _fine(), no injection
print(json.dumps({k: r.get(k) for k in
      ("available","reason","mark","label","confidence","efficiency","pctile",
       "p_finish_choppy_pct","p_finish_trendy_pct","n_band","sessions")}, indent=1))
print("\nnote:", r.get("note"))
print("\nforward:", (r.get("forward") or {}).get("note"))
