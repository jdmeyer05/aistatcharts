"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState, useRef } from "react";
import { NAV_GROUPS, findGroup, type NavGroup } from "@/lib/nav";
import { useSessionUser } from "@/components/auth-gate";
import { supabaseBrowser, hasSupabaseConfig } from "@/lib/supabase";
import { usePwa } from "@/components/pwa-provider";

/* ─── Theme Toggle ────────────────────────────────────────── */

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return <div className="w-8 h-8" />;

  const cycle = () => {
    if (theme === "dark") setTheme("light");
    else if (theme === "light") setTheme("retro");
    else if (theme === "retro") setTheme("matrix");
    else setTheme("dark");
  };
  const label = theme === "dark" ? "Dark" : theme === "retro" ? "Retro" : theme === "matrix" ? "Matrix" : "Light";

  return (
    <button
      onClick={cycle}
      className="h-8 flex items-center gap-1.5 px-2 rounded-md text-white/60 hover:text-white hover:bg-white/10 transition-colors"
      title={`Theme: ${label}`}
    >
      {theme === "dark" ? (
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>
      ) : theme === "retro" ? (
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#33ff33" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
        </svg>
      ) : theme === "matrix" ? (
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#00ff41" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="4" y1="2" x2="4" y2="22"/><line x1="8" y1="6" x2="8" y2="18"/><line x1="12" y1="2" x2="12" y2="22"/>
          <line x1="16" y1="4" x2="16" y2="20"/><line x1="20" y1="8" x2="20" y2="16"/>
        </svg>
      ) : (
        <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
          <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
        </svg>
      )}
      <span className="text-[0.55rem] font-semibold hidden sm:inline">{label}</span>
    </button>
  );
}

/* ─── Single-page nav link (no hooks, avoids rules-of-hooks issue) ── */

function NavLink({ group }: { group: NavGroup }) {
  const pathname = usePathname();
  const page = group.pages[0];
  const active = pathname === page.href;
  return (
    <Link href={page.href}
      className={`shrink-0 px-2.5 py-1 rounded text-xs font-semibold uppercase tracking-wider whitespace-nowrap transition-colors ${
        active ? "bg-white/15 text-white" : "text-white/60 hover:text-white hover:bg-white/10"
      }`}>
      {group.label}
    </Link>
  );
}

/* ─── Dropdown Menu ───────────────────────────────────────── */

function NavDropdown({ group, isActive }: { group: NavGroup; isActive: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  // Close on route change
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen(!open)}
        className={`px-2.5 py-1 rounded text-xs font-semibold uppercase tracking-wider whitespace-nowrap transition-colors flex items-center gap-1 ${
          isActive ? "bg-white/15 text-white" : "text-white/60 hover:text-white hover:bg-white/10"
        }`}
      >
        {group.label}
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className={`transition-transform ${open ? "rotate-180" : ""}`}>
          <path d="M2 4L5 7L8 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 w-64 bg-[#1e2530] dark:bg-[#1c2128] border border-white/10 rounded-lg shadow-xl z-50 py-1 max-h-[70vh] overflow-y-auto">
          {group.pages.map((page) => {
            const active = pathname === page.href;
            return (
              <Link key={page.href} href={page.href} onClick={() => setOpen(false)}
                className={`block px-3 py-2 transition-colors ${
                  active ? "bg-white/10 text-white" : "text-white/70 hover:bg-white/5 hover:text-white"
                }`}>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold">{page.label}</span>
                  {page.status === "live" && (
                    <span className="text-[0.55rem] font-bold uppercase tracking-wider text-emerald-400">Live</span>
                  )}
                </div>
                <div className="text-[0.6rem] text-white/40 mt-0.5 leading-tight">{page.description}</div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ─── User menu ──────────────────────────────────────────── */

function UserMenu() {
  const { email } = useSessionUser();
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  async function signOut() {
    if (!hasSupabaseConfig()) return;
    setBusy(true);
    try {
      await supabaseBrowser().auth.signOut();
      // Hard navigate so the proxy re-runs with the cleared cookie.
      window.location.assign("/login");
    } catch {
      setBusy(false);
    }
  }

  if (!email) return null;

  // Compact: just initials button; dropdown reveals email + sign out.
  const initial = email.charAt(0).toUpperCase();

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={email}
        className="w-7 h-7 flex items-center justify-center rounded-full bg-white/15 text-white text-[0.7rem] font-bold hover:bg-white/25 transition-colors"
      >
        {initial}
      </button>
      {open && (
        <div className="absolute right-0 top-9 min-w-[200px] rounded-md border border-border bg-surface shadow-lg text-sm z-50">
          <div className="px-3 py-2 border-b border-border text-xs text-text-muted font-data truncate">
            {email}
          </div>
          <button
            onClick={signOut}
            disabled={busy}
            className="w-full text-left px-3 py-2 text-xs font-semibold hover:bg-surface-alt disabled:opacity-50"
          >
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>
      )}
    </div>
  );
}

/* ─── Mobile drawer ──────────────────────────────────────── */

function InstallButton() {
  const { canInstall, isStandalone, promptInstall } = usePwa();
  if (isStandalone || !canInstall) return null;
  return (
    <button
      onClick={() => promptInstall()}
      className="mx-4 mt-3 mb-2 px-3 py-2 min-h-[44px] rounded border border-accent/60 bg-accent/10 text-white text-sm font-semibold flex items-center justify-center gap-2 hover:bg-accent/20 active:bg-accent/30"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
      </svg>
      Install app
    </button>
  );
}

function MobileNav({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const onCloseRef = useRef(onClose);

  // Keep the ref up to date without mutating during render.
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // Close when the route changes. Ref avoids re-running on every parent
  // re-render (onClose is a fresh inline function each time).
  useEffect(() => {
    onCloseRef.current();
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="md:hidden fixed inset-0 z-50" role="dialog" aria-modal="true">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="absolute top-0 right-0 bottom-0 w-[85%] max-w-sm bg-[#1a2332] dark:bg-[#161b22] text-white shadow-2xl overflow-y-auto flex flex-col">
        <div className="sticky top-0 flex items-center justify-between px-4 h-11 border-b border-white/10 bg-[#1a2332] dark:bg-[#161b22]">
          <span className="text-xs font-bold tracking-widest uppercase">Navigation</span>
          <button
            onClick={onClose}
            aria-label="Close navigation"
            className="w-10 h-10 flex items-center justify-center rounded-md text-white/70 hover:text-white hover:bg-white/10 -mr-2"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <InstallButton />
        <nav className="flex-1 py-2">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="py-2 border-b border-white/5 last:border-0">
              <div className="px-4 py-1 text-[0.6rem] font-bold uppercase tracking-wider text-white/40">
                {group.label}
              </div>
              {group.pages.map((page) => {
                const active = pathname === page.href;
                return (
                  <Link
                    key={page.href}
                    href={page.href}
                    onClick={onClose}
                    className={`block px-4 py-3 min-h-[44px] ${
                      active ? "bg-white/10 text-white" : "text-white/75 hover:bg-white/5 active:bg-white/10"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{page.label}</span>
                      {page.status === "live" && (
                        <span className="text-[0.55rem] font-bold uppercase tracking-wider text-emerald-400 shrink-0">Live</span>
                      )}
                    </div>
                    <div className="text-[0.65rem] text-white/40 mt-0.5 leading-snug">{page.description}</div>
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
      </div>
    </div>
  );
}

/* ─── Header ──────────────────────────────────────────────── */

export function Header() {
  const pathname = usePathname();
  const activeGroup = findGroup(pathname);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 bg-[#1a2332] dark:bg-[#161b22] text-white border-b border-transparent dark:border-border">
      <div className="max-w-7xl mx-auto px-3 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-11 gap-2">
          {/* Logo — text hidden below lg to make room for 7 nav groups */}
          <Link href="/" className="flex items-center gap-2 shrink-0">
            <Image src="/favicon.png" alt="AI Statcharts" width={22} height={22} className="rounded" />
            <span className="text-sm font-bold tracking-widest uppercase hidden lg:inline">
              AI Statcharts
            </span>
          </Link>

          {/* Desktop nav (md+). Min-w-0 + overflow-x-auto lets it gracefully
              scroll horizontally on tablets where 7 groups don't fit cleanly. */}
          <nav className="hidden md:flex items-center gap-0.5 min-w-0 overflow-x-auto scrollbar-thin">
            {NAV_GROUPS.map((group) =>
              group.pages.length === 1 ? (
                <NavLink key={group.label} group={group} />
              ) : (
                <NavDropdown
                  key={group.label}
                  group={group}
                  isActive={activeGroup?.label === group.label}
                />
              )
            )}
          </nav>

          {/* Theme toggle + user menu + mobile toggle */}
          <div className="flex items-center gap-1 sm:gap-2 shrink-0">
            <ThemeToggle />
            <UserMenu />
            <button
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
              className="md:hidden w-10 h-10 flex items-center justify-center rounded-md text-white/70 hover:text-white hover:bg-white/10 -mr-2"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
      <MobileNav open={mobileOpen} onClose={() => setMobileOpen(false)} />
    </header>
  );
}
