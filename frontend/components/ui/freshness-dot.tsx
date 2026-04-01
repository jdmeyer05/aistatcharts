interface FreshnessDotProps {
  label: string;
  ageMinutes: number | null;
  greenThreshold?: number;
  yellowThreshold?: number;
}

export function FreshnessDot({
  label,
  ageMinutes,
  greenThreshold = 5,
  yellowThreshold = 15,
}: FreshnessDotProps) {
  if (ageMinutes === null) {
    return (
      <span className="text-[0.6rem] font-mono text-text-muted">
        ● {label}: —
      </span>
    );
  }

  const color =
    ageMinutes < greenThreshold
      ? "dot-fresh"
      : ageMinutes < yellowThreshold
        ? "dot-aging"
        : "dot-stale";

  const ageStr =
    ageMinutes < 1
      ? "now"
      : ageMinutes < 60
        ? `${Math.round(ageMinutes)}m`
        : ageMinutes < 1440
          ? `${(ageMinutes / 60).toFixed(0)}h`
          : `${(ageMinutes / 1440).toFixed(0)}d`;

  return (
    <span className={`text-[0.6rem] font-mono ${color}`}>
      ● {label} {ageStr}
    </span>
  );
}

interface FreshnessBarProps {
  sources: Array<{
    label: string;
    ageMinutes: number | null;
    greenThreshold?: number;
    yellowThreshold?: number;
  }>;
}

export function FreshnessBar({ sources }: FreshnessBarProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {sources.map((src) => (
        <FreshnessDot key={src.label} {...src} />
      ))}
    </div>
  );
}
