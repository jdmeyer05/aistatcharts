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
