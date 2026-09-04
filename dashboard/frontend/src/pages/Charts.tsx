import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { fetchCandles, TIMEFRAMES, type Candle, type CandlesPayload, type Timeframe } from "../api";

const POLL_MS = 15 * 60 * 1000;
const UP = "#26a69a";
const DOWN = "#ef5350";

const candleOptions = {
  upColor: UP,
  downColor: DOWN,
  borderUpColor: UP,
  borderDownColor: DOWN,
  wickUpColor: UP,
  wickDownColor: DOWN,
};

function toCandleData(rows: Candle[]) {
  return rows.map((row) => ({
    time: row.time as UTCTimestamp,
    open: row.open,
    high: row.high,
    low: row.low,
    close: row.close,
  }));
}

export default function Charts() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const oiRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const fundRef = useRef<ISeriesApi<"Line"> | null>(null);
  const [tf, setTf] = useState<Timeframe>("15dk");
  const [error, setError] = useState("");
  const [count, setCount] = useState(0);
  const fittedRef = useRef(false);

  useEffect(() => {
    fittedRef.current = false;
  }, [tf]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0b0b0d" },
        textColor: "#8b8f97",
        fontFamily: "Inter, sans-serif",
        panes: { separatorColor: "#27272a", separatorHoverColor: "#3f3f46" },
      },
      grid: {
        vertLines: { color: "#18181b" },
        horzLines: { color: "#18181b" },
      },
      rightPriceScale: { borderColor: "#27272a" },
      timeScale: {
        borderColor: "#27272a",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { vertLine: { color: "#52525b" }, horzLine: { color: "#52525b" } },
    });

    const price = chart.addSeries(CandlestickSeries, candleOptions);
    chart.addPane();
    const oi = chart.addSeries(CandlestickSeries, candleOptions, 1);
    chart.addPane();
    const funding = chart.addSeries(
      LineSeries,
      { color: "#60a5fa", lineWidth: 1, priceLineVisible: false },
      2,
    );

    const panes = chart.panes();
    panes[0]?.setStretchFactor(3);
    panes[1]?.setStretchFactor(2);
    panes[2]?.setStretchFactor(0.8);

    oi.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });
    funding.priceScale().applyOptions({ scaleMargins: { top: 0.15, bottom: 0.15 } });

    chartRef.current = chart;
    priceRef.current = price;
    oiRef.current = oi;
    fundRef.current = funding;

    return () => {
      chart.remove();
      chartRef.current = null;
      priceRef.current = null;
      oiRef.current = null;
      fundRef.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data: CandlesPayload = await fetchCandles(tf);
        if (cancelled) return;
        priceRef.current?.setData(toCandleData(data.price));
        oiRef.current?.setData(toCandleData(data.oi));
        fundRef.current?.setData(
          data.funding.map((row) => ({ time: row.time as UTCTimestamp, value: row.value })),
        );
        if (!fittedRef.current) {
          chartRef.current?.timeScale().fitContent();
          fittedRef.current = true;
        }
        setCount(data.price.length);
        setError("");
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Grafik yüklenemedi");
      }
    }

    void load();
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [tf]);

  return (
    <div className="flex h-[calc(100vh-49px)] flex-col px-6 py-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Grafik</h1>
          <p className="text-xs text-zinc-500">
            Fiyat · OI · funding · {count} mum · {tf}
          </p>
        </div>
        <div className="flex rounded-lg border border-zinc-800 p-0.5">
          {TIMEFRAMES.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setTf(item)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                tf === item ? "bg-zinc-100 text-zinc-950" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>
      {error ? <p className="mb-2 text-sm text-down">{error}</p> : null}
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-zinc-800">
        <div className="pointer-events-none absolute top-2 left-3 z-10 text-[11px] tracking-wide text-zinc-500 uppercase">
          Fiyat
        </div>
        <div className="pointer-events-none absolute top-[52%] left-3 z-10 text-[11px] tracking-wide text-zinc-500 uppercase">
          Open interest
        </div>
        <div className="pointer-events-none absolute bottom-8 left-3 z-10 text-[11px] tracking-wide text-zinc-500 uppercase">
          Funding %
        </div>
        <div ref={containerRef} className="h-full w-full" />
      </div>
    </div>
  );
}