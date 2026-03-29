"""Verify all Supabase data is flowing correctly."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
os.environ["LOCAL_DEV"] = "false"
import toml
secrets = toml.load(".streamlit/secrets.toml")
os.environ["SUPABASE_URL"] = secrets["SUPABASE_URL"]
os.environ["SUPABASE_KEY"] = secrets["SUPABASE_KEY"]

from supabase import create_client
from datetime import datetime
db = create_client(secrets["SUPABASE_URL"], secrets["SUPABASE_KEY"])

print("=== SUPABASE DATA VERIFICATION ===\n")

tables = [
    ("api_cache", "cache_key, symbol, endpoint, expires_at"),
    ("ai_response_cache", "input_hash, model, source_page, ticker, expires_at"),
    ("price_history", "ticker, date, close"),
    ("signals", "source, ticker, direction, conviction, created_at"),
    ("metrics_history", "ticker, date, atm_iv, put_skew, vrp"),
    ("user_preferences", "user_id, key"),
    ("predictions", "source, ticker, created_at"),
    ("chat_history", "role, created_at"),
    ("ai_usage", "user_id, date, usage_count, chat_count"),
    ("positions", "ticker, type, qty, status"),
    ("iv_surface_snapshots", "ticker, date, spot"),
    ("conflict_analysis", "region, timestamp"),
    ("source_credibility", "source_handle, rolling_score"),
    ("subscriptions", "email, plan_type, status"),
    ("user_tokens", "email, balance"),
    ("payment_failures", "email, resolved"),
    ("conflict_timeline", "date, event, category"),
]

populated = 0
empty = 0

for table, columns in tables:
    try:
        result = db.table(table).select(columns, count="exact").order("id" if table not in ("price_history", "user_preferences", "user_tokens", "source_credibility") else columns.split(",")[0].strip(), desc=True).limit(3).execute()
        count = result.count if result.count is not None else len(result.data or [])
        status = "DATA" if count > 0 else "EMPTY"
        if count > 0:
            populated += 1
        else:
            empty += 1
        print(f"  {table:30s} {status:6s} ({count} rows)")
        if result.data and count > 0:
            r = result.data[0]
            preview = ", ".join(f"{k}={str(v)[:25]}" for k, v in list(r.items())[:3])
            print(f"    Latest: {preview}")
    except Exception as e:
        err = str(e)[:60]
        print(f"  {table:30s} ERROR  ({err})")
        empty += 1

print(f"\n=== {populated} populated, {empty} empty ===")

# Test write→read round-trip on api_cache (the workhorse)
print("\n=== WRITE/READ ROUND-TRIP TEST ===")
try:
    test_key = "verify_test_" + datetime.now().strftime("%H%M%S")
    db.table("api_cache").upsert({
        "cache_key": test_key,
        "response": {"test": True, "ts": datetime.now().isoformat()},
        "endpoint": "verify",
        "symbol": "TEST",
        "ttl_seconds": 60,
        "expires_at": datetime.now().isoformat(),
    }, on_conflict="cache_key").execute()

    read = db.table("api_cache").select("response").eq("cache_key", test_key).execute()
    if read.data and read.data[0]["response"].get("test") is True:
        print("  api_cache write→read: OK (JSONB round-trip verified)")
    else:
        print("  api_cache write→read: FAIL")

    db.table("api_cache").delete().eq("cache_key", test_key).execute()
    print("  cleanup: OK")
except Exception as e:
    print(f"  FAIL: {e}")
