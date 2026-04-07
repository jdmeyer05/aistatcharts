"use client";

import { useEffect, useRef, memo } from "react";

interface TradingViewChartProps {
  symbol?: string;
  interval?: string;
  theme?: "dark" | "light";
  height?: number;
  studies?: string[];
  hideTopToolbar?: boolean;
  hideSideToolbar?: boolean;
  allowSymbolChange?: boolean;
  className?: string;
}

// No TradingView login needed — the Advanced Chart widget is free and public.
// The "Cannot listen to the event from the provided iframe" console error is
// harmless — Next.js dev HMR tries to instrument the cross-origin iframe.
// Does not appear in production builds.

function TradingViewChartInner({
  symbol = "SPY",
  interval = "D",
  theme = "dark",
  height = 500,
  studies = ["MAExp@tv-basicstudies", "RSI@tv-basicstudies", "MACD@tv-basicstudies"],
  hideTopToolbar = false,
  hideSideToolbar = false,
  allowSymbolChange = true,
  className = "",
}: TradingViewChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    container.innerHTML = "";

    const wrapper = document.createElement("div");
    wrapper.className = "tradingview-widget-container";
    wrapper.style.height = "100%";
    wrapper.style.width = "100%";

    const widgetDiv = document.createElement("div");
    widgetDiv.className = "tradingview-widget-container__widget";
    widgetDiv.style.height = "100%";
    widgetDiv.style.width = "100%";
    wrapper.appendChild(widgetDiv);

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    script.type = "text/javascript";
    script.textContent = JSON.stringify({
      symbol,
      interval,
      timezone: "America/New_York",
      theme,
      style: "1",
      locale: "en",
      allow_symbol_change: allowSymbolChange,
      hide_top_toolbar: hideTopToolbar,
      hide_side_toolbar: hideSideToolbar,
      calendar: false,
      studies,
      support_host: "https://www.tradingview.com",
      details: true,
      hotlist: false,
      show_popup_button: true,
      popup_width: "1000",
      popup_height: "650",
      width: "100%",
      height: "100%",
    });
    wrapper.appendChild(script);
    container.appendChild(wrapper);

    return () => { container.innerHTML = ""; };
  }, [symbol, interval, theme, hideTopToolbar, hideSideToolbar, allowSymbolChange]);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ height, minHeight: 300 }}
    />
  );
}

export const TradingViewChart = memo(TradingViewChartInner);
