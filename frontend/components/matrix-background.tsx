"use client";

import { useEffect, useRef, useState } from "react";
import { useTheme } from "next-themes";

const CHARS = "01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホ0123456789ABCDEF";

/**
 * Persistent Matrix rain background. Renders behind all content when matrix theme is active.
 * Lighter/subtler than the loading screen — visible but not distracting.
 */
export function MatrixBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const { theme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    if (theme !== "matrix" || !canvasRef.current || !mounted) {
      cancelAnimationFrame(animRef.current);
      return;
    }

    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const fontSize = 14;
    let W = 0, H = 0;

    interface Col { y: number; speed: number; brightness: number; }
    let columns: Col[] = [];

    function setup() {
      const dpr = window.devicePixelRatio || 1;
      W = window.innerWidth;
      H = window.innerHeight;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = W + "px";
      canvas.style.height = H + "px";
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      const count = Math.floor(W / fontSize);
      columns = [];
      for (let i = 0; i < count; i++) {
        columns.push({
          y: Math.random() * H / fontSize,
          speed: 0.3 + Math.random() * 0.5,
          brightness: 0.03 + Math.random() * 0.08,  // very dim — background, not foreground
        });
      }
    }
    setup();

    let last = 0;
    function draw(time: number) {
      if (!ctx) return;
      if (time - last < 80) { // ~12fps — low CPU, slow rain
        animRef.current = requestAnimationFrame(draw);
        return;
      }
      last = time;

      // Very slow fade — characters persist a long time
      ctx.fillStyle = "rgba(0, 0, 0, 0.04)";
      ctx.fillRect(0, 0, W, H);

      ctx.font = `${fontSize}px "Courier New", monospace`;

      for (let i = 0; i < columns.length; i++) {
        const col = columns[i];
        const x = i * fontSize;
        const y = col.y * fontSize;

        if (y > 0 && y < H) {
          // Head character — slightly brighter
          const ch = CHARS[Math.floor(Math.random() * CHARS.length)];
          ctx.fillStyle = `rgba(0, 255, 65, ${(col.brightness * 2.5).toFixed(3)})`;
          ctx.fillText(ch, x, y);

          // Trail char
          if (y - fontSize > 0) {
            const ch2 = CHARS[Math.floor(Math.random() * CHARS.length)];
            ctx.fillStyle = `rgba(0, 255, 65, ${col.brightness.toFixed(3)})`;
            ctx.fillText(ch2, x, y - fontSize);
          }
        }

        col.y += col.speed;
        if (col.y * fontSize > H + 50) {
          col.y = -Math.random() * 10;
          col.speed = 0.3 + Math.random() * 0.5;
          col.brightness = 0.03 + Math.random() * 0.08;
        }
      }

      animRef.current = requestAnimationFrame(draw);
    }

    animRef.current = requestAnimationFrame(draw);

    const onResize = () => setup();
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener("resize", onResize);
    };
  }, [theme, mounted]);

  // Don't render on server or before mount — prevents hydration mismatch
  if (!mounted || theme !== "matrix") return null;

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none"
      style={{ zIndex: 0 }}
    />
  );
}
