/**
 * Terms-of-service acceptance tracking.
 *
 * Bumped whenever the disclaimer changes materially (scope of use, liability,
 * privacy, arbitration, governing law). Typo fixes don't bump.
 *
 * Acceptance is recorded in Supabase `auth.users.user_metadata` — no new
 * table, no RLS work. Clients read it off the standard session user object.
 */

export const CURRENT_TOS_VERSION = 1;

interface MaybeUser {
  user_metadata?: {
    tos_accepted_version?: number;
    tos_accepted_at?: string;
  } | null;
}

export function hasAcceptedCurrentTos(user: MaybeUser | null | undefined): boolean {
  if (!user) return false;
  const v = user.user_metadata?.tos_accepted_version;
  return typeof v === "number" && v >= CURRENT_TOS_VERSION;
}

/** Build the metadata payload stamped at acceptance time. */
export function tosAcceptancePayload() {
  return {
    tos_accepted_version: CURRENT_TOS_VERSION,
    tos_accepted_at: new Date().toISOString(),
  };
}
