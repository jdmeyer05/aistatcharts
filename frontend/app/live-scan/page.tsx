"use client";

import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { useTheme } from "next-themes";
import { Metric } from "@/components/ui/metric";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, Html } from "@react-three/drei";
import * as THREE from "three";

const DEFAULT_TICKERS = ["SPY","QQQ","IWM","DIA","AAPL","MSFT","NVDA","TSLA","AMD","AMZN","META","GOOGL","NFLX","JPM","BA","GS","XOM","CVX","GLD","TLT","SMH","XLF","XLE","XLV","COIN","SNOW","PLTR","UBER","SQ","SHOP"];
const DEFAULT_STRATS = ["sma_cross","ema_cross","macd","rsi_ob_os","mean_rev","adx_di","ichimoku","tema_cross","stochastic","parabolic_sar","momentum","donchian","bb_breakout","williams_r","cci","trend_mr_composite","trend_bb_composite"];

const STRAT_LABELS: Record<string, string> = {
  sma_cross: "SMA", ema_cross: "EMA", macd: "MACD", rsi_ob_os: "RSI", mean_rev: "BB MR",
  adx_di: "ADX", ichimoku: "Ichi", tema_cross: "TEMA", stochastic: "Stoch", parabolic_sar: "SAR",
  momentum: "Mom", donchian: "Donch", bb_breakout: "BB BO", williams_r: "W%R", cci: "CCI",
  trend_mr_composite: "T+RSI", trend_bb_composite: "T+BB",
};

interface CellData {
  scanned: boolean; dsr: number; excess_sharpe: number; sharpe: number; signal: string; cagr: number;
}

// ── Instanced columns — one mesh for ALL columns, GPU-efficient ──
function ColumnField({ tickers, strats, grid, onHover }: {
  tickers: string[]; strats: string[]; grid: Record<string, CellData>;
  onHover: (key: string | null) => void;
}) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const count = tickers.length * strats.length;
  const dummy = useMemo(() => new THREE.Object3D(), []);
  const colorArray = useMemo(() => new Float32Array(count * 3), [count]);
  const targetHeights = useRef(new Float32Array(count).fill(0.05));
  const currentHeights = useRef(new Float32Array(count).fill(0.05));

  useFrame(() => {
    if (!meshRef.current) return;
    const time = Date.now() * 0.001;
    let idx = 0;

    for (let si = 0; si < strats.length; si++) {
      for (let ti = 0; ti < tickers.length; ti++) {
        const key = `${tickers[ti]}_${strats[si]}`;
        const cell = grid[key];

        // Target height
        const target = cell?.scanned ? Math.max(0.1, Math.abs(cell.excess_sharpe) * 0.8 + 0.1) : 0.03;
        targetHeights.current[idx] = target;

        // Smooth interpolation
        currentHeights.current[idx] += (targetHeights.current[idx] - currentHeights.current[idx]) * 0.06;
        const h = currentHeights.current[idx];

        // Pulse for significant
        const pulse = cell?.scanned && cell.dsr >= 0.85 && cell.signal !== "Flat"
          ? 1 + Math.sin(time * 3 + ti + si) * 0.08 : 1;

        // Position + scale
        dummy.position.set(ti * 0.6, h * pulse / 2, si * 0.6);
        dummy.scale.set(0.25, h * pulse, 0.25);
        dummy.updateMatrix();
        meshRef.current.setMatrixAt(idx, dummy.matrix);

        // Color
        let r = 0.12, g = 0.12, b = 0.15;
        if (cell?.scanned) {
          if (cell.signal === "Long" && cell.dsr >= 0.85) { r = 0; g = 1; b = 0.6; }
          else if (cell.signal === "Long") { r = 0; g = 0.5; b = 0.3; }
          else if (cell.signal === "Short" && cell.dsr >= 0.85) { r = 1; g = 0.2; b = 0.2; }
          else if (cell.signal === "Short") { r = 0.6; g = 0.15; b = 0.15; }
          else { r = 0.2; g = 0.2; b = 0.22; }
        }
        colorArray[idx * 3] = r;
        colorArray[idx * 3 + 1] = g;
        colorArray[idx * 3 + 2] = b;

        idx++;
      }
    }

    meshRef.current.instanceMatrix.needsUpdate = true;
    meshRef.current.geometry.setAttribute("color", new THREE.InstancedBufferAttribute(colorArray, 3));
  });

  // Raycasting for hover
  const { raycaster, pointer, camera } = useThree();

  useFrame(() => {
    if (!meshRef.current) return;
    raycaster.setFromCamera(pointer, camera);
    const intersects = raycaster.intersectObject(meshRef.current);
    if (intersects.length > 0 && intersects[0].instanceId !== undefined) {
      const id = intersects[0].instanceId;
      const ti = id % tickers.length;
      const si = Math.floor(id / tickers.length);
      onHover(`${tickers[ti]}_${strats[si]}`);
    } else {
      onHover(null);
    }
  });

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, count]}>
      <boxGeometry args={[1, 1, 1]} />
      <meshStandardMaterial vertexColors roughness={0.4} metalness={0.1} />
    </instancedMesh>
  );
}

// ── Main page ──
export default function LiveScan() {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const [tickers, setTickers] = useState(DEFAULT_TICKERS.join(", "));
  const [strategies, setStrategies] = useState(DEFAULT_STRATS.join(", "));
  const [running, setRunning] = useState(false);
  const [completed, setCompleted] = useState(0);
  const [total, setTotal] = useState(0);
  const [tickerList, setTickerList] = useState<string[]>([]);
  const [stratList, setStratList] = useState<string[]>([]);
  const [lastEvent, setLastEvent] = useState("");
  const gridRef = useRef<Record<string, CellData>>({});
  const [gridSnapshot, setGridSnapshot] = useState<Record<string, CellData>>({});
  const eventSourceRef = useRef<EventSource | null>(null);
  const updateTimerRef = useRef<NodeJS.Timeout | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);

  const scheduleSnapshot = useCallback(() => {
    if (updateTimerRef.current) return;
    updateTimerRef.current = setTimeout(() => {
      setGridSnapshot({ ...gridRef.current });
      updateTimerRef.current = null;
    }, 250);
  }, []);

  const startScan = useCallback(() => {
    const tkList = tickers.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
    const stList = strategies.split(",").map(s => s.trim()).filter(Boolean);
    setTickerList(tkList); setStratList(stList); setTotal(tkList.length * stList.length);
    setCompleted(0); gridRef.current = {}; setGridSnapshot({}); setRunning(true);

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const params = new URLSearchParams({ tickers: tkList.join(","), strategies: stList.join(","), timeframe: "daily", commission_bps: "5", slippage_bps: "5" });
    const es = new EventSource(`${apiUrl}/api/market/strategy-scan-stream?${params}`);
    eventSourceRef.current = es;

    let totalRef = tkList.length * stList.length;
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "init") { setTickerList(data.tickers); setStratList(data.strategies); setTotal(data.n_tested); totalRef = data.n_tested; }
        else if (data.type === "result") {
          setCompleted(data.completed);
          gridRef.current[`${data.ticker}_${data.strategy}`] = {
            scanned: true, dsr: data.dsr ?? 0, excess_sharpe: data.excess_sharpe ?? 0,
            sharpe: data.sharpe ?? 0, signal: data.signal || "Flat", cagr: data.cagr ?? 0,
          };
          setLastEvent(`${data.ticker} ${STRAT_LABELS[data.strategy] || data.strategy}: ${data.signal} (α ${data.excess_sharpe})`);
          scheduleSnapshot();
        } else if (data.type === "skip" || data.type === "error") {
          setCompleted(data.completed);
          gridRef.current[`${data.ticker}_${data.strategy}`] = { scanned: true, dsr: 0, excess_sharpe: 0, sharpe: 0, signal: "Flat", cagr: 0 };
          scheduleSnapshot();
        } else if (data.type === "done") { setRunning(false); setGridSnapshot({ ...gridRef.current }); es.close(); }
      } catch { /* ignore */ }
    };
    es.onerror = () => { setRunning(false); es.close(); };
  }, [tickers, strategies, scheduleSnapshot]);

  const stopScan = useCallback(() => { eventSourceRef.current?.close(); setRunning(false); setGridSnapshot({ ...gridRef.current }); }, []);
  useEffect(() => () => { eventSourceRef.current?.close(); if (updateTimerRef.current) clearTimeout(updateTimerRef.current); }, []);

  const pct = total > 0 ? Math.round(completed / total * 100) : 0;
  const scanned = Object.values(gridSnapshot);
  const significant = scanned.filter(c => c.dsr >= 0.95).length;
  const activeLong = scanned.filter(c => c.signal === "Long" && c.dsr >= 0.85).length;
  const activeShort = scanned.filter(c => c.signal === "Short" && c.dsr >= 0.85).length;

  const hoveredCell = hoveredKey ? gridSnapshot[hoveredKey] : null;
  const camX = (tickerList.length * 0.6) / 2;
  const camZ = (stratList.length * 0.6) / 2;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Live Strategy Scan</h1>
        <p className="text-text-secondary text-sm mt-1">3D real-time scan. Columns rise as results stream in.</p>
      </div>

      <div className="card card-compact">
        <div className="space-y-2">
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="metric-label">Tickers ({tickers.split(",").filter(Boolean).length})</label>
              <textarea value={tickers} onChange={e => setTickers(e.target.value.toUpperCase())} rows={2}
                className="w-full mt-1 px-3 py-1.5 border border-border rounded-lg text-xs font-data bg-surface" />
            </div>
            <div className="flex-1">
              <label className="metric-label">Strategies ({strategies.split(",").filter(Boolean).length})</label>
              <textarea value={strategies} onChange={e => setStrategies(e.target.value)} rows={2}
                className="w-full mt-1 px-3 py-1.5 border border-border rounded-lg text-xs font-data bg-surface" />
            </div>
          </div>
          {!running ? (
            <button onClick={startScan} className="w-full py-2.5 bg-accent text-white font-bold rounded-lg hover:bg-accent-hover">
              Start Scan ({tickers.split(",").filter(Boolean).length} × {strategies.split(",").filter(Boolean).length} = {tickers.split(",").filter(Boolean).length * strategies.split(",").filter(Boolean).length})
            </button>
          ) : (
            <button onClick={stopScan} className="w-full py-2.5 bg-loss text-white font-bold rounded-lg hover:opacity-80">
              Stop ({pct}% — {completed}/{total})
            </button>
          )}
        </div>
      </div>

      {(running || completed > 0) && (
        <div className="card card-compact">
          <div className="flex flex-wrap gap-6 items-center">
            <div className="flex-1 min-w-[200px]">
              <div className="flex items-center gap-2 mb-1">
                {running && <div className="w-3 h-3 bg-accent rounded-full animate-pulse" />}
                <span className="text-xs font-data">{completed}/{total} ({pct}%)</span>
                {lastEvent && <span className="text-xs text-text-muted ml-2 truncate max-w-[300px]">{lastEvent}</span>}
              </div>
              <div className="w-full h-2 bg-surface-alt rounded-full overflow-hidden">
                <div className="h-full bg-accent rounded-full transition-all duration-200" style={{ width: `${pct}%` }} />
              </div>
            </div>
            <Metric label="Significant" value={String(significant)} />
            <Metric label="Long" value={String(activeLong)} />
            <Metric label="Short" value={String(activeShort)} />
          </div>
        </div>
      )}

      {/* 3D Scene */}
      {tickerList.length > 0 && stratList.length > 0 && (
        <div className="card p-0 overflow-hidden" style={{ height: 600, position: "relative" }}>
          <Canvas
            camera={{ position: [camX + 5, 4, camZ + 8], fov: 50 }}
            gl={{ antialias: true, alpha: false }}
            style={{ background: "#07080a" }}>
            <fog attach="fog" args={["#07080a", 8, 35]} />
            <ambientLight intensity={0.25} />
            <directionalLight position={[10, 12, 8]} intensity={0.7} color="#ffffff" />
            <pointLight position={[-5, 8, -3]} intensity={0.3} color="#4488ff" />
            <pointLight position={[camX * 2, 6, camZ * 2]} intensity={0.2} color="#ff4488" />

            {/* Ground */}
            <mesh rotation={[-Math.PI / 2, 0, 0]} position={[camX - 0.3, -0.01, camZ - 0.3]}>
              <planeGeometry args={[tickerList.length * 0.6 + 2, stratList.length * 0.6 + 2]} />
              <meshStandardMaterial color="#08090d" roughness={0.95} />
            </mesh>

            {/* Grid lines on ground */}
            {tickerList.map((_, i) => (
              <mesh key={`gx-${i}`} rotation={[-Math.PI / 2, 0, 0]} position={[i * 0.6, 0.001, camZ - 0.3]}>
                <planeGeometry args={[0.005, stratList.length * 0.6 + 1]} />
                <meshBasicMaterial color="#1a1d2e" />
              </mesh>
            ))}
            {stratList.map((_, i) => (
              <mesh key={`gz-${i}`} rotation={[-Math.PI / 2, 0, 0]} position={[camX - 0.3, 0.001, i * 0.6]}>
                <planeGeometry args={[tickerList.length * 0.6 + 1, 0.005]} />
                <meshBasicMaterial color="#1a1d2e" />
              </mesh>
            ))}

            {/* Instanced columns */}
            <ColumnField tickers={tickerList} strats={stratList} grid={gridSnapshot} onHover={setHoveredKey} />

            {/* Hover tooltip */}
            {hoveredKey && hoveredCell?.scanned && (() => {
              const si = hoveredKey.indexOf("_");
              const tk = hoveredKey.slice(0, si);
              const strat = hoveredKey.slice(si + 1);
              const ti = tickerList.indexOf(tk);
              const sti = stratList.indexOf(strat);
              if (ti < 0 || sti < 0) return null;
              const h = Math.abs(hoveredCell.excess_sharpe) * 0.8 + 0.3;
              return (
                <Html position={[ti * 0.6, h, sti * 0.6]} center style={{ pointerEvents: "none" }}>
                  <div className="bg-surface border border-border rounded-lg shadow-xl px-3 py-2 text-xs whitespace-nowrap">
                    <div className="font-bold">{tk} · {STRAT_LABELS[strat] || strat}</div>
                    <div className={`font-bold ${hoveredCell.signal === "Long" ? "text-gain" : hoveredCell.signal === "Short" ? "text-loss" : "text-text-muted"}`}>
                      {hoveredCell.signal}
                    </div>
                    <div className="font-data">α {hoveredCell.excess_sharpe} · DSR {(hoveredCell.dsr * 100).toFixed(0)}% · CAGR {hoveredCell.cagr}%</div>
                  </div>
                </Html>
              );
            })()}

            <OrbitControls makeDefault target={[camX - 0.3, 0.3, camZ - 0.3]}
              minPolarAngle={0.2} maxPolarAngle={Math.PI / 2.3} minDistance={3} maxDistance={30} />
          </Canvas>

          {/* Legend overlay */}
          <div className="absolute bottom-2 left-2 flex gap-3 text-[0.55rem] text-text-muted bg-black/70 rounded px-2 py-1 backdrop-blur-sm">
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: "#00ff96" }} />Long (sig)</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: "#008040" }} />Long</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: "#ff3333" }} />Short (sig)</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: "#993030" }} />Short</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: "#333338" }} />Flat</span>
          </div>
          <div className="absolute top-2 right-2 text-[0.55rem] text-text-muted bg-black/70 rounded px-2 py-1 backdrop-blur-sm">
            Drag to orbit · Scroll to zoom
          </div>
        </div>
      )}

      {/* Active signals */}
      {Object.keys(gridSnapshot).length > 0 && (() => {
        const active = Object.entries(gridSnapshot)
          .filter(([, c]) => c.scanned && c.signal !== "Flat" && c.dsr >= 0.85)
          .sort(([, a], [, b]) => b.excess_sharpe - a.excess_sharpe).slice(0, 15);
        if (active.length === 0) return null;
        return (
          <div className="card">
            <div className="metric-label mb-2">Active Signals (DSR ≥ 85%)</div>
            <div className="space-y-1">
              {active.map(([key, c], idx) => {
                const si = key.indexOf("_"); const tk = key.slice(0, si); const strat = key.slice(si + 1);
                return (
                  <div key={key} className={`flex items-center gap-3 px-3 py-1.5 rounded border text-sm font-data ${idx === 0 ? "border-accent bg-accent-light" : "border-border"}`}>
                    <span className="text-text-muted text-xs w-5">{idx + 1}</span>
                    <span className="font-bold w-12">{tk}</span>
                    <span className="text-text-muted w-16 text-xs">{STRAT_LABELS[strat] || strat}</span>
                    <span className={`font-bold w-12 ${c.signal === "Long" ? "text-gain" : "text-loss"}`}>{c.signal}</span>
                    <span className={`font-bold ${c.excess_sharpe > 0 ? "text-gain" : "text-loss"}`}>α {c.excess_sharpe}</span>
                    <span className="text-xs text-text-muted">DSR {(c.dsr * 100).toFixed(0)}%</span>
                    <span className="text-xs text-text-muted">CAGR {c.cagr}%</span>
                    {idx === 0 && <span className="badge badge-gain text-[0.5rem] ml-auto">TOP</span>}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}
    </div>
  );
}
