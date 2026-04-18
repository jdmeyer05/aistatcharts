import Link from "next/link";

export interface PendingFeature {
  title: string;
  description: string;
}

export interface PendingSource {
  name: string;
  status: "free" | "paid" | "internal";
  note?: string;
}

interface Props {
  pageTitle: string;
  subtitle: string;
  overview: string;
  features: PendingFeature[];
  sources: PendingSource[];
  backlinks?: { label: string; href: string }[];
}

/**
 * Designed placeholder for Smart Money pages whose backend data source is
 * not yet wired. Shows the intended shape of the page so the gap is
 * explicit rather than a generic "coming soon".
 */
export function PendingSmartMoneyPage({
  pageTitle,
  subtitle,
  overview,
  features,
  sources,
  backlinks,
}: Props) {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{pageTitle}</h1>
        <p className="text-text-secondary text-sm mt-1">{subtitle}</p>
      </div>

      <div className="card border-l-4 border-l-warn">
        <div className="text-xs font-bold uppercase tracking-wider text-warn mb-1">
          Data pipeline pending
        </div>
        <p className="text-sm">{overview}</p>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-3">What this page will cover</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {features.map((f) => (
            <div
              key={f.title}
              className="p-3 rounded border border-border bg-surface-alt/30"
            >
              <div className="text-sm font-semibold mb-1">{f.title}</div>
              <div className="text-xs text-text-muted leading-relaxed">
                {f.description}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Data sources needed</div>
        <table className="w-full text-xs">
          <thead className="text-text-muted border-b border-border">
            <tr>
              <th className="text-left py-1.5 px-2">Source</th>
              <th className="text-left py-1.5 px-2">Tier</th>
              <th className="text-left py-1.5 px-2">Notes</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => {
              const tierColor =
                s.status === "free"
                  ? "text-gain"
                  : s.status === "paid"
                    ? "text-warn"
                    : "text-accent";
              return (
                <tr key={s.name} className="border-b border-border/50">
                  <td className="py-1.5 px-2 font-semibold">{s.name}</td>
                  <td className={`py-1.5 px-2 font-semibold ${tierColor}`}>
                    {s.status}
                  </td>
                  <td className="py-1.5 px-2 text-text-muted">{s.note ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {backlinks && backlinks.length > 0 && (
        <div className="card card-compact">
          <div className="text-xs text-text-muted mb-2">In the meantime</div>
          <div className="flex flex-wrap gap-2">
            {backlinks.map((b) => (
              <Link
                key={b.href}
                href={b.href}
                className="px-3 py-1.5 text-xs rounded border border-border hover:bg-surface-alt"
              >
                {b.label} →
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
