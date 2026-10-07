import { useEffect, useState } from "react";
import { fetchTickerPrice } from "../api";

const COINS = [
  { symbol: "BTCUSDT", label: "BTC" },
  { symbol: "ETHUSDT", label: "ETH" },
  { symbol: "SOLUSDT", label: "SOL" },
  { symbol: "BNBUSDT", label: "BNB" },
  { symbol: "XRPUSDT", label: "XRP" },
  { symbol: "BATUSDT", label: "BAT" },
  { symbol: "DOTUSDT", label: "DOT" },
  { symbol: "ADAUSDT", label: "ADA" },
] as const;

function formatTickerPrice(value: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (value >= 1000) {
    return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (value >= 1) {
    return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  }
  return value.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 6 });
}

export default function CoinSelector() {
  const [symbol, setSymbol] = useState<(typeof COINS)[number]["symbol"]>("BTCUSDT");
  const [price, setPrice] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const next = await fetchTickerPrice(symbol);
        if (!cancelled) setPrice(next.price);
      } catch {
        if (!cancelled) setPrice(null);
      }
    }
    void load();
    const id = window.setInterval(() => void load(), 12_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [symbol]);

  const active = COINS.find((coin) => coin.symbol === symbol);

  return (
    <div className="flex items-center gap-3">
      <div className="flex flex-wrap items-center gap-1">
        {COINS.map((coin) => (
          <button
            key={coin.symbol}
            type="button"
            onClick={() => setSymbol(coin.symbol)}
            className={`rounded-full border px-2 py-0.5 text-[11px] ${
              symbol === coin.symbol
                ? "border-zinc-400 text-zinc-100"
                : "border-zinc-800 text-zinc-500 hover:text-zinc-300"
            }`}
          >
            {coin.label}
          </button>
        ))}
      </div>
      <span className="whitespace-nowrap font-mono text-sm text-zinc-100">
        {active?.label} ${formatTickerPrice(price)}
      </span>
    </div>
  );
}
