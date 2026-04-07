"use client";

import { useEffect, useRef, memo, useState, useCallback, useMemo } from "react";
import { createChart, type IChartApi, ColorType, CrosshairMode, CandlestickSeries, HistogramSeries, LineSeries, createSeriesMarkers, type UTCTimestamp } from "lightweight-charts";
import { fetchOHLCV, type OHLCVBar, type RHStock, type RHSpread, type ChartIndicators, type IndicatorPoint } from "@/lib/api";

export interface PositionOverlay {
  stocks: RHStock[];
  spreads: RHSpread[];
}

interface LightweightChartProps {
  symbol: string;
  height?: number;
  className?: string;
  showVolume?: boolean;
  positions?: PositionOverlay;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnySeries = any;

interface CrosshairData { o: number; h: number; l: number; c: number; v: number; time: string; }

function LightweightChartInner({
  symbol,
  height = 500,
  className = "",
  showVolume = true,
  positions,
}: LightweightChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<AnySeries>(null);
  const volumeRef = useRef<AnySeries>(null);
  const priceLinesRef = useRef<AnySeries[]>([]);
  const markersRef = useRef<AnySeries>(null);
  const indicatorSeriesRef = useRef<AnySeries[]>([]);
  const barsRef = useRef<OHLCVBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastBar, setLastBar] = useState<OHLCVBar | null>(null);
  const [firstBar, setFirstBar] = useState<OHLCVBar | null>(null);
  const [indicators, setIndicators] = useState<ChartIndicators | null>(null);
  const [dataReady, setDataReady] = useState(false);
  const [timeframe, setTimeframe] = useState<number>(365);
  const [interval, setInterval] = useState("1d");
  const [activeIndicators, setActiveIndicators] = useState<Set<string>>(new Set(["ema21", "ema50", "bb"]));
  const requestId = useRef(0);

  // Crosshair legend — written directly to DOM ref (avoids React re-render per mouse pixel)
  const legendRef = useRef<HTMLDivElement>(null);
  const volumeMapRef = useRef<Map<number, number>>(new Map());

  // Stable position references — avoid refetching OHLCV when parent re-renders
  const stockPos = useMemo(() => positions?.stocks.find(s => s.ticker === symbol), [positions, symbol]);
  const tickerSpreads = useMemo(() => positions?.spreads.filter(s => s.ticker === symbol) ?? [], [positions, symbol]);
  const hasPosition = !!stockPos || tickerSpreads.length > 0;

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8b949e",
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(88, 166, 255, 0.3)", labelBackgroundColor: "#1a56db" },
        horzLine: { color: "rgba(88, 166, 255, 0.3)", labelBackgroundColor: "#1a56db" },
      },
      rightPriceScale: {
        borderColor: "#30363d",
        scaleMargins: { top: 0.05, bottom: showVolume ? 0.25 : 0.05 },
      },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        barSpacing: 6,
      },
      width: containerRef.current.clientWidth,
      height,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#3fb950",
      downColor: "#f85149",
      borderUpColor: "#3fb950",
      borderDownColor: "#f85149",
      wickUpColor: "#3fb950",
      wickDownColor: "#f85149",
    });

    chartRef.current = chart;
    candleRef.current = candleSeries;

    if (showVolume) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      volumeRef.current = volumeSeries;
    }

    // Crosshair move — write directly to DOM (no React re-render)
    chart.subscribeCrosshairMove((param) => {
      const el = legendRef.current;
      if (!el) return;
      if (!param.time || !param.seriesData) { el.textContent = ""; return; }
      const candle = param.seriesData.get(candleSeries) as { open?: number; high?: number; low?: number; close?: number } | undefined;
      if (!candle?.close) { el.textContent = ""; return; }
      const ts = typeof param.time === "number" ? param.time : 0;
      const vol = volumeMapRef.current.get(ts) ?? 0;
      const d = new Date(ts * 1000);
      const timeStr = d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric",
        ...(d.getHours() !== 0 || d.getMinutes() !== 0 ? { hour: "2-digit", minute: "2-digit" } : {}),
      });
      const o = candle.open ?? 0; const h = candle.high ?? 0; const l = candle.low ?? 0; const c = candle.close;
      const cc = c >= o ? "#3fb950" : "#f85149";
      el.innerHTML = `<span>${timeStr}</span> `
        + `<span>O <span style="color:#e6edf3">${o.toFixed(2)}</span></span> `
        + `<span>H <span style="color:#e6edf3">${h.toFixed(2)}</span></span> `
        + `<span>L <span style="color:#e6edf3">${l.toFixed(2)}</span></span> `
        + `<span>C <span style="color:${cc}">${c.toFixed(2)}</span></span>`
        + (vol > 0 ? ` <span>V <span style="color:#e6edf3">${(vol / 1e6).toFixed(1)}M</span></span>` : "");
    });

    const el = containerRef.current;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width });
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
    };
  }, [height, showVolume]);

  // Load OHLCV data — only depends on symbol + timeframe, NOT positions
  const loadData = useCallback(async () => {
    if (!candleRef.current || !chartRef.current) return;
    const thisRequest = ++requestId.current;
    setLoading(true);
    setError("");
    setDataReady(false);

    // Clear stale indicator series + position lines BEFORE loading new data
    // (prevents fitContent from fitting to wrong time range)
    for (const s of indicatorSeriesRef.current) {
      try { chartRef.current.removeSeries(s); } catch { /* ok */ }
    }
    indicatorSeriesRef.current = [];
    for (const line of priceLinesRef.current) {
      try { candleRef.current.removePriceLine(line); } catch { /* ok */ }
    }
    priceLinesRef.current = [];
    if (markersRef.current) {
      try { markersRef.current.setMarkers([]); } catch { /* ok */ }
    }
    try {
      const res = await fetchOHLCV(symbol, timeframe, interval);
      if (thisRequest !== requestId.current) return;
      const bars = res.data;
      barsRef.current = bars;
      // Build O(1) volume lookup for crosshair
      const vm = new Map<number, number>();
      for (const b of bars) vm.set(b.time, b.volume);
      volumeMapRef.current = vm;
      if (!bars.length) {
        setError("No data available");
        setLoading(false);
        return;
      }

      candleRef.current.setData(
        bars.map((b) => ({
          time: b.time as UTCTimestamp,
          open: b.open, high: b.high, low: b.low, close: b.close,
        }))
      );

      if (volumeRef.current) {
        volumeRef.current.setData(
          bars.map((b) => ({
            time: b.time as UTCTimestamp,
            value: b.volume,
            color: b.close >= b.open ? "rgba(63,185,80,0.3)" : "rgba(248,81,73,0.3)",
          }))
        );
      }

      setFirstBar(bars[0]);
      setLastBar(bars[bars.length - 1]);
      setIndicators(res.indicators ?? null);
      setDataReady(true);
      chartRef.current?.timeScale().fitContent();
    } catch (e) {
      if (thisRequest === requestId.current) {
        setError(e instanceof Error ? e.message : "Failed to load data");
      }
    } finally {
      if (thisRequest === requestId.current) setLoading(false);
    }
  }, [symbol, timeframe, interval]);

  useEffect(() => { loadData(); }, [loadData]);

  // Draw indicator overlays
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !dataReady || !indicators) return;

    // Remove old indicator series
    for (const s of indicatorSeriesRef.current) {
      try { chart.removeSeries(s); } catch { /* already removed */ }
    }
    indicatorSeriesRef.current = [];

    const toLine = (data: IndicatorPoint[] | undefined) =>
      data?.map(p => ({ time: p.time as UTCTimestamp, value: p.value })) ?? [];

    const addLine = (data: IndicatorPoint[] | undefined, color: string, width: number = 1, style: number = 0) => {
      if (!data?.length) return;
      const s = chart.addSeries(LineSeries, {
        color, lineWidth: width as 1 | 2 | 3 | 4, lineStyle: style,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      } as AnySeries);
      s.setData(toLine(data));
      indicatorSeriesRef.current.push(s);
    };

    // Price overlay indicators
    if (activeIndicators.has("ema9")) addLine(indicators.ema9, "#ffaa00", 1);
    if (activeIndicators.has("ema21")) addLine(indicators.ema21, "#58a6ff", 1);
    if (activeIndicators.has("ema50")) addLine(indicators.ema50, "#a371f7", 1);
    if (activeIndicators.has("ema200")) addLine(indicators.ema200, "#f85149", 2);
    if (activeIndicators.has("bb")) {
      addLine(indicators.bb_upper, "rgba(136,146,166,0.4)", 1, 2);
      addLine(indicators.bb_middle, "rgba(136,146,166,0.25)", 1, 2);
      addLine(indicators.bb_lower, "rgba(136,146,166,0.4)", 1, 2);
    }
    if (activeIndicators.has("vwap")) addLine(indicators.vwap, "#d29922", 1, 2);

    // RSI — separate scale on left
    if (activeIndicators.has("rsi") && indicators.rsi?.length) {
      const s = chart.addSeries(LineSeries, {
        color: "#a371f7", lineWidth: 1,
        priceScaleId: "rsi", priceLineVisible: false, lastValueVisible: false,
      });
      s.setData(toLine(indicators.rsi));
      chart.priceScale("rsi").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0.02 },
        visible: false,
      });
      // Overbought/oversold reference lines
      const rsi70 = chart.addSeries(LineSeries, {
        color: "rgba(248,81,73,0.2)", lineWidth: 1, lineStyle: 2,
        priceScaleId: "rsi", priceLineVisible: false, lastValueVisible: false,
      });
      const rsi30 = chart.addSeries(LineSeries, {
        color: "rgba(63,185,80,0.2)", lineWidth: 1, lineStyle: 2,
        priceScaleId: "rsi", priceLineVisible: false, lastValueVisible: false,
      });
      const rsiTimes = indicators.rsi.map(p => ({ time: p.time as UTCTimestamp, value: 70 }));
      const rsiTimes30 = indicators.rsi.map(p => ({ time: p.time as UTCTimestamp, value: 30 }));
      rsi70.setData(rsiTimes);
      rsi30.setData(rsiTimes30);
      indicatorSeriesRef.current.push(s, rsi70, rsi30);
    }

    // MACD — separate scale
    if (activeIndicators.has("macd") && indicators.macd?.length) {
      const macdLine = chart.addSeries(LineSeries, {
        color: "#58a6ff", lineWidth: 1,
        priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false,
      });
      macdLine.setData(toLine(indicators.macd));

      const signalLine = chart.addSeries(LineSeries, {
        color: "#f85149", lineWidth: 1,
        priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false,
      });
      signalLine.setData(toLine(indicators.macd_signal));

      chart.priceScale("macd").applyOptions({
        scaleMargins: { top: 0.88, bottom: 0.0 },
        visible: false,
      });

      if (indicators.macd_hist?.length) {
        const histSeries = chart.addSeries(HistogramSeries, {
          priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false,
        });
        histSeries.setData(
          indicators.macd_hist.map(p => ({
            time: p.time as UTCTimestamp,
            value: p.value,
            color: p.value >= 0 ? "rgba(63,185,80,0.4)" : "rgba(248,81,73,0.4)",
          }))
        );
        indicatorSeriesRef.current.push(histSeries);
      }

      indicatorSeriesRef.current.push(macdLine, signalLine);
    }
  }, [dataReady, indicators, activeIndicators]);

  // Draw position overlays — only after data is loaded
  useEffect(() => {
    if (!candleRef.current || !dataReady) return;

    // Clear old lines
    for (const line of priceLinesRef.current) {
      try { candleRef.current.removePriceLine(line); } catch { /* already removed */ }
    }
    priceLinesRef.current = [];

    // Clear old markers
    if (markersRef.current) {
      markersRef.current.setMarkers([]);
    }

    if (!hasPosition) return;

    const lines: AnySeries[] = [];

    // Stock position: avg cost line + entry marker
    if (stockPos) {
      lines.push(candleRef.current.createPriceLine({
        price: stockPos.avg_cost,
        color: "#58a6ff",
        lineWidth: 2,
        lineStyle: 0,
        axisLabelVisible: true,
        title: `Avg ${stockPos.qty.toFixed(0)} @ $${stockPos.avg_cost.toFixed(2)}`,
      }));

      // Entry date marker (v5: createSeriesMarkers plugin)
      if (stockPos.entry_date) {
        const entryTs = Math.floor(new Date(stockPos.entry_date).getTime() / 1000);
        if (!markersRef.current) {
          markersRef.current = createSeriesMarkers(candleRef.current);
        }
        markersRef.current.setMarkers([{
          time: entryTs as UTCTimestamp,
          position: "belowBar",
          color: "#58a6ff",
          shape: "arrowUp",
          text: `Entry $${stockPos.avg_cost.toFixed(2)}`,
        }]);
      }
    }

    // Option spreads
    for (const spread of tickerSpreads) {
      // Determine which short strikes are puts vs calls from the legs
      const shortPutStrikes: number[] = [];
      const shortCallStrikes: number[] = [];
      const longPutStrikes: number[] = [];
      const longCallStrikes: number[] = [];

      for (const leg of spread.legs) {
        if (leg.direction === "short" && leg.opt_type === "put") shortPutStrikes.push(leg.strike);
        if (leg.direction === "short" && leg.opt_type === "call") shortCallStrikes.push(leg.strike);
        if (leg.direction === "long" && leg.opt_type === "put") longPutStrikes.push(leg.strike);
        if (leg.direction === "long" && leg.opt_type === "call") longCallStrikes.push(leg.strike);
      }

      // Short strikes (risk levels)
      for (const strike of shortPutStrikes) {
        lines.push(candleRef.current.createPriceLine({
          price: strike, color: "#f85149", lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: `Short $${strike}P`,
        }));
      }
      for (const strike of shortCallStrikes) {
        lines.push(candleRef.current.createPriceLine({
          price: strike, color: "#f85149", lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: `Short $${strike}C`,
        }));
      }

      // Long strikes (protection)
      for (const strike of [...longPutStrikes, ...longCallStrikes]) {
        lines.push(candleRef.current.createPriceLine({
          price: strike, color: "#484f58", lineWidth: 1, lineStyle: 3,
          axisLabelVisible: true, title: `Wing $${strike}`,
        }));
      }

      // Breakevens from credit and actual leg types
      const credit = spread.net_premium / (spread.qty * 100);
      if (credit > 0) {
        for (const strike of shortPutStrikes) {
          lines.push(candleRef.current.createPriceLine({
            price: strike - credit, color: "#d29922", lineWidth: 1, lineStyle: 2,
            axisLabelVisible: true, title: "BE",
          }));
        }
        for (const strike of shortCallStrikes) {
          lines.push(candleRef.current.createPriceLine({
            price: strike + credit, color: "#d29922", lineWidth: 1, lineStyle: 2,
            axisLabelVisible: true, title: "BE",
          }));
        }
      }
    }

    priceLinesRef.current = lines;
  }, [dataReady, stockPos, tickerSpreads, hasPosition, symbol]);

  const pctChange = firstBar && lastBar && firstBar.close > 0
    ? ((lastBar.close - firstBar.close) / firstBar.close) * 100
    : null;

  return (
    <div className={className}>
      {/* Header */}
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2.5">
          <span className="text-sm font-bold text-text">{symbol}</span>
          {lastBar && (
            <>
              <span className="text-sm font-data text-text">${lastBar.close.toFixed(2)}</span>
              {pctChange !== null && (
                <span className={`text-xs font-data font-semibold ${pctChange >= 0 ? "text-gain" : "text-loss"}`}>
                  {pctChange >= 0 ? "+" : ""}{pctChange.toFixed(2)}%
                </span>
              )}
              <span className="text-[0.55rem] font-data text-text-muted">
                H {lastBar.high.toFixed(2)} · L {lastBar.low.toFixed(2)}
                {lastBar.volume > 0 && <> · {(lastBar.volume / 1e6).toFixed(1)}M vol</>}
              </span>
            </>
          )}
          {hasPosition && (
            <span className="text-[0.5rem] font-semibold px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/30">
              POSITION
            </span>
          )}
          {loading && (
            <div className="w-3 h-3 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          )}
        </div>
        <div className="flex items-center gap-0.5">
          {[
            { label: "1M", days: 30 },
            { label: "3M", days: 90 },
            { label: "6M", days: 180 },
            { label: "1Y", days: 365 },
            { label: "2Y", days: 730 },
            { label: "5Y", days: 1825 },
          ].map(({ label, days }) => (
            <button
              key={label}
              onClick={() => setTimeframe(days)}
              className={`px-1.5 py-0.5 text-[0.6rem] rounded ${
                timeframe === days
                  ? "bg-accent/15 text-accent font-semibold"
                  : "text-text-muted hover:text-text"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Indicator toggles */}
      <div className="flex items-center gap-1 mb-1.5">
        {[
          { key: "ema9", label: "EMA 9", color: "#ffaa00" },
          { key: "ema21", label: "EMA 21", color: "#58a6ff" },
          { key: "ema50", label: "EMA 50", color: "#a371f7" },
          { key: "ema200", label: "EMA 200", color: "#f85149" },
          { key: "bb", label: "BB", color: "#8b949e" },
          { key: "vwap", label: "VWAP", color: "#d29922" },
          { key: "rsi", label: "RSI", color: "#a371f7" },
          { key: "macd", label: "MACD", color: "#58a6ff" },
        ].map(({ key, label, color }) => (
          <button
            key={key}
            onClick={() => setActiveIndicators(prev => {
              const next = new Set(prev);
              next.has(key) ? next.delete(key) : next.add(key);
              return next;
            })}
            className={`px-1.5 py-0.5 text-[0.5rem] rounded border transition-colors ${
              activeIndicators.has(key)
                ? "border-current font-semibold"
                : "border-border text-text-muted hover:text-text"
            }`}
            style={activeIndicators.has(key) ? { color, borderColor: color } : undefined}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Position legend */}
      {hasPosition && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 mb-1.5 text-[0.55rem]">
          {stockPos && (
            <span className="text-text-muted">
              <span className="inline-block w-3 h-[2px] bg-[#58a6ff] mr-1 align-middle" />
              {stockPos.qty.toFixed(0)} shares @ ${stockPos.avg_cost.toFixed(2)}
              <span className={`ml-1 font-semibold font-data ${stockPos.pl >= 0 ? "text-gain" : "text-loss"}`}>
                {stockPos.pl >= 0 ? "+" : ""}${stockPos.pl.toFixed(0)} ({stockPos.pl_pct >= 0 ? "+" : ""}{stockPos.pl_pct.toFixed(1)}%)
              </span>
            </span>
          )}
          {tickerSpreads.map((sp, i) => (
            <span key={i} className="text-text-muted">
              <span className="inline-block w-3 h-[2px] bg-[#f85149] mr-1 align-middle" style={{ borderTop: "1px dashed #f85149" }} />
              {sp.type} {sp.strikes} exp {sp.expiration}
              <span className={`ml-1 font-semibold font-data ${sp.pl >= 0 ? "text-gain" : "text-loss"}`}>
                {sp.pl >= 0 ? "+" : ""}${sp.pl.toFixed(0)}
              </span>
            </span>
          ))}
        </div>
      )}

      {error && <div className="text-xs text-loss mb-1">{error}</div>}

      {/* Chart with crosshair legend overlay */}
      <div className="relative" style={{ height }}>
        {/* Crosshair OHLCV legend — DOM-driven, no React re-renders */}
        <div ref={legendRef}
          className="absolute top-1 left-1 z-10 flex items-center gap-2 text-[0.6rem] font-data text-text-muted pointer-events-none"
        />

        {/* Interval selector — top-right overlay */}
        <div className="absolute top-1 right-1 z-10 flex items-center gap-0.5">
          {[
            { label: "1m", val: "1m" },
            { label: "5m", val: "5m" },
            { label: "15m", val: "15m" },
            { label: "1H", val: "1h" },
            { label: "D", val: "1d" },
          ].map(({ label, val }) => (
            <button
              key={val}
              onClick={() => setInterval(val)}
              className={`px-1 py-0.5 text-[0.5rem] rounded ${
                interval === val
                  ? "bg-accent/15 text-accent font-semibold"
                  : "text-text-muted hover:text-text"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div ref={containerRef} style={{ height: "100%", width: "100%" }} />
      </div>
    </div>
  );
}

export const LightweightChart = memo(LightweightChartInner);
