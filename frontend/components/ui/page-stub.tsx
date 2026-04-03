"use client";

import { usePathname } from "next/navigation";
import { findPage } from "@/lib/nav";

interface PageStubProps {
  title: string;
  description: string;
  tabs?: string[];
  streamlitPort?: number;
}

export function PageStub({ title, description, tabs, streamlitPort = 8502 }: PageStubProps) {
  const pathname = usePathname();
  const page = findPage(pathname);

  // Derive Streamlit page name from pathname for fallback link
  const streamlitPage = pathname.slice(1).replace(/-/g, "_")
    .replace(/^([a-z])/, (_, c) => c.toUpperCase())
    .replace(/_([a-z])/g, (_, c) => `_${c.toUpperCase()}`);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        <p className="text-text-secondary text-sm mt-1">{description}</p>
      </div>

      {tabs && tabs.length > 0 && (
        <div className="card card-compact">
          <div className="metric-label mb-2">Tabs to build</div>
          <div className="flex gap-1 flex-wrap">
            {tabs.map((tab) => (
              <span key={tab} className="px-2.5 py-1 text-xs font-semibold rounded bg-surface-alt text-text-muted border border-border">
                {tab}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="card text-center py-12">
        <div className="text-4xl mb-3 opacity-20">
          <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="inline-block">
            <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
          </svg>
        </div>
        <p className="text-sm text-text-muted">Migration in progress</p>
        <a
          href={`http://localhost:${streamlitPort}/${streamlitPage}`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block mt-3 px-4 py-1.5 text-xs font-semibold rounded bg-accent text-white hover:bg-accent-hover transition-colors"
        >
          Open in Streamlit
        </a>
      </div>
    </div>
  );
}
