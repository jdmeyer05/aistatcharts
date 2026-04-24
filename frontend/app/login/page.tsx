"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import { supabaseBrowser, hasSupabaseConfig, safeRedirectPath } from "@/lib/supabase";
import { tosAcceptancePayload } from "@/lib/tos";

type Mode = "signin" | "signup" | "reset";

/** Maps Supabase error messages to user-friendly copy. */
function friendlyError(msg: string): string {
  const lower = msg.toLowerCase();
  if (lower.includes("error sending") && (lower.includes("email") || lower.includes("magic"))) {
    return "Email delivery isn't configured for this project. Ask the admin to reset your password in the Supabase dashboard, or sign in with password.";
  }
  if (lower.includes("invalid login credentials")) return "Wrong email or password.";
  if (lower.includes("user already registered")) return "An account with this email already exists. Use sign in instead.";
  if (lower.includes("email not confirmed")) return "Your email isn't confirmed yet. Ask the admin to confirm you in the Supabase dashboard.";
  if (lower.includes("weak password") || lower.includes("password should be")) return "Password too weak — use at least 6 characters.";
  if (lower.includes("email rate limit")) return "Too many requests. Wait a minute and try again.";
  if (lower.includes("database error creating new user")) return "Sign-up is currently disabled on the server side. Ask the admin to enable it.";
  return msg;
}

function AuthForm() {
  const params = useSearchParams();
  const nextPath = safeRedirectPath(params.get("next"));
  const callbackError = params.get("error");

  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [tosAccepted, setTosAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const canSubmit =
    email.length > 3 &&
    (mode === "reset" ? true : password.length >= 6) &&
    (mode !== "signup" || (password === confirm && tosAccepted));

  function resetState() {
    setError(null);
    setInfo(null);
  }
  function switchMode(m: Mode) {
    resetState();
    setMode(m);
    setPassword("");
    setConfirm("");
    setTosAccepted(false);
  }

  async function handleSignIn() {
    if (!hasSupabaseConfig()) return setError("Supabase isn't configured. Check NEXT_PUBLIC_SUPABASE_URL / _ANON_KEY.");
    setBusy(true);
    try {
      const supabase = supabaseBrowser();
      const { error: err } = await supabase.auth.signInWithPassword({ email, password });
      if (err) setError(friendlyError(err.message));
      else window.location.assign(nextPath);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleSignUp() {
    if (!hasSupabaseConfig()) return setError("Supabase isn't configured.");
    if (password !== confirm) return setError("Passwords don't match.");
    setBusy(true);
    try {
      const supabase = supabaseBrowser();
      const { data, error: err } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(nextPath)}`,
          // Stamp ToS acceptance into user_metadata at creation time so it
          // sticks even if email confirmation delays first sign-in (the user
          // isn't yet a logged-in session we could updateUser() against).
          data: tosAcceptancePayload(),
        },
      });
      if (err) {
        setError(friendlyError(err.message));
        return;
      }
      // If email confirmation is disabled in Supabase settings, the session is
      // returned immediately and the user is effectively signed in.
      if (data.session) {
        window.location.assign(nextPath);
        return;
      }
      // Otherwise Supabase queued a confirmation email. On projects without
      // SMTP, the email never arrives — surface the admin-assist path.
      // Switch tab first (clears state) then set the message.
      switchMode("signin");
      setInfo(
        `Account created for ${email}. Confirm your email (check inbox) before signing in. ` +
        `If no email arrives, ask the admin to confirm you in the Supabase dashboard.`
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    if (!hasSupabaseConfig()) return setError("Supabase isn't configured.");
    setBusy(true);
    try {
      const supabase = supabaseBrowser();
      const { error: err } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth/reset?next=${encodeURIComponent(nextPath)}`,
      });
      if (err) setError(friendlyError(err.message));
      else setInfo(`If an account exists for ${email}, a password-reset link has been sent.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleMagicLink() {
    if (!email) return setError("Enter your email first.");
    if (!hasSupabaseConfig()) return setError("Supabase isn't configured.");
    setBusy(true);
    try {
      const supabase = supabaseBrowser();
      const { error: err } = await supabase.auth.signInWithOtp({
        email,
        options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(nextPath)}` },
      });
      if (err) setError(friendlyError(err.message));
      else setInfo(`Magic link sent to ${email}. Check your inbox.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    resetState();
    if (!canSubmit) return;
    if (mode === "signin") return handleSignIn();
    if (mode === "signup") return handleSignUp();
    if (mode === "reset") return handleReset();
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg px-4">
      <form onSubmit={handleSubmit} className="card w-full max-w-md space-y-4">
        {/* Header */}
        <div className="flex items-center gap-2 justify-center">
          <Image src="/favicon.png" alt="AI Statcharts" width={28} height={28} className="rounded" />
          <h1 className="text-xl font-bold tracking-widest uppercase">AI Statcharts</h1>
        </div>

        {/* Tabs */}
        {mode !== "reset" && (
          <div className="flex gap-1 border-b border-border">
            <button
              type="button"
              onClick={() => switchMode("signin")}
              className={`flex-1 py-2 text-xs font-semibold uppercase tracking-wider transition-colors ${
                mode === "signin" ? "border-b-2 border-accent text-accent" : "text-text-muted hover:text-text"
              }`}
            >
              Sign in
            </button>
            <button
              type="button"
              onClick={() => switchMode("signup")}
              className={`flex-1 py-2 text-xs font-semibold uppercase tracking-wider transition-colors ${
                mode === "signup" ? "border-b-2 border-accent text-accent" : "text-text-muted hover:text-text"
              }`}
            >
              Create account
            </button>
          </div>
        )}

        {mode === "reset" && (
          <div className="text-center">
            <div className="text-sm font-semibold">Reset password</div>
            <div className="text-xs text-text-muted mt-1">Enter your email — we&apos;ll send a reset link.</div>
          </div>
        )}

        {/* Email */}
        <div>
          <label className="metric-label">Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            autoFocus
            className="mt-0.5 w-full px-3 py-2 border border-border rounded-lg text-sm bg-surface font-data"
          />
        </div>

        {/* Password (sign in + sign up) */}
        {mode !== "reset" && (
          <div>
            <div className="flex items-center justify-between">
              <label className="metric-label">Password</label>
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="text-[0.65rem] text-text-muted hover:text-accent uppercase tracking-wider"
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
            <input
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              minLength={mode === "signup" ? 6 : undefined}
              className="mt-0.5 w-full px-3 py-2 border border-border rounded-lg text-sm bg-surface font-data"
            />
            {mode === "signup" && password.length > 0 && password.length < 6 && (
              <div className="text-[0.65rem] text-spot mt-1">At least 6 characters.</div>
            )}
          </div>
        )}

        {/* Confirm password (sign up only) */}
        {mode === "signup" && (
          <div>
            <label className="metric-label">Confirm password</label>
            <input
              type={showPassword ? "text" : "password"}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              minLength={6}
              className="mt-0.5 w-full px-3 py-2 border border-border rounded-lg text-sm bg-surface font-data"
            />
            {confirm.length > 0 && password !== confirm && (
              <div className="text-[0.65rem] text-loss mt-1">Passwords don&apos;t match.</div>
            )}
          </div>
        )}

        {/* Messages */}
        {!error && callbackError && (
          <p className="text-xs text-loss">
            {callbackError === "missing_code"
              ? "Your login link is missing the verification code. Try again."
              : `Sign-in link failed: ${friendlyError(callbackError)}`}
          </p>
        )}
        {error && <p className="text-xs text-loss">{error}</p>}
        {info && <p className="text-xs text-gain">{info}</p>}

        {/* ToS acceptance — signup only. Placed above submit so the checkbox
            is visible when the button appears disabled. */}
        {mode === "signup" && (
          <label className="flex items-start gap-2 text-[0.7rem] text-text leading-relaxed cursor-pointer select-none">
            <input
              type="checkbox"
              checked={tosAccepted}
              onChange={(e) => setTosAccepted(e.target.checked)}
              className="mt-0.5 w-4 h-4 accent-accent cursor-pointer shrink-0"
              aria-describedby="tos-notice"
            />
            <span id="tos-notice">
              I have read and agree to the{" "}
              <Link href="/disclaimer" target="_blank" rel="noopener" className="text-accent hover:underline">
                Disclaimer &amp; Terms
              </Link>
              . I understand AI Statcharts is a research tool, not investment advice.
            </span>
          </label>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={busy || !canSubmit}
          className="w-full py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
        >
          {busy
            ? mode === "signin" ? "Signing in…" : mode === "signup" ? "Creating account…" : "Sending…"
            : mode === "signin" ? "Sign in" : mode === "signup" ? "Create account" : "Send reset link"}
        </button>

        {/* Secondary actions */}
        {mode === "signin" && (
          <>
            <button
              type="button"
              onClick={handleMagicLink}
              disabled={busy}
              className="w-full py-2 border border-border text-text font-semibold rounded-lg hover:bg-surface-alt disabled:opacity-50 text-xs"
            >
              Email me a magic link instead
            </button>
            <div className="text-center">
              <button
                type="button"
                onClick={() => switchMode("reset")}
                className="text-xs text-text-muted hover:text-accent"
              >
                Forgot password?
              </button>
            </div>
          </>
        )}

        {mode === "reset" && (
          <div className="text-center">
            <button
              type="button"
              onClick={() => switchMode("signin")}
              className="text-xs text-text-muted hover:text-accent"
            >
              ← Back to sign in
            </button>
          </div>
        )}

      </form>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <AuthForm />
    </Suspense>
  );
}
