"""Full Supabase integration test — run with: python test_supabase.py"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8")
os.environ["LOCAL_DEV"] = "false"

import toml
secrets = toml.load(".streamlit/secrets.toml")
os.environ["SUPABASE_URL"] = secrets["SUPABASE_URL"]
os.environ["SUPABASE_KEY"] = secrets["SUPABASE_KEY"]

from supabase import create_client
from datetime import datetime, timedelta

db = create_client(secrets["SUPABASE_URL"], secrets["SUPABASE_KEY"])
print("=== SUPABASE FULL INTEGRATION TEST ===")
print("Connected: YES\n")

passed = failed = 0

def ok(name):
    global passed; passed += 1; print(f"  {name:40s} OK")

def fail(name, e):
    global failed; failed += 1; print(f"  {name:40s} FAIL ({e})")

# 1. All 14 tables readable
print("--- TABLE READ ---")
for t in ["subscriptions", "user_tokens", "payment_failures", "signals",
          "metrics_history", "positions", "pnl_history", "predictions",
          "iv_surface_snapshots", "conflict_analysis", "ai_usage",
          "chat_history", "source_credibility", "api_cache"]:
    try:
        r = db.table(t).select("*", count="exact").limit(1).execute()
        count = r.count if r.count is not None else 0
        print(f"  {t:30s} OK  ({count} rows)")
        passed += 1
    except Exception as e:
        fail(t, str(e)[:50]); failed += 1

# 2. Views
print("\n--- VIEWS ---")
try:
    r = db.table("signal_composites").select("*").limit(5).execute()
    ok(f"signal_composites ({len(r.data or [])} rows)")
except Exception as e:
    fail("signal_composites", e)

# 3. RPC functions
print("\n--- RPC FUNCTIONS ---")
for fn in ["cleanup_expired_cache", "cleanup_old_signals"]:
    try:
        db.rpc(fn).execute(); ok(fn)
    except Exception as e:
        fail(fn, e)

# 4. Signal CRUD
print("\n--- WRITE/READ/DELETE ---")
try:
    db.table("signals").insert({"user_id": "test", "source": "probe", "ticker": "ZTEST",
        "direction": "bull", "conviction": 0.8, "vol_view": "short_vol", "reasoning": "test"}).execute()
    r = db.table("signals").select("*").eq("ticker", "ZTEST").execute()
    assert len(r.data) == 1
    db.table("signals").delete().eq("ticker", "ZTEST").execute()
    ok("signals CRUD")
except Exception as e:
    fail("signals CRUD", e)

# 5. Metrics upsert
try:
    db.table("metrics_history").upsert({"user_id": "test", "ticker": "ZTEST", "date": "2099-01-01",
        "atm_iv": 0.25, "put_skew": 1.1, "vrp": 0.05, "spot": 100.0},
        on_conflict="user_id,ticker,date").execute()
    r = db.table("metrics_history").select("*").eq("ticker", "ZTEST").execute()
    assert len(r.data) == 1
    db.table("metrics_history").delete().eq("ticker", "ZTEST").execute()
    ok("metrics_history upsert")
except Exception as e:
    fail("metrics_history upsert", e)

# 6. Positions + PnL CASCADE
try:
    db.table("positions").insert({"id": "ztest01", "user_id": "test", "ticker": "ZTEST",
        "type": "stock", "qty": 100, "entry_price": 50.0, "details": "{}",
        "greeks": "{}", "alerts": "{}",
        "journal": '{"entry_thesis":"","exit_thesis":"","tags":[],"notes":[]}'}).execute()
    db.table("pnl_history").insert({"position_id": "ztest01", "date": "2099-01-01",
        "delta_pnl": 10.0, "total_pnl": 10.5}).execute()
    db.table("positions").delete().eq("id", "ztest01").execute()
    r = db.table("pnl_history").select("*").eq("position_id", "ztest01").execute()
    assert len(r.data) == 0, "CASCADE delete failed"
    ok("positions + pnl_history CASCADE")
except Exception as e:
    fail("positions + pnl CASCADE", e)

# 7. API cache
try:
    db.table("api_cache").upsert({"cache_key": "zkey", "response": {"ok": True},
        "endpoint": "/test", "symbol": "ZTEST", "ttl_seconds": 60,
        "expires_at": (datetime.now() + timedelta(minutes=5)).isoformat()},
        on_conflict="cache_key").execute()
    r = db.table("api_cache").select("response").eq("cache_key", "zkey").execute()
    assert r.data[0]["response"] == {"ok": True}
    db.table("api_cache").delete().eq("cache_key", "zkey").execute()
    ok("api_cache upsert + JSONB read")
except Exception as e:
    fail("api_cache", e)

# 8. Chat history
try:
    db.table("chat_history").insert({"user_id": "test", "session_id": "s_test",
        "role": "user", "content": "hello"}).execute()
    r = db.table("chat_history").select("*").eq("session_id", "s_test").execute()
    assert len(r.data) == 1
    db.table("chat_history").delete().eq("session_id", "s_test").execute()
    ok("chat_history")
except Exception as e:
    fail("chat_history", e)

# 9. AI usage atomic increment
try:
    db.rpc("increment_ai_usage", {"p_user_id": "tester", "p_field": "usage_count"}).execute()
    db.rpc("increment_ai_usage", {"p_user_id": "tester", "p_field": "usage_count"}).execute()
    db.rpc("increment_ai_usage", {"p_user_id": "tester", "p_field": "chat_count"}).execute()
    r = db.table("ai_usage").select("*").eq("user_id", "tester").execute()
    assert r.data[0]["usage_count"] == 2 and r.data[0]["chat_count"] == 1
    db.table("ai_usage").delete().eq("user_id", "tester").execute()
    ok("increment_ai_usage RPC (atomic)")
except Exception as e:
    fail("increment_ai_usage RPC", e)

# 10. Source credibility
try:
    db.table("source_credibility").upsert({"source_handle": "@Test",
        "total_citations": 5, "rolling_score": 80},
        on_conflict="source_handle").execute()
    r = db.table("source_credibility").select("*").eq("source_handle", "@Test").execute()
    assert r.data[0]["rolling_score"] == 80
    db.table("source_credibility").delete().eq("source_handle", "@Test").execute()
    ok("source_credibility upsert")
except Exception as e:
    fail("source_credibility", e)

print(f"\n=== RESULTS: {passed} passed, {failed} failed ===")
print("ALL TESTS PASSED" if failed == 0 else "SOME TESTS FAILED")
