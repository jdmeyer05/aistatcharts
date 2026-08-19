-- Self-improving prompt loop — storage for the home page's AI surfaces.
--
-- WHAT THIS EXISTS FOR. The three AI blocks on the home page (market-driver,
-- the page-wide interpretation, the ES-card auditor) are driven by long system
-- prompts whose every rule is scar tissue from a failure someone happened to
-- notice by eye. Nothing recorded what the model was shown, nothing scored what
-- it said, and no rule was ever added because a measurement demanded it.
--
-- THE PAYLOAD IS THE ASSET. `ai_snapshots.payload` freezes the exact JSON the
-- model was handed. That single column is what makes the loop honest: a new
-- prompt can be REPLAYED over hundreds of past payloads and scored against
-- outcomes that are already known, instead of being promoted on a hunch and
-- measured over the following month. It is the same trick es_track_record.py
-- uses on the character read — reconstruct history rather than accumulate it.
--
-- Everything here is platform-global, not per-user. Writes come from the API
-- and the worker (service_role); reads are served through FastAPI, so no anon
-- policy is granted.

-- ── Prompt versions ───────────────────────────────────────────────
-- One row per prompt text ever served or evaluated. Version 0 for each surface
-- is the baseline baked into git; the loop only ever appends.
CREATE TABLE IF NOT EXISTS public.prompt_versions (
  id             BIGSERIAL PRIMARY KEY,
  surface        TEXT NOT NULL,
  version        INT NOT NULL,
  body           TEXT NOT NULL,
  body_hash      TEXT NOT NULL,
  parent_version INT,
  -- champion: currently served. challenger: proposed, under evaluation.
  -- retired: was champion, superseded. rejected: lost its experiment.
  status         TEXT NOT NULL DEFAULT 'challenger',
  origin         TEXT NOT NULL DEFAULT 'critic',   -- baseline | critic | human
  rationale      TEXT,
  diff_summary   TEXT,
  metrics        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  promoted_at    TIMESTAMPTZ,
  retired_at     TIMESTAMPTZ,
  UNIQUE (surface, version)
);

CREATE INDEX IF NOT EXISTS prompt_versions_surface_status_idx
  ON public.prompt_versions (surface, status);

-- ── Snapshots: what the model saw and what it said ────────────────
CREATE TABLE IF NOT EXISTS public.ai_snapshots (
  id             BIGSERIAL PRIMARY KEY,
  surface        TEXT NOT NULL,
  prompt_version INT NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  session_phase  TEXT,                              -- rth_open | rth_midday | ...
  model          TEXT,
  payload        JSONB NOT NULL,                    -- frozen model input
  output         JSONB NOT NULL,                    -- what was published
  meta           JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- Replays reuse a live snapshot's payload under a different prompt. They are
  -- never served to anyone and must be excluded from "how did the page do".
  is_replay      BOOLEAN NOT NULL DEFAULT FALSE,
  replay_of      BIGINT REFERENCES public.ai_snapshots(id) ON DELETE SET NULL,
  -- Which half of the record this payload belongs to. Assigned once, at write
  -- time, from a hash of the id — so a challenger tuned on `discovery` can be
  -- scored on `holdout` payloads it never influenced.
  split          TEXT NOT NULL DEFAULT 'discovery'  -- discovery | holdout
);

CREATE INDEX IF NOT EXISTS ai_snapshots_surface_created_idx
  ON public.ai_snapshots (surface, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_snapshots_live_idx
  ON public.ai_snapshots (surface, is_replay, split, created_at DESC);

-- ── Grades: how good was that output, given its own payload ───────
CREATE TABLE IF NOT EXISTS public.ai_grades (
  id          BIGSERIAL PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES public.ai_snapshots(id) ON DELETE CASCADE,
  surface     TEXT NOT NULL,
  grader      TEXT NOT NULL,                        -- rules | adversary
  score       NUMERIC,                              -- 0..1, higher is better
  findings    JSONB NOT NULL DEFAULT '[]'::jsonb,
  counts      JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {critical, major, minor}
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (snapshot_id, grader)
);

CREATE INDEX IF NOT EXISTS ai_grades_surface_created_idx
  ON public.ai_grades (surface, created_at DESC);

-- ── Claims: the falsifiable half ──────────────────────────────────
-- Grounding says the note was faithful to its data. It cannot say the note was
-- RIGHT. These rows can: each is a machine-resolvable statement with a horizon,
-- stated before the fact and settled from price afterwards.
CREATE TABLE IF NOT EXISTS public.ai_claims (
  id          BIGSERIAL PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES public.ai_snapshots(id) ON DELETE CASCADE,
  surface     TEXT NOT NULL,
  claim       JSONB NOT NULL,          -- {subject, op, threshold, horizon_hours, text}
  confidence  NUMERIC,                 -- 0..1, the model's own stated probability
  stated_at   TIMESTAMPTZ NOT NULL,
  resolve_at  TIMESTAMPTZ NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',  -- pending | resolved | unresolvable | expired
  correct     BOOLEAN,
  actual      JSONB,
  -- What the same claim would have scored by chance over the last year of the
  -- same subject. A hit rate with no base rate beside it is not a measurement.
  base_rate   NUMERIC,
  resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ai_claims_pending_idx
  ON public.ai_claims (status, resolve_at);
CREATE INDEX IF NOT EXISTS ai_claims_surface_idx
  ON public.ai_claims (surface, stated_at DESC);

-- ── Experiments: champion vs challenger, and why it was decided ───
CREATE TABLE IF NOT EXISTS public.prompt_experiments (
  id                 BIGSERIAL PRIMARY KEY,
  surface            TEXT NOT NULL,
  champion_version   INT NOT NULL,
  challenger_version INT NOT NULL,
  n_holdout          INT NOT NULL DEFAULT 0,
  metrics            JSONB NOT NULL DEFAULT '{}'::jsonb,
  regression_pass    BOOLEAN NOT NULL DEFAULT FALSE,
  verdict            TEXT NOT NULL DEFAULT 'inconclusive',  -- promote | reject | inconclusive
  promoted           BOOLEAN NOT NULL DEFAULT FALSE,
  notes              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS prompt_experiments_surface_idx
  ON public.prompt_experiments (surface, created_at DESC);

-- ── RLS: server-side only ─────────────────────────────────────────
-- These tables hold the full text of every prompt and the payloads behind them.
-- Nothing here is served straight to a browser — the dashboard reads through
-- FastAPI on the service role — so no anon/authenticated policy is granted.
ALTER TABLE public.prompt_versions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_snapshots        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_grades           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_claims           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.prompt_experiments  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS prompt_versions_service_all ON public.prompt_versions;
CREATE POLICY prompt_versions_service_all ON public.prompt_versions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS ai_snapshots_service_all ON public.ai_snapshots;
CREATE POLICY ai_snapshots_service_all ON public.ai_snapshots
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS ai_grades_service_all ON public.ai_grades;
CREATE POLICY ai_grades_service_all ON public.ai_grades
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS ai_claims_service_all ON public.ai_claims;
CREATE POLICY ai_claims_service_all ON public.ai_claims
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS prompt_experiments_service_all ON public.prompt_experiments;
CREATE POLICY prompt_experiments_service_all ON public.prompt_experiments
    FOR ALL TO service_role USING (true) WITH CHECK (true);
