-- ═══════════════════════════════════════════════════════════════
-- Trump Decoder — Supabase Schema Migration
-- Run in Supabase Dashboard → SQL Editor → New Query
-- ═══════════════════════════════════════════════════════════════


-- ─── 1. PSYCH PROFILE (cached Claude Opus deep analysis) ────
CREATE TABLE IF NOT EXISTS trump_psych_profile (
    id BIGSERIAL PRIMARY KEY,
    profile_version INTEGER NOT NULL DEFAULT 1,
    model TEXT NOT NULL DEFAULT 'claude-opus-4-6',
    -- Core psychological assessment
    mbti TEXT,                          -- e.g. "ESTP"
    big_five JSONB DEFAULT '{}',        -- openness, conscientiousness, extraversion, agreeableness, neuroticism
    dark_triad JSONB DEFAULT '{}',      -- narcissism, machiavellianism, psychopathy scores
    negotiation_style JSONB DEFAULT '{}', -- tactics, patterns, tells
    bluff_patterns JSONB DEFAULT '[]',  -- array of documented bluffing patterns
    escalation_tells JSONB DEFAULT '[]', -- signals that indicate genuine vs performative escalation
    deescalation_tells JSONB DEFAULT '[]',
    known_triggers JSONB DEFAULT '[]',  -- topics/events that provoke strong reactions
    communication_patterns JSONB DEFAULT '{}', -- word choice, timing, platform preferences
    -- Full narrative profile
    full_profile TEXT NOT NULL,         -- complete markdown narrative from Claude
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '30 days'
);
CREATE INDEX IF NOT EXISTS idx_trump_psych_version ON trump_psych_profile (profile_version DESC);


-- ─── 2. DECODED STATEMENTS (every decode you run) ───────────
CREATE TABLE IF NOT EXISTS trump_decoded_statements (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    -- Input
    statement TEXT NOT NULL,            -- the raw Trump quote/tweet
    user_context TEXT DEFAULT '',       -- optional context the user provided
    -- AI Analysis Results
    decoded_meaning TEXT,
    bluff_score INTEGER CHECK (bluff_score >= 0 AND bluff_score <= 100),
    bluff_label TEXT,                   -- "Likely Bluff" / "Genuine Intent" / "Uncertain"
    market_impact FLOAT,               -- -5 to +5
    market_impact_label TEXT,           -- "Strongly Bearish" to "Strongly Bullish"
    probability_distribution JSONB DEFAULT '{}',  -- {deal: 0.65, escalation: 0.20, ...}
    historical_analogs JSONB DEFAULT '[]',        -- [{date, statement, outcome, market_reaction}]
    affected_sectors JSONB DEFAULT '[]',
    affected_tickers JSONB DEFAULT '[]',
    position_risks JSONB DEFAULT '[]',            -- [{ticker, type, risk_level, recommendation}]
    mood_index JSONB DEFAULT '{}',                -- {posting_freq, sentiment, escalation_level}
    narrative TEXT,                                -- full markdown reasoning
    -- Model attribution
    model_sources JSONB DEFAULT '{}',             -- {grok: "...", claude: "...", gemini: "..."}
    -- Outcome tracking (user fills in later)
    actual_outcome TEXT,                           -- what actually happened
    outcome_date TIMESTAMPTZ,
    outcome_market_move FLOAT,                     -- actual SPY % move
    was_accurate BOOLEAN,                          -- user marks whether analysis was right
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trump_decoded_user ON trump_decoded_statements (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trump_decoded_bluff ON trump_decoded_statements (bluff_score DESC);


-- ─── 3. PATTERN HISTORY (Grok-built historical cycles) ─────
CREATE TABLE IF NOT EXISTS trump_pattern_history (
    id BIGSERIAL PRIMARY KEY,
    -- Pattern identification
    category TEXT NOT NULL,             -- tariffs, china, fed, iran, trade_war, executive_order, etc.
    date_range TEXT,                    -- "Aug 1-15, 2019"
    trigger_statement TEXT,             -- what Trump said/did to start the cycle
    -- Cycle tracking
    escalation_path JSONB DEFAULT '[]', -- [{date, event, market_reaction}]
    resolution TEXT,                    -- how it ended
    resolution_type TEXT,               -- deal, walkback, follow_through, ongoing
    days_to_resolution INTEGER,
    -- Market impact
    market_impact_summary TEXT,
    spy_move_pct FLOAT,                -- total SPY move during cycle
    vix_peak FLOAT,
    most_affected_sectors JSONB DEFAULT '[]',
    -- Pattern classification
    pattern_type TEXT,                  -- bluff_cycle, genuine_policy, negotiation_tactic, distraction
    bluff_score INTEGER,               -- 0-100 in hindsight
    -- Source
    source_model TEXT DEFAULT 'grok-4',
    search_query TEXT,                  -- what Grok searched for
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (category, trigger_statement)
);
CREATE INDEX IF NOT EXISTS idx_trump_pattern_cat ON trump_pattern_history (category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trump_pattern_type ON trump_pattern_history (pattern_type);


-- ─── 4. MONITOR POSTS (archived Trump posts) ───────────────
CREATE TABLE IF NOT EXISTS trump_monitor_posts (
    id BIGSERIAL PRIMARY KEY,
    -- Post content
    post_text TEXT NOT NULL,
    platform TEXT DEFAULT 'truth_social', -- truth_social, x, press_conference, interview
    post_timestamp TIMESTAMPTZ,          -- when Trump posted it
    -- AI interpretation
    interpretation TEXT,                  -- Grok's quick interpretation
    market_relevance INTEGER CHECK (market_relevance >= 0 AND market_relevance <= 10),
    category TEXT,                        -- tariffs, fed, trade, military, domestic, personal
    sentiment TEXT,                       -- aggressive, conciliatory, boastful, threatening, neutral
    -- Market context at time of post
    spy_price FLOAT,
    vix_level FLOAT,
    -- Metadata
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    source_model TEXT DEFAULT 'grok-4',
    UNIQUE (post_text, post_timestamp)
);
CREATE INDEX IF NOT EXISTS idx_trump_posts_time ON trump_monitor_posts (post_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_trump_posts_relevance ON trump_monitor_posts (market_relevance DESC);
CREATE INDEX IF NOT EXISTS idx_trump_posts_category ON trump_monitor_posts (category, post_timestamp DESC);
