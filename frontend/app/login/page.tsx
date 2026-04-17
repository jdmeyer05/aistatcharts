"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import { supabaseBrowser, hasSupabaseConfig } from "@/lib/supabase";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const nextPath = params.get("next") || "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);

    if (!hasSupabaseConfig()) {
      setError("Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.");
      return;
    }

    setBusy(true);
    try {
      const supabase = supabaseBrowser();
      const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });
      if (signInError) {
        setError(signInError.message);
        return;
      }
      // The session cookie is set by @supabase/ssr. Hard-navigate so the
      // middleware sees the new cookie on the next request.
      router.replace(nextPath);
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function sendMagicLink() {
    setError(null);
    setInfo(null);
    if (!email) {
      setError("Enter your email above first.");
      return;
    }
    if (!hasSupabaseConfig()) {
      setError("Supabase is not configured.");
      return;
    }
    setBusy(true);
    try {
      const supabase = supabaseBrowser();
      const { error: otpError } = await supabase.auth.signInWithOtp({
        email,
        options: { emailRedirectTo: `${window.location.origin}${nextPath}` },
      });
      if (otpError) setError(otpError.message);
      else setInfo(`Magic link sent to ${email}. Check your inbox.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg px-4">
      <form onSubmit={handleSubmit} className="card w-full max-w-sm space-y-4">
        <div className="flex items-center gap-2 justify-center">
          <Image src="/favicon.png" alt="AI Statcharts" width={28} height={28} className="rounded" />
          <h1 className="text-xl font-bold tracking-widest uppercase">AI Statcharts</h1>
        </div>
        <p className="text-xs text-text-muted text-center">Sign in to continue.</p>

        <div>
          <label className="metric-label">Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            className="mt-0.5 w-full px-3 py-2 border border-border rounded-lg text-sm bg-surface font-data"
          />
        </div>
        <div>
          <label className="metric-label">Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            className="mt-0.5 w-full px-3 py-2 border border-border rounded-lg text-sm bg-surface font-data"
          />
        </div>

        {error && <p className="text-xs text-loss">{error}</p>}
        {info && <p className="text-xs text-gain">{info}</p>}

        <button
          type="submit"
          disabled={busy}
          className="w-full py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <button
          type="button"
          onClick={sendMagicLink}
          disabled={busy}
          className="w-full py-2 border border-border text-text font-semibold rounded-lg hover:bg-surface-alt disabled:opacity-50 text-xs"
        >
          Email me a magic link instead
        </button>

        <p className="text-xs text-text-muted text-center">
          Private site. Contact the admin if you need access.
        </p>
      </form>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
