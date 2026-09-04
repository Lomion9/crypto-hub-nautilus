import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { fetchHistory, TIMEFRAMES, type HistoryPayload, type Timeframe } from "../api";
import { formatPrice, localIsoDate, localIsoDaysAgo, signedClass } from "../format";

const LINE = { lineWidth: 1 as const, priceLineVisible: false };

function MiniChart({
  points,
  color,
}: {
  points: Array<{ time: number; value: number }>;
  color: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0b0b0d" },
        textColor: "#8b8f97",
        fontFamily: "Inter, sans-serif",
      },
      grid: { vertLines: { color: "#18181b" }, horzLines: { color: "#18181b" } },
      rightPriceScale: { borderColor: "#27272a" },
      timeScale: { borderColor: "#27272a", timeVisible: true, secondsVisible: false },
    });
    const series = chart.addSeries(LineSeries, { color, ...LINE });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [color]);

  useEffect(() => {
    seriesRef.current?.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    chartRef.current?.timeScale().fitContent();
  }, [points]);

  return <div ref={ref} className="h-40 w-full" />;
}

export default function History() {
  const [start, setStart] = useState(localIsoDaysAgo(14));
  const [end, setEnd] = useState(localIsoDate());
  const [tfFilter, setTfFilter] = useState<Timeframe | "all">("all");
  const [data, setData] = useState<HistoryPayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const next = await fetchHistory(start, end);
        if (!cancelled) {
          setData(next);
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Geçmiş yüklenemedi");
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [start, end]);

  const signals =
    data?.signals.filter((row) => (tfFilter === "all" ? true : row.tf === tfFilter)) ?? [];

  const oi = useMemo(
    () =>
      (data?.series ?? [])
        .filter((r) => r.oi_btc != null)
        .map((r) => ({ time: r.time, value: r.oi_btc as number })),
    [data],
  );
  const funding = useMemo(
    () =>
      (data?.series ?? [])
        .filter((r) => r.funding_pct != null)
        .map((r) => ({ time: r.time, value: r.funding_pct as number })),
    [data],
  );
  const cvdSpot = useMemo(
    () =>
      (data?.series ?? [])
        .filter((r) => r.cvd_spot_btc != null)
        .map((r) => ({ time: r.time, value: r.cvd_spot_btc as number })),
    [data],
  );
  const cvdPerp = useMemo(
    () =>
      (data?.series ?? [])
        .filter((r) => r.cvd_perp_btc != null)
        .map((r) => ({ time: r.time, value: r.cvd_perp_btc as number })),
    [data],
  );

  const stats = data?.stats;

  return (
    <div className="px-6 py-6">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Geçmiş / analiz</h1>
          <p className="text-xs text-zinc-500">{data?.series.length ?? 0} bar · {signals.length} sinyal</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="rounded-md border border-zinc-800 bg-zinc-950 px-2 py-1 text-zinc-200"
          />
          <span>—</span>
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className="rounded-md border border-zinc-800 bg-zinc-950 px-2 py-1 text-zinc-200"
          />
        </div>
      </div>

      {error ? <p className="mb-3 text-sm text-down">{error}</p> : null}

      <section className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
          <p className="text-[11px] text-zinc-500 uppercase">Sinyal</p>
          <p className="mt-1 text-xl text-zinc-100">{stats?.count ?? 0}</p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
          <p className="text-[11px] text-zinc-500 uppercase">Win / loss</p>
          <p className="mt-1 text-xl text-zinc-100">
            {stats?.wins ?? 0} / {stats?.losses ?? 0}
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
          <p className="text-[11px] text-zinc-500 uppercase">Win rate</p>
          <p className="mt-1 text-xl text-zinc-100">
            {stats?.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : "—"}
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
          <p className="text-[11px] text-zinc-500 uppercase">Ort. %</p>
          <p className={`mt-1 text-xl ${signedClass(stats?.avg_pct)}`}>
            {stats?.avg_pct != null ? `${stats.avg_pct >= 0 ? "+" : ""}${stats.avg_pct.toFixed(2)}%` : "—"}
          </p>
        </div>
      </section>

      <div className="mb-6 grid gap-3 lg:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 p-3">
          <p className="mb-1 text-[11px] tracking-wide text-zinc-500 uppercase">OI (BTC)</p>
          <MiniChart points={oi} color="#a1a1aa" />
        </div>
        <div className="rounded-xl border border-zinc-800 p-3">
          <p className="mb-1 text-[11px] tracking-wide text-zinc-500 uppercase">Funding</p>
          <MiniChart points={funding} color="#60a5fa" />
        </div>
        <div className="rounded-xl border border-zinc-800 p-3">
          <p className="mb-1 text-[11px] tracking-wide text-zinc-500 uppercase">CVD spot</p>
          <MiniChart points={cvdSpot} color="#26a69a" />
        </div>
        <div className="rounded-xl border border-zinc-800 p-3">
          <p className="mb-1 text-[11px] tracking-wide text-zinc-500 uppercase">CVD perp</p>
          <MiniChart points={cvdPerp} color="#ef5350" />
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-1">
        <button
          type="button"
          onClick={() => setTfFilter("all")}
          className={`rounded-md px-2 py-1 text-xs ${tfFilter === "all" ? "bg-zinc-100 text-zinc-950" : "text-zinc-500"}`}
        >
          tümü
        </button>
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            type="button"
            onClick={() => setTfFilter(tf)}
            className={`rounded-md px-2 py-1 text-xs ${tfFilter === tf ? "bg-zinc-100 text-zinc-950" : "text-zinc-500"}`}
          >
            {tf}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto rounded-xl border border-zinc-800">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-zinc-800 text-zinc-500">
            <tr>
              <th className="px-3 py-2 font-medium">TF</th>
              <th className="px-3 py-2 font-medium">Kapanış</th>
              <th className="px-3 py-2 font-medium">Sinyal</th>
              <th className="px-3 py-2 font-medium">Yön</th>
              <th className="px-3 py-2 font-medium">Giriş</th>
              <th className="px-3 py-2 font-medium">Çıkış</th>
              <th className="px-3 py-2 font-medium">%</th>
              <th className="px-3 py-2 font-medium">Tip</th>
            </tr>
          </thead>
          <tbody>
            {signals.slice().reverse().map((row, idx) => (
              <tr key={`${row.tf}-${row.kapanis_tarih}-${row.kapanis_saat}-${idx}`} className="border-b border-zinc-900">
                <td className="px-3 py-2 text-zinc-300">{row.tf}</td>
                <td className="px-3 py-2 text-zinc-400">
                  {row.kapanis_tarih} {row.kapanis_saat}
                </td>
                <td className="px-3 py-2 text-zinc-200">{row.sinyal}</td>
                <td className="px-3 py-2">{row.yon}</td>
                <td className="px-3 py-2">${formatPrice(row.giris_fiyat)}</td>
                <td className="px-3 py-2">${formatPrice(row.cikis_fiyat)}</td>
                <td className={`px-3 py-2 ${signedClass(row.kar_yuzde)}`}>
                  {row.kar_yuzde != null ? `${row.kar_yuzde >= 0 ? "+" : ""}${row.kar_yuzde.toFixed(2)}` : "—"}
                </td>
                <td className="px-3 py-2 text-zinc-500">{row.kapanis_tipi || "—"}</td>
              </tr>
            ))}
            {!signals.length ? (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-zinc-500">
                  Bu aralıkta kapanan sinyal yok
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
