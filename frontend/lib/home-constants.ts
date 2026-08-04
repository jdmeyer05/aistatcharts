/** English ordinal suffix. Three places on the home page built this by
 *  appending a bare "th", which printed "1th pctile" and "23th pctile" — and a
 *  percentile is exactly where a reader is looking hard at the number. */
export function ordinal(n: number): string {
  const i = Math.round(n);
  const mod100 = Math.abs(i) % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${i}th`;
  switch (Math.abs(i) % 10) {
    case 1: return `${i}st`;
    case 2: return `${i}nd`;
    case 3: return `${i}rd`;
    default: return `${i}th`;
  }
}

export const PULSE_TICKERS = [
  "SPY",
  "QQQ",
  "^VIX",
  "TLT",
  "GLD",
  "USO",
  "BTC-USD",
  "DX-Y.NYB",
] as const;

export const PULSE_LABELS: Record<string, string> = {
  SPY: "S&P",
  QQQ: "Nasdaq",
  "^VIX": "VIX",
  TLT: "20Y",
  GLD: "Gold",
  USO: "Crude",
  "BTC-USD": "BTC",
  "DX-Y.NYB": "DXY",
};
