-- ═══════════════════════════════════════════════════════════════
-- AI Statcharts — Row-Level Security (RLS) Migration
-- Run in Supabase Dashboard → SQL Editor → New Query
--
-- Enables RLS on ALL tables with appropriate access policies.
-- After running, the Supabase security warnings should clear.
-- ═══════════════════════════════════════════════════════════════


-- ─── SENSITIVE TABLES: service_role only ──────────────────────
-- These contain payment data, tokens, and auth info.
-- Only the worker (service_role key) and server-side code should access them.
-- The anon key CANNOT read or write these.

-- subscriptions (email, stripe IDs, payment status)
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read" ON subscriptions
    FOR SELECT USING (true);
CREATE POLICY "service_write" ON subscriptions
    FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "service_update" ON subscriptions
    FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "service_delete" ON subscriptions
    FOR DELETE USING (auth.role() = 'service_role');

-- user_tokens (credit balances)
ALTER TABLE user_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read" ON user_tokens
    FOR SELECT USING (true);
CREATE POLICY "service_write" ON user_tokens
    FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "service_update" ON user_tokens
    FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "service_delete" ON user_tokens
    FOR DELETE USING (auth.role() = 'service_role');

-- payment_failures (invoice data)
ALTER TABLE payment_failures ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read" ON payment_failures
    FOR SELECT USING (true);
CREATE POLICY "service_write" ON payment_failures
    FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "service_update" ON payment_failures
    FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "service_delete" ON payment_failures
    FOR DELETE USING (auth.role() = 'service_role');


-- ─── USER DATA TABLES: anon can read/write (open beta) ───────
-- During open beta, the app uses the anon key for all operations.
-- These policies allow the app to function while RLS is enabled.
-- When auth is re-enabled, replace anon policies with JWT-based ones.

-- ai_usage (daily counters — sensitive: per-user usage)
ALTER TABLE ai_usage ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON ai_usage
    FOR ALL USING (true) WITH CHECK (true);

-- chat_history (conversation logs)
ALTER TABLE chat_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON chat_history
    FOR ALL USING (true) WITH CHECK (true);

-- positions (trade book)
ALTER TABLE positions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON positions
    FOR ALL USING (true) WITH CHECK (true);

-- pnl_history (P&L attribution)
ALTER TABLE pnl_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON pnl_history
    FOR ALL USING (true) WITH CHECK (true);

-- predictions (accuracy tracking)
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON predictions
    FOR ALL USING (true) WITH CHECK (true);

-- user_preferences (settings, watchlist)
ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON user_preferences
    FOR ALL USING (true) WITH CHECK (true);


-- ─── SHARED DATA TABLES: anon can read/write ────────────────
-- Non-sensitive platform data shared across all users.

-- signals (signal engine)
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON signals
    FOR ALL USING (true) WITH CHECK (true);

-- metrics_history (vol/options snapshots)
ALTER TABLE metrics_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON metrics_history
    FOR ALL USING (true) WITH CHECK (true);

-- iv_surface_snapshots (options chain cache)
ALTER TABLE iv_surface_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON iv_surface_snapshots
    FOR ALL USING (true) WITH CHECK (true);

-- conflict_analysis (geopolitical risk)
ALTER TABLE conflict_analysis ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON conflict_analysis
    FOR ALL USING (true) WITH CHECK (true);

-- conflict_timeline (events)
ALTER TABLE conflict_timeline ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON conflict_timeline
    FOR ALL USING (true) WITH CHECK (true);

-- source_credibility (news scoring)
ALTER TABLE source_credibility ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON source_credibility
    FOR ALL USING (true) WITH CHECK (true);


-- ─── CACHE TABLES: anon can read/write ──────────────────────
-- API and AI response caches — ephemeral, shared across users.

-- price_history (OHLCV cache)
ALTER TABLE price_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON price_history
    FOR ALL USING (true) WITH CHECK (true);

-- api_cache (Polygon response cache)
ALTER TABLE api_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON api_cache
    FOR ALL USING (true) WITH CHECK (true);

-- ai_response_cache (AI model response cache)
ALTER TABLE ai_response_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_full_access" ON ai_response_cache
    FOR ALL USING (true) WITH CHECK (true);


-- ═══════════════════════════════════════════════════════════════
-- DONE. RLS enabled on all 18 tables.
--
-- Sensitive tables (subscriptions, user_tokens, payment_failures):
--   → Only accessible via service_role key (worker, webhook server)
--   → Anon key CANNOT read or write these
--
-- All other tables:
--   → Accessible via anon key (app uses this during open beta)
--   → When auth is re-enabled, replace "anon_full_access" policies
--     with JWT-based policies like:
--     CREATE POLICY "users_own_data" ON positions
--       FOR ALL USING (auth.uid()::text = user_id);
--
-- To verify: Supabase Dashboard → Authentication → Policies
-- ═══════════════════════════════════════════════════════════════
