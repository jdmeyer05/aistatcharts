"use client";

/**
 * Blocking modal that forces the signed-in user to accept the current Terms
 * if their stored `user_metadata.tos_accepted_version` is missing or older
 * than `CURRENT_TOS_VERSION`. Users signed up before this system existed
 * see it once; thereafter only when the version is bumped.
 *
 * Deliberately non-dismissable — the only exits are "I agree" (records the
 * new version) or "Sign out". Standard click-through compliance pattern.
 *
 * Exempt routes: /login, /auth/*, /disclaimer. These need to remain usable
 * either because the user isn't logged in yet or because they're reading
 * the very terms we're gating on.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { supabaseBrowser, hasSupabaseConfig } from "@/lib/supabase";
import { hasAcceptedCurrentTos, tosAcceptancePayload } from "@/lib/tos";

const EXEMPT_PREFIXES = ["/login", "/auth", "/disclaimer"];

export function TosGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);
  const [needsAccept, setNeedsAccept] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const exempt = EXEMPT_PREFIXES.some((p) => pathname === p || pathname.startsWith(p + "/"));

  useEffect(() => {
    if (exempt || !hasSupabaseConfig()) {
      setChecked(true);
      return;
    }
    const supabase = supabaseBrowser();
    supabase.auth
      .getUser()
      .then(({ data }) => {
        const user = data.user;
        // Not logged in — nothing to gate; the proxy will redirect anyway.
        if (!user) {
          setNeedsAccept(false);
        } else {
          setNeedsAccept(!hasAcceptedCurrentTos(user));
        }
      })
      .catch(() => {
        // Supabase unreachable — fail open (don't block the app) rather
        // than trap users behind a modal they can't dismiss.
        setNeedsAccept(false);
      })
      .finally(() => setChecked(true));
  }, [exempt, pathname]);

  async function accept() {
    setErr(null);
    setBusy(true);
    try {
      const supabase = supabaseBrowser();
      const { error } = await supabase.auth.updateUser({ data: tosAcceptancePayload() });
      if (error) {
        setErr(error.message);
        return;
      }
      setNeedsAccept(false);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    try {
      const supabase = supabaseBrowser();
      await supabase.auth.signOut();
    } finally {
      window.location.assign("/login");
    }
  }

  // Render children with the modal overlaid. Children stay mounted so the
  // route doesn't unmount/remount when the modal opens/closes — but the
  // backdrop swallows pointer events, so they can't be interacted with.
  return (
    <>
      {children}
      {checked && needsAccept && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="tos-gate-title"
          className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
        >
          <div className="card w-full max-w-lg space-y-4 border border-accent/40">
            <h2 id="tos-gate-title" className="text-lg font-bold">Terms updated</h2>
            <div className="text-sm text-text leading-relaxed space-y-3">
              <p>
                To keep using AI Statcharts, please review and accept our updated{" "}
                <Link href="/disclaimer" target="_blank" rel="noopener" className="text-accent hover:underline">
                  Disclaimer &amp; Terms
                </Link>
                .
              </p>
              <p className="text-xs text-text-muted">
                Summary: this is an analytical research tool, not investment
                advice. AI outputs are probabilistic, third-party data can be
                incorrect, and you make your own trading decisions. Full terms
                at the link above.
              </p>
            </div>
            {err && <p className="text-xs text-loss">{err}</p>}
            <div className="flex gap-2">
              <button
                onClick={accept}
                disabled={busy}
                className="flex-1 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
              >
                {busy ? "Saving…" : "I agree"}
              </button>
              <button
                onClick={signOut}
                className="px-4 py-2 border border-border text-text font-semibold rounded-lg hover:bg-surface-alt text-sm"
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
