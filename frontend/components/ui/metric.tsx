interface MetricProps {
  label: string;
  value: string;
  delta?: string;
  deltaType?: "gain" | "loss" | "neutral";
  className?: string;
}

export function Metric({ label, value, delta, deltaType, className = "" }: MetricProps) {
  const deltaColor =
    deltaType === "gain"
      ? "text-gain"
      : deltaType === "loss"
        ? "text-loss"
        : "text-text-muted";

  return (
    <div className={className}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {delta && (
        <div className={`text-xs font-medium font-data ${deltaColor}`}>
          {delta}
        </div>
      )}
    </div>
  );
}
