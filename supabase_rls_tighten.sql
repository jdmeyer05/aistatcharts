-- ═══════════════════════════════════════════════════════════════
-- AI Statcharts — RLS Tightening Migration
-- Run in Supabase Dashboard → SQL Editor → New Query
--
-- DO NOT RUN UNTIL:
--   1. `SUPABASE_SERVICE_ROLE_KEY` is set in GCP Secret Manager
--      (scripts/create_gcp_secrets.py registers it as `supabase-service-role-key`)
--   2. FastAPI service redeployed with the secret mounted
--      (scripts/deploy_api.ps1 mounts it as the SUPABASE_SERVICE_ROLE_KEY env var)
--   3. `/api/health` returns 200 and a logged-in user can still load /positions
--
-- Running this before the backend has service_role access will 403 every
-- user-data query — the app stops working.
--
-- What this migration does:
--   - Drops the open `anon_full_access` / `anon_read` policies on tables
--     that hold per-user data (user data + payment data).
--   - Replaces them with service_role-only policies. The backend writes
--     through the service_role key (which bypasses RLS); the anon key in
--     every browser now gets nothing from these tables.
--   - Leaves shared non-user caches (cftc_cache, api_cache, price_history,
--     etc.) unchanged — those are fine for anon access.
--
-- Rollback (per-table): drop the service_role policy and recreate the old
-- anon policy — see "ROLLBACK" block at the bottom.
-- ═══════════════════════════════════════════════════════════════


-- ─── 1. PAYMENT / BILLING TABLES ────────────────────────────────
-- Currently: `anon_read USING (true)` — any browser with the anon key can
-- enumerate every user's Stripe customer id, tier, token balance.

-- subscriptions
DROP POLICY IF EXISTS "anon_read" ON subscriptions;
DROP POLICY IF EXISTS "service_write" ON subscriptions;
DROP POLICY IF EXISTS "service_update" ON subscriptions;
DROP POLICY IF EXISTS "service_delete" ON subscriptions;
CREATE POLICY "service_role_all" ON subscriptions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- user_tokens
DROP POLICY IF EXISTS "anon_read" ON user_tokens;
DROP POLICY IF EXISTS "service_write" ON user_tokens;
DROP POLICY IF EXISTS "service_update" ON user_tokens;
DROP POLICY IF EXISTS "service_delete" ON user_tokens;
CREATE POLICY "service_role_all" ON user_tokens
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- payment_failures
DROP POLICY IF EXISTS "anon_read" ON payment_failures;
DROP POLICY IF EXISTS "service_write" ON payment_failures;
DROP POLICY IF EXISTS "service_update" ON payment_failures;
DROP POLICY IF EXISTS "service_delete" ON payment_failures;
CREATE POLICY "service_role_all" ON payment_failures
    FOR ALL TO service_role USING (true) WITH CHECK (true);


-- ─── 2. USER DATA TABLES ────────────────────────────────────────
-- Currently: `anon_full_access USING (true) WITH CHECK (true)` — any
-- browser can read and MODIFY every other user's data via the anon key.
-- This is the largest live data-exposure surface.

-- ai_usage (per-user AI call counters)
DROP POLICY IF EXISTS "anon_full_access" ON ai_usage;
CREATE POLICY "service_role_all" ON ai_usage
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- chat_history (conversation logs — contains user prompts)
DROP POLICY IF EXISTS "anon_full_access" ON chat_history;
CREATE POLICY "service_role_all" ON chat_history
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- positions (live trade book)
DROP POLICY IF EXISTS "anon_full_access" ON positions;
CREATE POLICY "service_role_all" ON positions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- pnl_history (per-position P&L attribution)
DROP POLICY IF EXISTS "anon_full_access" ON pnl_history;
CREATE POLICY "service_role_all" ON pnl_history
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- predictions (accuracy tracking, per-user prediction log)
DROP POLICY IF EXISTS "anon_full_access" ON predictions;
CREATE POLICY "service_role_all" ON predictions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- user_preferences (watchlist, settings)
DROP POLICY IF EXISTS "anon_full_access" ON user_preferences;
CREATE POLICY "service_role_all" ON user_preferences
    FOR ALL TO service_role USING (true) WITH CHECK (true);


-- ─── NOT TOUCHED (intentionally) ────────────────────────────────
-- Shared non-user tables — anon access is fine, they're world-shared caches
-- or reference data:
--   cftc_cache, api_cache, price_history, ai_response_cache
--   metrics_history, iv_surface_snapshots, signals
--   source_credibility, conflict_analysis, conflict_timeline
--
-- Tables with their own per-user RLS already:
--   user_alerts (supabase_alerts_schema.sql — auth.uid() = user_id)
--   user_alert_firings (supabase_alert_firings_schema.sql — same pattern)
--   options_oi_history, options_oi_universe


-- ═══════════════════════════════════════════════════════════════
-- VERIFY after running:
--   1. /api/health returns 200
--   2. Log in to the site, visit /positions, /trump-decoder, /alerts — each
--      loads successfully
--   3. In browser devtools console:
--        const c = createClient(URL, ANON_KEY);
--        (await c.from('positions').select('*')).data  // should be [] or error
--      The anon key now reads zero rows from any of these tables.
--
-- ROLLBACK (single table, paste then adjust per table name):
--   DROP POLICY "service_role_all" ON <table>;
--   CREATE POLICY "anon_full_access" ON <table>
--       FOR ALL USING (true) WITH CHECK (true);
--
-- FULL ROLLBACK (restore pre-migration state — re-runs the original
-- supabase_rls_migration.sql policies): paste the drop+create-anon block
-- for each table above into the SQL editor.
-- ═══════════════════════════════════════════════════════════════
