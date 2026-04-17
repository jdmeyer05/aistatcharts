"use client";

/**
 * Password-reset landing page. Supabase sends users here after clicking the
 * reset link. The URL carries a short-lived ?code= which we exchange for a
 * recovery session (signs the user in). Then the user picks a new password
 * via `supabase.auth.updateUser({ password })`.
 */
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Image from "next/image";
import { supabaseBrowser, hasSupabaseConfig } from "@/lib/supabase";

function ResetForm() {
  const params = useSearchParams();
  const code = params.get("code");
  const nextPath = params.get("next") || "/";

  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Exchange the recovery code for a session on mount so updateUser works.
  useEffect(() => {
    if (!hasSupabaseConfig()) {
      setError("Supabase isn't configured.");
      return;
    }
    const supabase = supabaseBrowser();
    if (code) {
      supabase.auth.exchangeCodeForSession(code).then(({ error: err }) => {
        if (err) setError(`Reset link is invalid or expired: ${err.message}`);
        else setReady(true);
      });
    } else {
      // No code — user may have navigated here directly. Check if they
      // already have a session (magic-link flow landed here by mistake).
      supabase.auth.getSession().then(({ data }) => {
        if (data.session) setReady(true);
        else setError("No reset code in URL. Request a new reset link from the sign-in page.");
      });
    }
  }, [code]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 6) return setError("Password must be at least 6 characters.");
    if (password !== confirm) return setError("Passwords don't match.");
    setBusy(true);
    try {
      const supabase = supabaseBrowser();
      const { error: err } = await supabase.auth.updateUser({ password });
      if (err) {
        setError(err.message);
        return;
      }
      window.location.assign(nextPath);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg px-4">
      <form onSubmit={handleSubmit} className="card w-full max-w-md space-y-4">
        <div className="flex items-center gap-2 justify-center">
          <Image src="/favicon.png" alt="AI Statcharts" width={28} height={28} className="rounded" />
          <h1 className="text-xl font-bold tracking-widest uppercase">AI Statcharts</h1>
        </div>
        <div className="text-center">
          <div className="text-sm font-semibold">Set a new password</div>
        </div>

        <div>
          <label className="metric-label">New password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={6}
            disabled={!ready}
            autoFocus
            className="mt-0.5 w-full px-3 py-2 border border-border rounded-lg text-sm bg-surface font-data disabled:opacity-50"
          />
        </div>
        <div>
          <label className="metric-label">Confirm</label>
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            minLength={6}
            disabled={!ready}
            className="mt-0.5 w-full px-3 py-2 border border-border rounded-lg text-sm bg-surface font-data disabled:opacity-50"
          />
        </div>

        {error && <p className="text-xs text-loss">{error}</p>}

        <button
          type="submit"
          disabled={busy || !ready || password.length < 6 || password !== confirm}
          className="w-full py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
        >
          {busy ? "Updating…" : "Update password"}
        </button>

        <div className="text-center">
          <a href="/login" className="text-xs text-text-muted hover:text-accent">← Back to sign in</a>
        </div>
      </form>
    </div>
  );
}

export default function ResetPage() {
  return (
    <Suspense fallback={null}>
      <ResetForm />
    </Suspense>
  );
}
