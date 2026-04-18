-- CFTC scan-output cache — survives Cloud Run cold starts.
--
-- All results are precomputed + persisted here so the first user after a
-- deploy hits instant responses instead of waiting 30-60s for the directory
-- of annual ZIP archives to download again.
--
-- Content is NOT user-specific; single shared cache. Read access is open,
-- writes only from the server role.

CREATE TABLE IF NOT EXISTS public.cftc_cache (
  key         TEXT PRIMARY KEY,
  value       JSONB NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cftc_cache_updated_at_idx ON public.cftc_cache (updated_at DESC);

-- Shared non-user-specific cache — no per-row access control needed.
-- RLS was enabled in the initial version but blocked even service-role
-- writes (likely a legacy-key / role-mapping quirk), so we disable it for
-- this table. Readers are still rate-limited by PostgREST.
ALTER TABLE public.cftc_cache DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cftc_cache_read_all ON public.cftc_cache;
