import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { EstimatedMap, LiqLevel } from "../api";
import { formatBtc, formatPrice } from "../format";

const LONG = "#ef5350";
const SHORT = "#26a69a";

type Hover = { x: number; y: number; level: LiqLevel } | null;

export default function EstimatedHeatmap({
  data,
  rangePct,
}: {
  data: EstimatedMap;
  rangePct: number | null;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hover, setHover] = useState<Hover>(null);

  const visible = useMemo(() => {
    const price = data.current_price;
    if (rangePct == null || price == null) return data.levels;
    const lo = price * (1 - rangePct / 100);
    const hi = price * (1 + rangePct / 100);
    return data.levels.filter((l) => l.price >= lo && l.price <= hi);
  }, [data, rangePct]);

  const scale = useMemo(() => {
    const prices = visible.map((l) => l.price);
    if (data.current_price != null) {
      if (rangePct == null) prices.push(data.current_price);
      else {
        prices.push(data.current_price * (1 - rangePct / 100));
        prices.push(data.current_price * (1 + rangePct / 100));
      }
    }
    if (!prices.length) return null;
    const minP = Math.min(...prices);
    const maxP = Math.max(...prices);
    return { minP, maxP, span: maxP - minP || 1 };
  }, [visible, data.current_price, rangePct]);

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

      const pad = { top: 16, right: 78, bottom: 28, left: 16 };
      const innerW = width - pad.left - pad.right;
      const innerH = height - pad.top - pad.bottom;
      const midX = pad.left + innerW / 2;

      if (!scale || innerW <= 0 || innerH <= 0) {
        ctx.fillStyle = "#71717a";
        ctx.font = "12px Inter, sans-serif";
        ctx.fillText(visible.length ? "Küme yok" : "Bu aralıkta küme yok", 20, 32);
        return;
      }

      const yOf = (price: number) => pad.top + ((scale.maxP - price) / scale.span) * innerH;
      const maxAmt = Math.max(...visible.map((l) => l.amount_btc), 1e-9);
      const maxBar = innerW / 2 - 8;

      ctx.strokeStyle = "#1f1f23";
      ctx.beginPath();
      for (let i = 0; i <= 6; i++) {
        const y = pad.top + (innerH * i) / 6;
        ctx.moveTo(pad.left, y);
        ctx.lineTo(pad.left + innerW, y);
      }
      ctx.stroke();

      ctx.strokeStyle = "#27272a";
      ctx.beginPath();
      ctx.moveTo(midX, pad.top);
      ctx.lineTo(midX, pad.top + innerH);
      ctx.stroke();

      ctx.fillStyle = "#71717a";
      ctx.font = "10px Inter, sans-serif";
      for (let i = 0; i <= 6; i++) {
        const price = scale.maxP - (scale.span * i) / 6;
        const y = pad.top + (innerH * i) / 6;
        ctx.fillText(`$${formatPrice(price)}`, width - pad.right + 6, y + 3);
      }

      const bandH = Math.max(1.5, Math.min(8, innerH / Math.max(visible.length, 40)));
      for (const level of visible) {
        const y = yOf(level.price);
        const w = Math.max(2, (level.amount_btc / maxAmt) * maxBar);
        const intensity = 0.22 + 0.78 * (level.amount_btc / maxAmt);
        const longSide = level.side.toLowerCase().includes("long");
        ctx.globalAlpha = intensity;
        ctx.fillStyle = longSide ? LONG : SHORT;
        if (longSide) {
          ctx.fillRect(midX - w, y - bandH / 2, w, bandH);
        } else {
          ctx.fillRect(midX, y - bandH / 2, w, bandH);
        }
      }
      ctx.globalAlpha = 1;

      if (data.current_price != null) {
        const y = yOf(data.current_price);
        ctx.strokeStyle = "#e4e4e7";
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(width - pad.right, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#e4e4e7";
        ctx.font = "11px Inter, sans-serif";
        ctx.fillText(`$${formatPrice(data.current_price)}`, width - pad.right + 6, y + 4);
      }

      ctx.fillStyle = "#71717a";
      ctx.font = "10px Inter, sans-serif";
      ctx.fillText("LONG →", pad.left, height - 10);
      ctx.fillText("← SHORT", width - pad.right - 52, height - 10);
    };

    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [data, visible, scale]);

  function onMove(event: MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas || !scale || !visible.length) return;
    const rect = canvas.getBoundingClientRect();
    const y = event.clientY - rect.top;
    const height = rect.height;
    const padTop = 16;
    const padBottom = 28;
    const innerH = height - padTop - padBottom;
    const price = scale.maxP - ((y - padTop) / innerH) * scale.span;
    let best: LiqLevel | null = null;
    let bestDist = Infinity;
    for (const level of visible) {
      const dist = Math.abs(level.price - price);
      if (dist < bestDist) {
        bestDist = dist;
        best = level;
      }
    }
    if (best && bestDist / scale.span < 0.02) {
      setHover({ x: event.clientX - rect.left, y, level: best });
    } else {
      setHover(null);
    }
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
          className="pointer-events-none absolute rounded-md border border-zinc-700 bg-zinc-950/95 px-2 py-1 text-[11px] text-zinc-200"
          style={{ left: hover.x + 12, top: hover.y + 12 }}
        >
          ${formatPrice(hover.level.price)} · {hover.level.side} · {formatBtc(hover.level.amount_btc)} BTC
        </div>
      ) : null}
    </div>
  );
}
