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

-- Row-level security: readable by anyone with an auth session, writable only
-- by the service role (the API's backend client uses SUPABASE_KEY which is
-- service_role and bypasses RLS).
ALTER TABLE public.cftc_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cftc_cache_read_all ON public.cftc_cache;
CREATE POLICY cftc_cache_read_all
  ON public.cftc_cache
  FOR SELECT
  TO authenticated, anon
  USING (TRUE);
