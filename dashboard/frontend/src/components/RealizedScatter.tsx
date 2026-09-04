import { useEffect, useRef, useState, type MouseEvent } from "react";
import type { RealizedLiq } from "../api";
import { formatPrice, formatUsdCompact } from "../format";

const LONG = "#ef5350";
const SHORT = "#26a69a";

type Hover = { x: number; y: number; event: RealizedLiq } | null;

function formatAxisTime(ts: number): string {
  const d = new Date(ts * 1000);
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${dd}.${mm} ${hh}:${mi}`;
}

export default function RealizedScatter({ events }: { events: RealizedLiq[] }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hover, setHover] = useState<Hover>(null);
  const layoutRef = useRef<{
    points: Array<{ x: number; y: number; r: number; event: RealizedLiq }>;
  } | null>(null);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;

    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const width = wrap.clientWidth;
      const height = wrap.clientHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#0b0b0d";
      ctx.fillRect(0, 0, width, height);

      const usable = events.filter((e) => e.time && e.gercek_fiyat);
      if (!usable.length) {
        ctx.fillStyle = "#71717a";
        ctx.font = "12px Inter, sans-serif";
        ctx.fillText("Kayıt yok", 20, 32);
        layoutRef.current = null;
        return;
      }

      const pad = { top: 16, right: 16, bottom: 36, left: 72 };
      const times = usable.map((e) => e.time as number);
      const prices = usable.map((e) => e.gercek_fiyat);
      const minT = Math.min(...times);
      const maxT = Math.max(...times);
      const minP = Math.min(...prices);
      const maxP = Math.max(...prices);
      const tSpan = maxT - minT || 1;
      const pSpan = maxP - minP || 1;
      const innerW = width - pad.left - pad.right;
      const innerH = height - pad.top - pad.bottom;
      const xOf = (t: number) => pad.left + ((t - minT) / tSpan) * innerW;
      const yOf = (p: number) => pad.top + ((maxP - p) / pSpan) * innerH;
      const maxN = Math.max(...usable.map((e) => e.notional_usd || 0), 1);

      ctx.strokeStyle = "#1f1f23";
      ctx.beginPath();
      for (let i = 0; i <= 4; i++) {
        const y = pad.top + (innerH * i) / 4;
        ctx.moveTo(pad.left, y);
        ctx.lineTo(pad.left + innerW, y);
      }
      ctx.stroke();

      ctx.strokeStyle = "#27272a";
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, pad.top + innerH);
      ctx.lineTo(pad.left + innerW, pad.top + innerH);
      ctx.stroke();

      ctx.fillStyle = "#71717a";
      ctx.font = "10px Inter, sans-serif";
      for (let i = 0; i <= 4; i++) {
        const price = maxP - (pSpan * i) / 4;
        const y = pad.top + (innerH * i) / 4;
        ctx.fillText(`$${formatPrice(price)}`, 8, y + 3);
      }
      ctx.fillText(formatAxisTime(minT), pad.left, height - 12);
      ctx.fillText(formatAxisTime(maxT), pad.left + innerW - 72, height - 12);

      const points: Array<{ x: number; y: number; r: number; event: RealizedLiq }> = [];
      for (const event of usable) {
        const x = xOf(event.time as number);
        const y = yOf(event.gercek_fiyat);
        const r = 2 + Math.sqrt((event.notional_usd || 0) / maxN) * 10;
        ctx.globalAlpha = 0.75;
        ctx.fillStyle = event.yon?.toLowerCase() === "long" ? LONG : SHORT;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
        points.push({ x, y, r, event });
      }
      ctx.globalAlpha = 1;
      layoutRef.current = { points };
    };

    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [events]);

  function onMove(event: MouseEvent<HTMLCanvasElement>) {
    const layout = layoutRef.current;
    const canvas = canvasRef.current;
    if (!layout || !canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let best: Hover = null;
    let bestD = 14;
    for (const point of layout.points) {
      const d = Math.hypot(point.x - x, point.y - y);
      if (d < Math.max(bestD, point.r + 4) && d < bestD + 8) {
        bestD = d;
        best = { x, y, event: point.event };
      }
    }
    setHover(best);
  }

  return (
    <div ref={wrapRef} className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        className="h-full w-full"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      />
      {hover ? (
        <div
          className="pointer-events-none absolute z-10 max-w-xs rounded-md border border-zinc-700 bg-zinc-950/95 px-2 py-1.5 text-[11px] text-zinc-200"
          style={{ left: hover.x + 12, top: hover.y + 12 }}
        >
          <p>
            {hover.event.tarih} {hover.event.saat} · {hover.event.yon} {hover.event.kontrat_tipi}
          </p>
          <p>
            ${formatPrice(hover.event.gercek_fiyat)} · {formatUsdCompact(hover.event.notional_usd)}
          </p>
          <p className="text-zinc-400">
            tahmin {hover.event.tahmini_kume_fiyat != null ? `$${formatPrice(hover.event.tahmini_kume_fiyat)}` : "—"}
            {hover.event.fark_yuzde != null ? ` · fark ${hover.event.fark_yuzde.toFixed(3)}%` : ""}
          </p>
        </div>
      ) : null}
    </div>
  );
}
