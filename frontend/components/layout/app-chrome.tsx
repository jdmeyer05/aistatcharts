"use client";

/**
 * App-level chrome (header + main wrapper) that hides itself on auth pages
 * like /login. Avoids restructuring every route into an `(app)` group.
 */
import { usePathname } from "next/navigation";
import { Header } from "@/components/layout/header";

const BARE_PATHS = new Set<string>(["/login"]);

export function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (BARE_PATHS.has(pathname)) return <>{children}</>;

  return (
    <>
      <Header />
      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {children}
      </main>
    </>
  );
}
