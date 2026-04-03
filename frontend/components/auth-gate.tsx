"use client";

import { useState, useEffect } from "react";

const SITE_KEY = "aistatcharts_auth";
const VALID_PASSWORD = process.env.NEXT_PUBLIC_SITE_PASSWORD || "letmein";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState(false);
  const [checking, setChecking] = useState(true);
  const [password, setPassword] = useState("");
  const [error, setError] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(SITE_KEY);
    if (stored === VALID_PASSWORD) {
      setAuthed(true);
    }
    setChecking(false);
  }, []);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (password === VALID_PASSWORD) {
      localStorage.setItem(SITE_KEY, password);
      setAuthed(true);
      setError(false);
    } else {
      setError(true);
    }
  }

  if (checking) return null;

  if (!authed) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg">
        <form onSubmit={handleSubmit} className="card w-80 space-y-4 text-center">
          <h1 className="text-xl font-bold">AI Statcharts</h1>
          <p className="text-sm text-text-muted">Private access only.</p>
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="Access code"
            autoFocus
            className="w-full px-4 py-2 border border-border rounded-lg text-sm bg-surface text-center"
          />
          {error && <p className="text-xs text-loss">Incorrect code.</p>}
          <button type="submit"
            className="w-full py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover text-sm">
            Enter
          </button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
