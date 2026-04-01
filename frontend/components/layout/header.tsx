"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/" },
  { label: "Iron Condor", href: "/iron-condor" },
  { label: "Calendar Spread", href: "/calendar-spread" },
  { label: "Vol Surface", href: "/vol-surface" },
];

export function Header() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 bg-navy text-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-11">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2">
            <Image src="/favicon.png" alt="AI Statcharts" width={24} height={24} className="rounded" />
            <span className="text-sm font-bold tracking-widest uppercase">
              AI Statcharts
            </span>
          </Link>

          {/* Nav */}
          <nav className="flex items-center gap-0.5">
            {NAV_ITEMS.map((item) => {
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`px-3 py-1 rounded text-xs font-semibold uppercase tracking-wider transition-colors ${
                    isActive
                      ? "bg-white/15 text-white"
                      : "text-white/60 hover:text-white hover:bg-white/10"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </div>
    </header>
  );
}
