"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useTheme } from "next-themes";

const CHARS = "01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン$¥€£ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<>{}[]|/\\+=@#%&";

interface Column {
  y: number;
  speed: number;
  brightness: number;
  tailLen: number;
  chars: string[];
  lastSwap: number;
  startDelay: number;  // #2: staggered boot — frames to wait before starting
  active: boolean;
}

export function MatrixLoader({ loading, status }: { loading: boolean; status?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const columnsRef = useRef<Column[]>([]);
  const phaseRef = useRef<"boot" | "steady" | "done">("boot");
  const frameRef = useRef(0);
  const doneFrameRef = useRef(0);
  const { theme } = useTheme();
  const [visible, setVisible] = useState(false);
  const [fading, setFading] = useState(false);

  // #3: Typing effect for status text
  const [displayStatus, setDisplayStatus] = useState("");
  const [panelVisible, setPanelVisible] = useState(false);
  const targetStatusRef = useRef("INITIALIZING SYSTEM...");
  const typeIdxRef = useRef(0);

  const isMatrix = theme === "matrix";
  const isAnyTheme = true;  // show loading for all themes

  useEffect(() => {
    if (status && status !== targetStatusRef.current) {
      targetStatusRef.current = status;
      typeIdxRef.current = 0;
      setDisplayStatus("");
    }
  }, [status]);

  // Typing interval
  useEffect(() => {
    if (!visible) return;
    const iv = setInterval(() => {
      const target = targetStatusRef.current;
      if (typeIdxRef.current < target.length) {
        typeIdxRef.current++;
        setDisplayStatus(target.slice(0, typeIdxRef.current));
      }
    }, 35);
    return () => clearInterval(iv);
  }, [visible]);

  useEffect(() => {
    if (loading && isAnyTheme) {
      setVisible(true);
      setFading(false);
      setPanelVisible(false);
      phaseRef.current = "boot";
      frameRef.current = 0;
      doneFrameRef.current = 0;
      // Fade panel in after rain starts
      const showPanel = setTimeout(() => setPanelVisible(true), 600);
      return () => clearTimeout(showPanel);
    } else if (!loading && visible) {
      phaseRef.current = "done";
      doneFrameRef.current = 0;
      // Fade panel out first, then rain
      setPanelVisible(false);
      const t = setTimeout(() => {
        setFading(true);
        const t2 = setTimeout(() => { setVisible(false); setFading(false); }, 600);
        return () => clearTimeout(t2);
      }, 800);
      return () => clearTimeout(t);
    }
  }, [loading, isMatrix, visible]);

  const initColumns = useCallback((width: number, height: number) => {
    const fontSize = 14;
    const count = Math.floor(width / fontSize);
    const cols: Column[] = [];
    for (let i = 0; i < count; i++) {
      const tailLen = 25 + Math.floor(Math.random() * 30);
      const chars: string[] = [];
      for (let j = 0; j < tailLen + 5; j++) {
        chars.push(CHARS[Math.floor(Math.random() * CHARS.length)]);
      }
      cols.push({
        y: -tailLen - Math.random() * 30,
        speed: 0.4 + Math.random() * 0.6,
        brightness: 0.4 + Math.random() * 0.6,
        tailLen,
        chars,
        lastSwap: 0,
        startDelay: Math.floor(Math.abs(i - count / 2) * 0.3 + Math.random() * 20),
        active: false,
      });
    }
    columnsRef.current = cols;
  }, []);

  const sizeRef = useRef({ w: 0, h: 0 });

  useEffect(() => {
    if (!visible || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const fontSize = 14;
    const panelHalfW = 160;
    const panelHalfH = 50;

    function setupCanvas() {
      const dpr = window.devicePixelRatio || 1;
      // Size to the overlay container (absolute inset-0 div)
      const container = canvas.parentElement;
      if (!container) return { w: 300, h: 300 };
      const rect = container.getBoundingClientRect();
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = "100%";
      canvas.style.height = "100%";
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      sizeRef.current = { w, h };
      return { w, h };
    }

    const { w, h } = setupCanvas();
    initColumns(w, h);

    let lastTime = 0;

    function draw(time: number) {
      if (!ctx || !canvas) return;

      const phase = phaseRef.current;
      const interval = phase === "boot" ? 40 : phase === "done" ? 35 : 55;
      if (time - lastTime < interval) {
        animRef.current = requestAnimationFrame(draw);
        return;
      }
      lastTime = time;
      frameRef.current++;
      if (phase === "done") doneFrameRef.current++;

      if (phase === "boot" && frameRef.current > 40) {
        phaseRef.current = "steady";
      }

      // Use live dimensions (updated on resize)
      const W = sizeRef.current.w;
      const H = sizeRef.current.h;
      const centerX = W / 2;
      const centerY = H / 2;

      // Partial fade — very slow = dense, layered rain
      ctx.fillStyle = "rgba(0, 0, 0, 0.06)";
      ctx.fillRect(0, 0, W, H);

      const columns = columnsRef.current;
      // #5: Done phase — slow deceleration, not acceleration
      const speedMult = phase === "boot" ? 1.4 : phase === "done" ? Math.max(0.3, 1.0 - doneFrameRef.current * 0.03) : 1.0;

      for (let i = 0; i < columns.length; i++) {
        const col = columns[i];

        // #2: Staggered boot — columns activate over time
        if (!col.active) {
          if (frameRef.current >= col.startDelay) {
            col.active = true;
          } else {
            continue;
          }
        }

        const x = i * fontSize + 2;
        const headRow = Math.floor(col.y);

        // Swap chars
        if (frameRef.current - col.lastSwap > 3 + Math.floor(Math.random() * 5)) {
          const swapIdx = Math.floor(Math.random() * col.chars.length);
          col.chars[swapIdx] = CHARS[Math.floor(Math.random() * CHARS.length)];
          col.lastSwap = frameRef.current;
        }

        for (let j = 0; j < col.tailLen; j++) {
          const row = headRow - j;
          const charY = row * fontSize;
          if (charY < -fontSize || charY > H + fontSize) continue;

          const charIdx = (row + i * 7) % col.chars.length;
          const ch = col.chars[Math.abs(charIdx) % col.chars.length];
          const tailFrac = j / col.tailLen;
          const fadeFrac = tailFrac < 0.6 ? 0 : (tailFrac - 0.6) / 0.4;

          // #1: Dim characters that fall behind the center panel (not remove — just dim)
          let dimMult = 1.0;
          if (Math.abs(x - centerX) < panelHalfW && Math.abs(charY - centerY) < panelHalfH) {
            continue;  // skip — no characters behind the text panel
          }

          if (j === 0) {
            // Head — bold white with strong glow
            ctx.font = `bold ${fontSize + 2}px "Courier New", monospace`;
            ctx.shadowColor = "#00ff41";
            ctx.shadowBlur = 10;
            ctx.fillStyle = "#ffffff";
            ctx.fillText(ch, x, charY);
            // Double-strike for extra brightness
            ctx.shadowBlur = 4;
            ctx.fillText(ch, x, charY);
            ctx.shadowBlur = 0;
          } else if (j <= 4) {
            // Near head — bold bright green with glow
            const a = Math.min(1, col.brightness * 1.3);
            ctx.font = `bold ${fontSize}px "Courier New", monospace`;
            ctx.shadowColor = "#00ff41";
            ctx.shadowBlur = 4;
            ctx.fillStyle = `rgba(0, 255, 65, ${a.toFixed(2)})`;
            ctx.fillText(ch, x, charY);
            ctx.shadowBlur = 0;
          } else {
            // Trail — regular weight, fading
            const a = col.brightness * (1 - fadeFrac * 0.85);
            if (a < 0.04) continue;
            const green = Math.floor(255 - fadeFrac * 100);
            ctx.font = `${fontSize}px "Courier New", monospace`;
            ctx.fillStyle = `rgba(0, ${green}, ${Math.floor(60 - fadeFrac * 30)}, ${a.toFixed(2)})`;
            ctx.fillText(ch, x, charY);
          }
        }

        col.y += col.speed * speedMult;

        if ((headRow - col.tailLen) * fontSize > H) {
          // #5: In done phase, some columns don't restart (rain thins out)
          if (phase === "done" && Math.random() < 0.3) {
            col.active = false;
            continue;
          }
          col.y = -col.tailLen - Math.random() * 10;
          col.speed = 0.4 + Math.random() * 0.6;
          col.brightness = 0.4 + Math.random() * 0.6;
          col.tailLen = 25 + Math.floor(Math.random() * 30);
          for (let j = 0; j < col.chars.length; j++) {
            col.chars[j] = CHARS[Math.floor(Math.random() * CHARS.length)];
          }
        }
      }

      animRef.current = requestAnimationFrame(draw);
    }

    animRef.current = requestAnimationFrame(draw);

    const handleResize = () => {
      const { w, h } = setupCanvas();
      initColumns(w, h);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener("resize", handleResize);
    };
  }, [visible, initColumns]);

  if (!visible) return null;

  // Non-matrix themes: clean loading overlay with spinner
  if (!isMatrix) {
    return (
      <div
        className={`absolute inset-0 z-[60] flex flex-col items-center justify-center transition-opacity duration-500 rounded-md ${fading ? "opacity-0" : "opacity-100"}`}
        style={{ background: "var(--color-surface)", borderRadius: "6px" }}
      >
        <div className="w-10 h-10 border-3 border-accent border-t-transparent rounded-full animate-spin mb-3" />
        <div className="text-sm font-semibold text-text-secondary">Analyzing...</div>
        <div className="text-xs text-text-muted mt-1">{displayStatus}</div>
      </div>
    );
  }

  // Matrix theme: full rain animation
  return (
    <div
      className={`absolute inset-0 z-[60] flex items-center justify-center transition-opacity duration-600 ${fading ? "opacity-0" : "opacity-100"}`}
      style={{ background: "#000000", borderRadius: "6px", overflow: "hidden" }}
    >
      <canvas ref={canvasRef} className="absolute inset-0" />
      <div className="absolute inset-0 pointer-events-none" style={{
        background: `
          linear-gradient(to right, var(--color-surface, #000) 0%, rgba(0,0,0,0.6) 8%, transparent 20%, transparent 80%, rgba(0,0,0,0.6) 92%, var(--color-surface, #000) 100%),
          linear-gradient(to bottom, var(--color-surface, #000) 0%, rgba(0,0,0,0.6) 8%, transparent 22%, transparent 78%, rgba(0,0,0,0.6) 92%, var(--color-surface, #000) 100%)
        `,
        zIndex: 5,
      }} />
      <div
        className={`relative z-10 text-center px-8 py-5 rounded transition-all duration-500 ${panelVisible ? "opacity-100 scale-100" : "opacity-0 scale-95"}`}
        style={{
          border: "1px solid rgba(0, 255, 65, 0.15)",
          background: "rgba(0, 0, 0, 0.75)",
          boxShadow: "0 0 30px rgba(0, 255, 65, 0.06), inset 0 0 20px rgba(0, 0, 0, 0.5)",
        }}>
        <div className="font-mono text-base font-bold tracking-[0.3em]"
          style={{ color: "#00ff41", textShadow: "0 0 8px #00ff41, 0 0 20px #008f11" }}>
          AI STATCHARTS
        </div>
        <div className="w-full h-px my-2" style={{ background: "linear-gradient(90deg, transparent, #00ff41, transparent)" }} />
        <div className="font-mono text-[0.6rem] tracking-wider"
          style={{ color: "#00ff41", textShadow: "0 0 6px #00ff41" }}>
          {displayStatus}
          <span className="inline-block w-1.5 h-3 ml-1 align-middle animate-pulse" style={{ background: "#00ff41", boxShadow: "0 0 4px #00ff41" }} />
        </div>
      </div>
    </div>
  );
}
