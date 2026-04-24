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
-- RLS is enabled with permissive per-role policies. Earlier attempt
-- relied on auth.role() = 'service_role' inside USING (which is the
-- source of the legacy-key quirk); granting TO service_role / anon
-- explicitly is the supported Supabase pattern and works reliably.
ALTER TABLE public.cftc_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cftc_cache_service_all ON public.cftc_cache;
CREATE POLICY cftc_cache_service_all ON public.cftc_cache
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS cftc_cache_anon_all ON public.cftc_cache;
CREATE POLICY cftc_cache_anon_all ON public.cftc_cache
    FOR ALL TO anon, authenticated
    USING (true) WITH CHECK (true);
