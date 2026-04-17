/**
 * Supabase client factories for Phase 2 auth.
 *
 * Browser usage: `createClient()` returns a Supabase client that stores the
 * session in cookies (via @supabase/ssr) so the middleware and server
 * components can read it.
 *
 * Server usage: use `createServerClient` via @supabase/ssr directly inside
 * middleware / server components (cookies access differs between the app
 * router request handler and middleware).
 */
import { createBrowserClient } from "@supabase/ssr";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export function supabaseBrowser() {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set in .env.local"
    );
  }
  return createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY);
}

/** True when Supabase envs are configured (for conditional UI). */
export function hasSupabaseConfig(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
}
