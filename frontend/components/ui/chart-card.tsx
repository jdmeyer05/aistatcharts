"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useIsMobile } from "@/lib/chart-theme";

export interface ChartCardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  /** Top-right slot for controls (tab switcher, filter, etc.). */
  action?: ReactNode;
  /** Override the internal loading state. When true, skeleton replaces children. */
  loading?: boolean;
  /** If set, renders a bordered error surface at the reserved height. */
  error?: string | null;
  /** Reserved chart height in pixels. Single number or per-breakpoint. */
  height?: number | { desktop: number; mobile: number };
  /** Skip IntersectionObserver and render immediately. Default false. */
  eager?: boolean;
  /** Below 640px, render this instead of children (e.g. a 2D fallback for 3D). */
  mobileFallback?: ReactNode;
  /** Extra class on the outer `.card` wrapper. */
  className?: string;
  children: ReactNode;
}

/**
 * Wraps a chart (typically Plotly) in the standard `.card` surface and adds:
 * - Lazy hydration via IntersectionObserver (chart mounts only when scrolled near).
 * - A reserved-height skeleton so content below doesn't shift when the chart mounts.
 * - A loading / error state that reuses the same reserved height.
 * - An optional mobile fallback slot for charts that don't work on phones.
 *
 * Drop-in: any existing `<div className="card"><Plot .../></div>` becomes
 * `<ChartCard height={...}><Plot .../></ChartCard>`.
 */
export function ChartCard({
  title,
  subtitle,
  action,
  loading = false,
  error = null,
  height = 340,
  eager = false,
  mobileFallback,
  className = "",
  children,
}: ChartCardProps) {
  const isMobile = useIsMobile();
  const reservedHeight =
    typeof height === "number" ? height : isMobile ? height.mobile : height.desktop;

  const [visible, setVisible] = useState(eager);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (visible) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [visible]);

  const showSkeleton = !visible || loading;
  const useMobileSlot = isMobile && mobileFallback !== undefined;

  let body: ReactNode;
  if (error) {
    body = (
      <div
        className="flex items-center justify-center text-sm text-loss font-data border border-loss/20 rounded"
        style={{ height: reservedHeight }}
      >
        {error}
      </div>
    );
  } else if (showSkeleton) {
    body = (
      <div
        className="rounded bg-surface-alt/60 animate-pulse"
        style={{ height: reservedHeight }}
        aria-hidden="true"
      />
    );
  } else {
    body = useMobileSlot ? mobileFallback : children;
  }

  const hasHeader = title !== undefined || subtitle !== undefined || action !== undefined;

  return (
    <div ref={ref} className={`card ${className}`}>
      {hasHeader && (
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="min-w-0">
            {title && <div className="font-semibold text-sm truncate">{title}</div>}
            {subtitle && <div className="text-xs text-text-muted mt-0.5">{subtitle}</div>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      {body}
    </div>
  );
}
