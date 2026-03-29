-- ═══════════════════════════════════════════════════════════════
-- AI Statcharts — Complete Supabase Schema
-- Run this ONCE in Supabase Dashboard → SQL Editor → New Query
-- ═══════════════════════════════════════════════════════════════


-- ─── 1. SUBSCRIPTIONS (auth/payments) ────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    email TEXT NOT NULL UNIQUE,
    stripe_customer_id TEXT,
    stripe_price_id TEXT,
    status TEXT DEFAULT 'active',
    plan_type TEXT DEFAULT 'free',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_subs_email ON subscriptions (email, status);


-- ─── 2. USER TOKENS (AI analysis credits) ───────────────────
CREATE TABLE IF NOT EXISTS user_tokens (
    email TEXT PRIMARY KEY,
    balance INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Atomic increment function for webhook safety
CREATE OR REPLACE FUNCTION increment_tokens(p_email TEXT, p_amount INTEGER)
RETURNS void AS $$
BEGIN
    INSERT INTO user_tokens (email, balance, updated_at)
    VALUES (p_email, p_amount, NOW())
    ON CONFLICT (email) DO UPDATE
    SET balance = user_tokens.balance + p_amount,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;


-- ─── 3. PAYMENT FAILURES ────────────────────────────────────
CREATE TABLE IF NOT EXISTS payment_failures (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    invoice_id TEXT,
    failed_at TIMESTAMPTZ DEFAULT NOW(),
    resolved BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_pf_email ON payment_failures (email, resolved);


-- ─── 4. SIGNALS (unified signal engine) ─────────────────────
CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT DEFAULT 'default',
    source TEXT NOT NULL,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('bull', 'bear', 'neutral')),
    conviction FLOAT NOT NULL CHECK (conviction >= 0 AND conviction <= 1),
    vol_view TEXT DEFAULT 'neutral' CHECK (vol_view IN ('long_vol', 'short_vol', 'neutral')),
    reasoning TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals (ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_user ON signals (user_id, created_at DESC);


-- ─── 5. METRICS HISTORY (daily vol/options snapshots) ───────
CREATE TABLE IF NOT EXISTS metrics_history (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT DEFAULT 'default',
    ticker TEXT NOT NULL,
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    atm_iv FLOAT,
    put_skew FLOAT,
    vrp FLOAT,
    pc_ratio FLOAT,
    hv20 FLOAT,
    hv60 FLOAT,
    iv_hv_ratio FLOAT,
    ts_slope FLOAT,
    spot FLOAT,
    UNIQUE (user_id, ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_metrics_ticker_date ON metrics_history (ticker, date DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_user ON metrics_history (user_id, ticker);

-- Coverage summary function
CREATE OR REPLACE FUNCTION get_metrics_coverage()
RETURNS TABLE (ticker TEXT, n_days BIGINT, first_date DATE, last_date DATE) AS $$
BEGIN
    RETURN QUERY
    SELECT m.ticker, COUNT(*)::BIGINT, MIN(m.date), MAX(m.date)
    FROM metrics_history m
    GROUP BY m.ticker
    ORDER BY m.ticker;
END;
$$ LANGUAGE plpgsql;


-- ─── 6. POSITIONS (trade book with lifecycle) ───────────────
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    user_id TEXT DEFAULT 'default',
    ticker TEXT NOT NULL,
    type TEXT NOT NULL,
    qty INTEGER NOT NULL,
    entry_price FLOAT NOT NULL,
    entry_date TIMESTAMPTZ DEFAULT NOW(),
    details JSONB DEFAULT '{}',
    source_page TEXT DEFAULT '',
    status TEXT DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    close_price FLOAT,
    close_date TIMESTAMPTZ,
    notes TEXT DEFAULT '',
    greeks JSONB DEFAULT '{}',
    alerts JSONB DEFAULT '{}',
    journal JSONB DEFAULT '{"entry_thesis":"","exit_thesis":"","tags":[],"notes":[]}'
);
CREATE INDEX IF NOT EXISTS idx_positions_user_status ON positions (user_id, status);
CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions (ticker);


-- ─── 7. PNL HISTORY (daily Greek attribution) ───────────────
CREATE TABLE IF NOT EXISTS pnl_history (
    id BIGSERIAL PRIMARY KEY,
    position_id TEXT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    delta_pnl FLOAT DEFAULT 0,
    gamma_pnl FLOAT DEFAULT 0,
    theta_pnl FLOAT DEFAULT 0,
    vega_pnl FLOAT DEFAULT 0,
    total_pnl FLOAT DEFAULT 0,
    spot FLOAT,
    iv FLOAT,
    UNIQUE (position_id, date)
);
CREATE INDEX IF NOT EXISTS idx_pnl_position ON pnl_history (position_id, date DESC);


-- ─── 8. PREDICTIONS (accuracy tracking) ─────────────────────
CREATE TABLE IF NOT EXISTS predictions (
    id TEXT PRIMARY KEY,
    user_id TEXT DEFAULT 'default',
    source TEXT NOT NULL,
    ticker TEXT NOT NULL,
    prediction JSONB NOT NULL,
    spot_at_prediction FLOAT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    outcomes JSONB DEFAULT '{}',
    evaluated_days TEXT[] DEFAULT '{}',
    UNIQUE (user_id, source, ticker, created_at)
);
CREATE INDEX IF NOT EXISTS idx_predictions_ticker ON predictions (ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_eval ON predictions (source, user_id);


-- ─── 9. IV SURFACE SNAPSHOTS (daily chain cache) ────────────
CREATE TABLE IF NOT EXISTS iv_surface_snapshots (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT DEFAULT 'default',
    ticker TEXT NOT NULL,
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    spot FLOAT NOT NULL,
    data JSONB NOT NULL,
    UNIQUE (user_id, ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_surface_ticker_date ON iv_surface_snapshots (ticker, date DESC);


-- ─── 10. CONFLICT ANALYSIS HISTORY ──────────────────────────
CREATE TABLE IF NOT EXISTS conflict_analysis (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT DEFAULT 'default',
    region TEXT DEFAULT 'iran',
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    situation_summary TEXT,
    escalation_risk JSONB DEFAULT '{}',
    models_used TEXT[] DEFAULT '{}',
    latest_developments JSONB DEFAULT '[]',
    infrastructure_status JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_conflict_region ON conflict_analysis (region, timestamp DESC);


-- ─── 11. AI USAGE TRACKING (persistent daily counters) ──────
CREATE TABLE IF NOT EXISTS ai_usage (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    usage_count INTEGER DEFAULT 0,
    chat_count INTEGER DEFAULT 0,
    UNIQUE (user_id, date)
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON ai_usage (user_id, date DESC);

-- Atomic increment for AI usage
CREATE OR REPLACE FUNCTION increment_ai_usage(p_user_id TEXT, p_field TEXT DEFAULT 'usage_count')
RETURNS INTEGER AS $$
DECLARE
    new_count INTEGER;
BEGIN
    INSERT INTO ai_usage (user_id, date, usage_count, chat_count)
    VALUES (p_user_id, CURRENT_DATE,
            CASE WHEN p_field = 'usage_count' THEN 1 ELSE 0 END,
            CASE WHEN p_field = 'chat_count' THEN 1 ELSE 0 END)
    ON CONFLICT (user_id, date) DO UPDATE
    SET usage_count = CASE WHEN p_field = 'usage_count' THEN ai_usage.usage_count + 1 ELSE ai_usage.usage_count END,
        chat_count = CASE WHEN p_field = 'chat_count' THEN ai_usage.chat_count + 1 ELSE ai_usage.chat_count END
    RETURNING CASE WHEN p_field = 'usage_count' THEN usage_count ELSE chat_count END INTO new_count;
    RETURN new_count;
END;
$$ LANGUAGE plpgsql;


-- ─── 12. CHAT HISTORY (persistent conversations) ────────────
CREATE TABLE IF NOT EXISTS chat_history (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    model_used TEXT,
    context_page TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_history (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history (session_id, created_at);


-- ─── 13. SOURCE CREDIBILITY (news/intel source scoring) ─────
CREATE TABLE IF NOT EXISTS source_credibility (
    source_handle TEXT PRIMARY KEY,
    total_citations INTEGER DEFAULT 0,
    total_accuracy_sum INTEGER DEFAULT 0,
    rolling_score INTEGER DEFAULT 50,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);


-- ─── 14. MATERIALIZED VIEW: Pre-computed percentile ranks ────
-- Refreshed on demand. Moves percentile math from Python to Postgres.
CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_percentiles AS
SELECT
    ticker,
    MAX(date) as latest_date,
    MAX(CASE WHEN date = (SELECT MAX(date) FROM metrics_history m2 WHERE m2.ticker = m.ticker) THEN atm_iv END) as current_atm_iv,
    MAX(CASE WHEN date = (SELECT MAX(date) FROM metrics_history m2 WHERE m2.ticker = m.ticker) THEN put_skew END) as current_put_skew,
    MAX(CASE WHEN date = (SELECT MAX(date) FROM metrics_history m2 WHERE m2.ticker = m.ticker) THEN vrp END) as current_vrp,
    MAX(CASE WHEN date = (SELECT MAX(date) FROM metrics_history m2 WHERE m2.ticker = m.ticker) THEN spot END) as current_spot,
    PERCENT_RANK() OVER (PARTITION BY ticker ORDER BY atm_iv) * 100 as atm_iv_pctile,
    PERCENT_RANK() OVER (PARTITION BY ticker ORDER BY put_skew) * 100 as put_skew_pctile,
    PERCENT_RANK() OVER (PARTITION BY ticker ORDER BY vrp) * 100 as vrp_pctile,
    PERCENT_RANK() OVER (PARTITION BY ticker ORDER BY iv_hv_ratio) * 100 as iv_hv_pctile,
    PERCENT_RANK() OVER (PARTITION BY ticker ORDER BY hv20) * 100 as hv20_pctile,
    COUNT(*) OVER (PARTITION BY ticker) as n_days
FROM metrics_history m
WHERE date >= CURRENT_DATE - 252
GROUP BY ticker, atm_iv, put_skew, vrp, iv_hv_ratio, hv20;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mp_ticker ON metrics_percentiles (ticker);

-- Function to refresh the materialized view (call daily or on demand)
CREATE OR REPLACE FUNCTION refresh_percentiles()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY metrics_percentiles;
END;
$$ LANGUAGE plpgsql;


-- ─── 15. VIEW: Signal composites (real-time, no refresh needed) ──
CREATE OR REPLACE VIEW signal_composites AS
WITH weighted AS (
    SELECT
        s.ticker,
        s.direction,
        s.conviction,
        s.vol_view,
        s.source,
        CASE s.source
            WHEN 'ml_predictor' THEN 1.5
            WHEN 'rl_trading' THEN 1.4
            WHEN 'signal_scanner' THEN 1.3
            WHEN 'stock_analysis' THEN 1.2
            WHEN 'scenario_analysis' THEN 1.1
            WHEN 'vol_surface' THEN 1.1
            WHEN 'options_flow' THEN 1.0
            WHEN 'market_expectations' THEN 1.0
            WHEN 'correlation' THEN 0.9
            WHEN 'tech_screener' THEN 0.8
            ELSE 1.0
        END as weight
    FROM signals s
    WHERE s.created_at > NOW() - INTERVAL '24 hours'
)
SELECT
    ticker,
    COUNT(*) as n_signals,
    ROUND(SUM(
        CASE direction
            WHEN 'bull' THEN conviction * weight
            WHEN 'bear' THEN -conviction * weight
            ELSE 0
        END
    )::numeric / NULLIF(SUM(conviction * weight), 0)::numeric, 3) as direction_score,
    CASE
        WHEN SUM(CASE direction WHEN 'bull' THEN conviction * weight WHEN 'bear' THEN -conviction * weight ELSE 0 END)
             / NULLIF(SUM(conviction * weight), 0) > 0.2 THEN 'bull'
        WHEN SUM(CASE direction WHEN 'bull' THEN conviction * weight WHEN 'bear' THEN -conviction * weight ELSE 0 END)
             / NULLIF(SUM(conviction * weight), 0) < -0.2 THEN 'bear'
        ELSE 'neutral'
    END as overall_direction,
    ROUND(AVG(conviction)::numeric, 2) as avg_conviction
FROM weighted
GROUP BY ticker
HAVING COUNT(*) >= 2;


-- ─── 16. Auto-cleanup: delete old signals (>7 days) ──────────
CREATE OR REPLACE FUNCTION cleanup_old_signals()
RETURNS void AS $$
BEGIN
    DELETE FROM signals WHERE created_at < NOW() - INTERVAL '7 days';
    DELETE FROM chat_history WHERE created_at < NOW() - INTERVAL '90 days';
END;
$$ LANGUAGE plpgsql;


-- ─── 17. CONFLICT TIMELINE EVENTS (auto-updated by Grok) ────
CREATE TABLE IF NOT EXISTS conflict_timeline (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    event TEXT NOT NULL,
    category TEXT DEFAULT 'Military',
    impact TEXT DEFAULT '',
    infrastructure TEXT DEFAULT '',
    source TEXT DEFAULT 'grok_auto',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (date, event)
);
CREATE INDEX IF NOT EXISTS idx_timeline_date ON conflict_timeline (date DESC);


-- ─── 18. PRICE HISTORY (fetch once, append daily) ───────────
CREATE TABLE IF NOT EXISTS price_history (
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    close FLOAT NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_ph_ticker ON price_history (ticker, date DESC);


-- ─── 19. AI RESPONSE CACHE ───────────────────────────────────
-- Caches AI model responses keyed by input hash.
-- Same input = same output. Shared across users. TTL-based expiry.
CREATE TABLE IF NOT EXISTS ai_response_cache (
    input_hash TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    source_page TEXT NOT NULL,
    ticker TEXT,
    prompt_summary TEXT,
    response TEXT NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    cost_estimate FLOAT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '2 hours'
);
CREATE INDEX IF NOT EXISTS idx_ai_cache_ticker ON ai_response_cache (ticker, source_page);
CREATE INDEX IF NOT EXISTS idx_ai_cache_expires ON ai_response_cache (expires_at);

-- Cleanup expired AI cache
CREATE OR REPLACE FUNCTION cleanup_expired_ai_cache()
RETURNS void AS $$
BEGIN
    DELETE FROM ai_response_cache WHERE expires_at < NOW();
END;
$$ LANGUAGE plpgsql;


-- ─── 20. API CACHE (Polygon response caching layer) ─────────
-- Replaces Edge Function approach — cache API responses in Postgres.
-- Python checks cache before hitting Polygon. ~100ms vs ~1-2s.
CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    response JSONB NOT NULL,
    endpoint TEXT NOT NULL,
    symbol TEXT,
    ttl_seconds INTEGER DEFAULT 300,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '5 minutes'
);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON api_cache (expires_at);
CREATE INDEX IF NOT EXISTS idx_cache_symbol ON api_cache (symbol, endpoint);

-- Auto-cleanup expired cache entries
CREATE OR REPLACE FUNCTION cleanup_expired_cache()
RETURNS void AS $$
BEGIN
    DELETE FROM api_cache WHERE expires_at < NOW();
END;
$$ LANGUAGE plpgsql;


-- ═══════════════════════════════════════════════════════════════
-- DONE. 14 tables + 2 views + 6 RPC functions created.
-- ═══════════════════════════════════════════════════════════════
