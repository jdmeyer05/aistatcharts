"""Recursive numpy/pandas → native-Python sanitizer for FastAPI responses.

FastAPI's `jsonable_encoder` refuses `numpy.int64`/`numpy.float64` scalars
("'numpy.int64' object is not iterable"). This happens whenever a route
returns a dict that includes `df.to_dict(orient="records")` results or
other structures built from numpy/pandas internals.

Two entry points:

    json_safe(obj)
        Apply at the return boundary of any endpoint. Recursively walks the
        object and casts numpy/pandas scalars to natives.

    df_records(df, date_cols=("Date","date"))
        Drop-in replacement for `df.to_dict(orient="records")`. Returns a
        JSON-safe list of dicts AND normalizes any date-like columns to
        `YYYY-MM-DD` strings so frontends can string-match across series
        (Fama-French factors, CFTC report dates, FRED series) without
        timezone-suffix drift.

Prefer these over per-field casts — numpy types can hide in nested dicts,
helper-returned dicts, and tuple entries that are easy to miss.
"""

from __future__ import annotations

import math
from typing import Any


def json_safe(obj: Any) -> Any:
    """Return a copy of `obj` with numpy/pandas scalars cast to natives.

    NaN floats become None — JSON has no NaN and browsers choke on it.
    pd.Timestamp → ISO 8601 string.
    np.ndarray → list (recursively sanitized).
    Unknown objects pass through; jsonable_encoder will raise if they're
    also unserializable.
    """
    # Lazy imports keep this module cheap to import elsewhere.
    import numpy as np
    try:
        import pandas as pd
        _pd_ts = pd.Timestamp
    except Exception:
        _pd_ts = None

    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if math.isnan(v) else v
    if isinstance(obj, float):
        # Plain float NaN also breaks JSON (json.dumps emits `NaN`, browsers reject).
        return None if math.isnan(obj) else obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return json_safe(obj.tolist())
    if _pd_ts is not None and isinstance(obj, _pd_ts):
        return obj.isoformat()
    return obj


_DEFAULT_DATE_COLS = ("Date", "date", "period", "report_date", "timestamp", "datetime")


def df_records(df, date_cols=_DEFAULT_DATE_COLS):
    """Safe `df.to_dict(orient="records")` — returns JSON-safe list of dicts.

    Steps:
      1. Coerce any present date-like columns to `YYYY-MM-DD` strings so
         frontend string joins against other series line up without
         timezone-suffix mismatches. Silent no-op if a listed column is
         absent.
      2. Round-trip through pandas' JSON encoder — converts numpy int64 /
         float64 / Timestamp to native Python scalars and NaN to null. Much
         more reliable than `.to_dict()` which leaves numpy types in place.

    Empty / None DataFrame returns `[]`.
    """
    if df is None:
        return []
    # Avoid hasattr-sniffing; let the shape failures bubble up. DataFrames
    # are the only expected input.
    if df.empty:
        return []
    import pandas as pd
    import json as _json

    df = df.copy()
    for col in date_cols:
        if col in df.columns:
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
            except Exception:
                # Column exists but isn't parseable — leave as-is; the round
                # trip below will still normalize numpy scalars.
                pass
    return _json.loads(df.to_json(orient="records"))
