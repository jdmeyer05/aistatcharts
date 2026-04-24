-- ═══════════════════════════════════════════════════════════════
-- AI Statcharts — Fix "RLS Disabled in Public" for public.cftc_cache
-- Run in Supabase Dashboard → SQL Editor → New Query
--
-- Shared non-user cache — policies are permissive so the app keeps
-- working exactly as today. The earlier attempt (see comment in
-- supabase_cftc_cache_schema.sql) failed because it relied on
-- auth.role() = 'service_role'. This version grants per-role
-- explicitly, which is the supported Supabase pattern.
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE public.cftc_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cftc_cache_service_all ON public.cftc_cache;
CREATE POLICY cftc_cache_service_all ON public.cftc_cache
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS cftc_cache_anon_all ON public.cftc_cache;
CREATE POLICY cftc_cache_anon_all ON public.cftc_cache
    FOR ALL TO anon, authenticated
    USING (true) WITH CHECK (true);

-- After running:
--   Supabase Dashboard → Advisors → Security → refresh
--   "RLS Disabled in Public" for cftc_cache should clear.
--
-- Also update the committed schema file so fresh deploys don't
-- re-introduce the lint:
--   supabase_cftc_cache_schema.sql line 22 says DISABLE — change
--   it to ENABLE + the two policies above.
