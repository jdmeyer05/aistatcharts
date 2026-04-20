export default function Loading() {
  return (
    <div className="space-y-5 animate-pulse" aria-busy="true" aria-live="polite">
      <div className="space-y-2">
        <div className="h-7 w-64 rounded bg-surface-alt" />
        <div className="h-4 w-96 max-w-full rounded bg-surface-alt/70" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="card h-24" />
        ))}
      </div>
      <div className="card">
        <div className="h-80 rounded bg-surface-alt/50" />
      </div>
    </div>
  );
}
