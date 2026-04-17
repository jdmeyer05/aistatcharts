"use client";

/**
 * Phase 2 auth gate. Middleware (`frontend/middleware.ts`) handles the actual
 * route protection — unauthenticated users are redirected to /login before
 * the app ever renders. This component is kept as a thin session-context
 * provider so pages can read the signed-in user without refetching.
 */
import { createContext, useContext, useEffect, useState } from "react";
import { supabaseBrowser, hasSupabaseConfig } from "@/lib/supabase";

interface SessionUser {
  email: string | null;
  id: string | null;
}

const SessionContext = createContext<SessionUser>({ email: null, id: null });

export function useSessionUser(): SessionUser {
  return useContext(SessionContext);
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<SessionUser>({ email: null, id: null });

  useEffect(() => {
    if (!hasSupabaseConfig()) return;
    const supabase = supabaseBrowser();

    supabase.auth.getUser().then(({ data }) => {
      setUser({ email: data.user?.email ?? null, id: data.user?.id ?? null });
    });

    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser({ email: session?.user?.email ?? null, id: session?.user?.id ?? null });
    });

    return () => {
      sub.subscription.unsubscribe();
    };
  }, []);

  return <SessionContext.Provider value={user}>{children}</SessionContext.Provider>;
}
