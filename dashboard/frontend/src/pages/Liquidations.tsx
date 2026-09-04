import { useEffect, useState } from "react";
import {
  fetchLiquidationMap,
  fetchRealizedLiquidations,
  type EstimatedMap,
  type RealizedLiq,
} from "../api";
import EstimatedHeatmap from "../components/EstimatedHeatmap";
import RealizedScatter from "../components/RealizedScatter";
import { formatBtc, localIsoDate, localIsoDaysAgo } from "../format";

const RANGES: Array<{ label: string; pct: number | null }> = [
  { label: "±5%", pct: 5 },
  { label: "±10%", pct: 10 },
  { label: "±20%", pct: 20 },
  { label: "Tümü", pct: null },
];

export default function Liquidations() {
  const [tab, setTab] = useState<"estimated" | "realized">("estimated");
  const [layer, setLayer] = useState<"linear" | "inverse">("linear");
  const [windowH, setWindowH] = useState<12 | 24>(12);
  const [rangePct, setRangePct] = useState<number | null>(10);
  const [map, setMap] = useState<EstimatedMap | null>(null);
  const [events, setEvents] = useState<RealizedLiq[]>([]);
  const [start, setStart] = useState(localIsoDaysAgo(7));
  const [end, setEnd] = useState(localIsoDate());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (tab !== "estimated") return;
    let cancelled = false;
    async function load() {
      try {
        const next = await fetchLiquidationMap(layer, windowH);
        if (!cancelled) {
          setMap(next);
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Harita yüklenemedi");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    setLoading(true);
    void load();
    const id = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [tab, layer, windowH]);

  useEffect(() => {
    if (tab !== "realized") return;
    let cancelled = false;
    async function load() {
      try {
        const next = await fetchRealizedLiquidations(start, end);
        if (!cancelled) {
          setEvents(next.events);
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Likidasyonlar yüklenemedi");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    setLoading(true);
    void load();
    const id = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [tab, start, end]);

  return (
    <div className="flex h-[calc(100vh-49px)] flex-col px-6 py-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Likidasyon</h1>
          <p className="text-xs text-zinc-500">
            {tab === "estimated"
              ? `${map?.levels.length ?? 0} tahmini küme · canlı hesap`
              : `${events.length} gerçekleşen event`}
          </p>
        </div>
        <div className="flex rounded-lg border border-zinc-800 p-0.5">
          <button
            type="button"
            onClick={() => setTab("estimated")}
            className={`rounded-md px-3 py-1 text-xs font-medium ${
              tab === "estimated" ? "bg-zinc-100 text-zinc-950" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Tahmini harita
          </button>
          <button
            type="button"
            onClick={() => setTab("realized")}
            className={`rounded-md px-3 py-1 text-xs font-medium ${
              tab === "realized" ? "bg-zinc-100 text-zinc-950" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Gerçekleşen
          </button>
        </div>
      </div>

      {tab === "estimated" ? (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {(["linear", "inverse"] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setLayer(item)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                layer === item ? "border-zinc-500 text-zinc-100" : "border-zinc-800 text-zinc-500"
              }`}
            >
              {item}
            </button>
          ))}
          {([12, 24] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setWindowH(item)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                windowH === item ? "border-zinc-500 text-zinc-100" : "border-zinc-800 text-zinc-500"
              }`}
            >
              {item}s
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-zinc-800" />
          {RANGES.map((item) => (
            <button
              key={item.label}
              type="button"
              onClick={() => setRangePct(item.pct)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                rangePct === item.pct ? "border-zinc-500 text-zinc-100" : "border-zinc-800 text-zinc-500"
              }`}
            >
              {item.label}
            </button>
          ))}
          {map?.guncel_oi?.[layer] != null ? (
            <span className="self-center text-xs text-zinc-500">
              OI {formatBtc(map.guncel_oi[layer], 0)} BTC
            </span>
          ) : null}
          <span className="ml-auto self-center text-[11px] text-zinc-600">
            kırmızı = long liq · yeşil = short liq
          </span>
        </div>
      ) : (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-zinc-400">
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
          <span className="ml-auto text-[11px] text-zinc-600">
            nokta boyutu = notional · tooltip = tahmin farkı
          </span>
        </div>
      )}

      {error ? <p className="mb-2 text-sm text-down">{error}</p> : null}

      <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-zinc-800">
        {tab === "estimated" && !map && loading ? (
          <p className="p-4 text-sm text-zinc-500">Harita hesaplanıyor...</p>
        ) : null}
        {tab === "estimated" && map ? <EstimatedHeatmap data={map} rangePct={rangePct} /> : null}
        {tab === "realized" && loading && !events.length ? (
          <p className="p-4 text-sm text-zinc-500">Yükleniyor...</p>
        ) : null}
        {tab === "realized" && (events.length > 0 || !loading) ? (
          <RealizedScatter events={events} />
        ) : null}
      </div>
    </div>
  );
}
