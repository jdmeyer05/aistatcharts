export function fmtBn(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(digits)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(digits)}M`;
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function fmtPctSigned(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function shortDate(s: string | null | undefined): string {
  if (!s || s === "NaT") return "—";
  return s.slice(0, 10);
}

export const EIGHT_K_ITEM_NAMES: Record<string, string> = {
  "1.01": "Entry into Agreement", "1.02": "Termination of Agreement",
  "2.01": "Acquisition/Disposition", "2.02": "Results of Operations",
  "2.03": "Obligation Trigger", "2.05": "Costs for Exit",
  "3.01": "Delisting", "3.02": "Unregistered Sales",
  "4.01": "Auditor Change", "4.02": "Non-Reliance on Financials",
  "5.01": "Change of Control", "5.02": "Officer Departure/Appointment",
  "5.03": "Amendments to Articles", "7.01": "Regulation FD Disclosure",
  "8.01": "Other Events", "9.01": "Financial Statements and Exhibits",
};
