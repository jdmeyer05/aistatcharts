"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState, useRef } from "react";
import { NAV_GROUPS, findGroup, type NavGroup } from "@/lib/nav";

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
      className={`px-2.5 py-1 rounded text-xs font-semibold uppercase tracking-wider transition-colors ${
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
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`px-2.5 py-1 rounded text-xs font-semibold uppercase tracking-wider transition-colors flex items-center gap-1 ${
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

/* ─── Header ──────────────────────────────────────────────── */

export function Header() {
  const pathname = usePathname();
  const activeGroup = findGroup(pathname);

  return (
    <header className="sticky top-0 z-50 bg-[#1a2332] dark:bg-[#161b22] text-white border-b border-transparent dark:border-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-11">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2 shrink-0">
            <Image src="/favicon.png" alt="AI Statcharts" width={22} height={22} className="rounded" />
            <span className="text-sm font-bold tracking-widest uppercase hidden sm:inline">
              AI Statcharts
            </span>
          </Link>

          {/* Nav groups */}
          <nav className="flex items-center gap-0.5">
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

          {/* Theme toggle */}
          <div className="flex items-center shrink-0">
            <ThemeToggle />
          </div>
        </div>
      </div>
    </header>
  );
}
