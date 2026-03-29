"""Complete system verification — syntax, imports, Supabase, data flow."""
import sys, os, ast, glob, json
sys.stdout.reconfigure(encoding="utf-8")
os.environ["LOCAL_DEV"] = "false"

print("=" * 60)
print("AI STATCHARTS — FULL SYSTEM VERIFICATION")
print("=" * 60)

# ─── 1. SYNTAX CHECK ─────────────────────────────────────
print("\n1. SYNTAX CHECK")
files = glob.glob("pages/*.py") + glob.glob("src/*.py")
errors = []
for f in files:
    try:
        ast.parse(open(f, encoding="utf-8").read())
    except SyntaxError as e:
        errors.append(f"{os.path.basename(f)}: line {e.lineno} - {e.msg}")
print(f"   {len(files)} files, {len(errors)} errors")
for e in errors:
    print(f"   ERROR: {e}")

# ─── 2. IMPORT CHECK ─────────────────────────────────────
print("\n2. KEY IMPORTS")
modules = [
    "src.db", "src.signal_engine", "src.metrics_store", "src.position_book",
    "src.prediction_tracker", "src.api_cache", "src.ai_cache", "src.user_prefs",
    "src.data_engine", "src.auth", "src.chatbot", "src.layout", "src.styles",
    "src.options_models", "src.cross_context", "src.economic_calendar",
    "src.macro_data", "src.market_data",
]
import importlib
for m in modules:
    try:
        importlib.import_module(m)
        print(f"   {m:30s} OK")
    except Exception as e:
        print(f"   {m:30s} FAIL - {str(e)[:50]}")

# ─── 3. SUPABASE CONNECTION ──────────────────────────────
print("\n3. SUPABASE CONNECTION")
try:
    import toml
    secrets = toml.load(".streamlit/secrets.toml")
    os.environ["SUPABASE_URL"] = secrets["SUPABASE_URL"]
    os.environ["SUPABASE_KEY"] = secrets["SUPABASE_KEY"]
    from supabase import create_client
    from datetime import datetime, timedelta
    db = create_client(secrets["SUPABASE_URL"], secrets["SUPABASE_KEY"])
    print("   Connected: YES")
except Exception as e:
    print(f"   Connected: FAIL - {e}")
    db = None

# ─── 4. TABLE VERIFICATION ───────────────────────────────
if db:
    print("\n4. SUPABASE TABLES")
    tables = [
        "subscriptions", "user_tokens", "payment_failures",
        "signals", "metrics_history", "positions", "pnl_history",
        "predictions", "iv_surface_snapshots", "conflict_analysis",
        "ai_usage", "chat_history", "source_credibility", "api_cache",
        "ai_response_cache", "price_history", "user_preferences",
        "conflict_timeline",
    ]
    for t in tables:
        try:
            r = db.table(t).select("*", count="exact").limit(1).execute()
            count = r.count if r.count is not None else len(r.data or [])
            print(f"   {t:30s} OK  ({count} rows)")
        except Exception as e:
            print(f"   {t:30s} FAIL ({str(e)[:40]})")

    # ─── 5. VIEWS ─────────────────────────────────────────
    print("\n5. SUPABASE VIEWS")
    for v in ["signal_composites"]:
        try:
            r = db.table(v).select("*").limit(3).execute()
            print(f"   {v:30s} OK  ({len(r.data or [])} rows)")
        except Exception as e:
            print(f"   {v:30s} FAIL ({str(e)[:40]})")

    # ─── 6. RPC FUNCTIONS ─────────────────────────────────
    print("\n6. RPC FUNCTIONS")
    for fn in ["cleanup_expired_cache", "cleanup_old_signals", "cleanup_expired_ai_cache"]:
        try:
            db.rpc(fn).execute()
            print(f"   {fn:30s} OK")
        except Exception as e:
            print(f"   {fn:30s} FAIL ({str(e)[:40]})")

    # ─── 7. CRUD TESTS ───────────────────────────────────
    print("\n7. CRUD ROUND-TRIP TESTS")

    # api_cache
    try:
        db.table("api_cache").upsert({
            "cache_key": "test_verify", "response": {"ok": True},
            "endpoint": "test", "symbol": "TEST", "ttl_seconds": 1,
            "expires_at": (datetime.now() + timedelta(seconds=10)).isoformat(),
        }, on_conflict="cache_key").execute()
        r = db.table("api_cache").select("response").eq("cache_key", "test_verify").execute()
        assert r.data and r.data[0]["response"]["ok"] is True
        db.table("api_cache").delete().eq("cache_key", "test_verify").execute()
        print(f"   api_cache CRUD:              OK")
    except Exception as e:
        print(f"   api_cache CRUD:              FAIL ({e})")

    # ai_response_cache
    try:
        db.table("ai_response_cache").upsert({
            "input_hash": "test_verify", "model": "test", "source_page": "test",
            "response": "test response", "expires_at": (datetime.now() + timedelta(minutes=1)).isoformat(),
        }, on_conflict="input_hash").execute()
        r = db.table("ai_response_cache").select("response").eq("input_hash", "test_verify").execute()
        assert r.data and r.data[0]["response"] == "test response"
        db.table("ai_response_cache").delete().eq("input_hash", "test_verify").execute()
        print(f"   ai_response_cache CRUD:      OK")
    except Exception as e:
        print(f"   ai_response_cache CRUD:      FAIL ({e})")

    # price_history
    try:
        db.table("price_history").upsert({
            "ticker": "ZTEST", "date": "2099-01-01", "close": 99.99,
        }, on_conflict="ticker,date").execute()
        r = db.table("price_history").select("close").eq("ticker", "ZTEST").execute()
        assert r.data and r.data[0]["close"] == 99.99
        db.table("price_history").delete().eq("ticker", "ZTEST").execute()
        print(f"   price_history CRUD:          OK")
    except Exception as e:
        print(f"   price_history CRUD:          FAIL ({e})")

    # signals
    try:
        db.table("signals").insert({
            "source": "test", "ticker": "ZTEST", "direction": "bull",
            "conviction": 0.5, "vol_view": "neutral",
        }).execute()
        r = db.table("signals").select("*").eq("ticker", "ZTEST").execute()
        assert len(r.data) == 1
        db.table("signals").delete().eq("ticker", "ZTEST").execute()
        print(f"   signals CRUD:                OK")
    except Exception as e:
        print(f"   signals CRUD:                FAIL ({e})")

    # user_preferences
    try:
        db.table("user_preferences").upsert({
            "user_id": "test", "key": "test_key", "value": json.dumps({"v": 42}),
        }, on_conflict="user_id,key").execute()
        r = db.table("user_preferences").select("value").eq("user_id", "test").eq("key", "test_key").execute()
        assert r.data
        db.table("user_preferences").delete().eq("user_id", "test").execute()
        print(f"   user_preferences CRUD:       OK")
    except Exception as e:
        print(f"   user_preferences CRUD:       FAIL ({e})")

    # positions + pnl_history CASCADE
    try:
        db.table("positions").insert({
            "id": "ztest99", "ticker": "ZTEST", "type": "stock",
            "qty": 1, "entry_price": 1.0, "details": "{}",
            "greeks": "{}", "alerts": "{}",
            "journal": '{"entry_thesis":"","exit_thesis":"","tags":[],"notes":[]}',
        }).execute()
        db.table("pnl_history").insert({
            "position_id": "ztest99", "date": "2099-01-01", "total_pnl": 1.0,
        }).execute()
        db.table("positions").delete().eq("id", "ztest99").execute()
        r = db.table("pnl_history").select("*").eq("position_id", "ztest99").execute()
        assert len(r.data) == 0  # CASCADE delete
        print(f"   positions+pnl CASCADE:       OK")
    except Exception as e:
        print(f"   positions+pnl CASCADE:       FAIL ({e})")

    # increment_ai_usage RPC
    try:
        db.rpc("increment_ai_usage", {"p_user_id": "verify_test", "p_field": "usage_count"}).execute()
        r = db.table("ai_usage").select("usage_count").eq("user_id", "verify_test").execute()
        assert r.data and r.data[0]["usage_count"] == 1
        db.table("ai_usage").delete().eq("user_id", "verify_test").execute()
        print(f"   increment_ai_usage RPC:      OK")
    except Exception as e:
        print(f"   increment_ai_usage RPC:      FAIL ({e})")

# ─── 8. DATA FLOW VERIFICATION ───────────────────────────
if db:
    print("\n8. DATA FLOW (populated tables)")
    populated = []
    for t in ["price_history", "signals", "metrics_history", "iv_surface_snapshots",
              "conflict_analysis", "api_cache", "ai_response_cache"]:
        try:
            r = db.table(t).select("*", count="exact").limit(1).execute()
            count = r.count if r.count is not None else len(r.data or [])
            if count > 0:
                populated.append((t, count))
        except Exception:
            pass
    for t, c in populated:
        print(f"   {t:30s} {c} rows")
    print(f"   {len(populated)} tables with data flowing")

# ─── 9. FUNCTION EXISTENCE CHECK ─────────────────────────
print("\n9. KEY FUNCTIONS")
checks = [
    ("src.signal_engine", "write_signal"),
    ("src.signal_engine", "compute_composite"),
    ("src.signal_engine", "get_top_trade_ideas"),
    ("src.metrics_store", "save_snapshot"),
    ("src.metrics_store", "percentile_ranks_all"),
    ("src.position_book", "add_position"),
    ("src.position_book", "update_greeks"),
    ("src.position_book", "record_daily_pnl"),
    ("src.prediction_tracker", "record_prediction"),
    ("src.prediction_tracker", "evaluate_pending"),
    ("src.api_cache", "cached_request"),
    ("src.ai_cache", "get_cached_ai"),
    ("src.ai_cache", "cache_ai_response"),
    ("src.user_prefs", "save_pref"),
    ("src.user_prefs", "load_pref"),
    ("src.options_models", "implied_vol"),
    ("src.data_engine", "fetch_options_surface_history"),
]
for mod, func in checks:
    try:
        m = importlib.import_module(mod)
        assert hasattr(m, func), f"missing {func}"
        print(f"   {mod}.{func:30s} OK")
    except Exception as e:
        print(f"   {mod}.{func:30s} FAIL ({e})")

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE")
print("=" * 60)
