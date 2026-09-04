import { useEffect, useState } from "react";
import { fetchOverview, type LiquidityLevel, type Overview } from "../api";
import {
  formatBtc,
  formatFundingPct,
  formatPrice,
  formatSigned,
  formatUsdCompact,
  signedClass,
} from "../format";

const POLL_MS = 15_000;

function tone(genel: string | null | undefined, trap: string | null | undefined) {
  if (trap) {
    return {
      border: "border-amber-500/40",
      badge: "bg-amber-500/15 text-amber-300",
      label: trap,
    };
  }
  const longSet = new Set(["Sağlıklı Long", "Short Squeeze", "Akümülasyon"]);
  const shortSet = new Set(["Sağlıklı Short", "Long Squeeze", "Dağıtım"]);
  if (genel && longSet.has(genel)) {
    return { border: "border-up/40", badge: "bg-up/15 text-up", label: genel };
  }
  if (genel && shortSet.has(genel)) {
    return { border: "border-down/40", badge: "bg-down/15 text-down", label: genel };
  }
  return {
    border: "border-zinc-800",
    badge: "bg-zinc-800 text-zinc-400",
    label: genel || "Veri yok",
  };
}

function LiquidityCell({
  title,
  level,
}: {
  title: string;
  level: LiquidityLevel;
}) {
  if (!level) {
    return (
      <div>
        <p className="text-[11px] tracking-wide text-zinc-500 uppercase">{title}</p>
        <p className="mt-1 text-sm text-zinc-500">Küme yok</p>
      </div>
    );
  }
  const dir = level.distance_pct >= 0 ? "+" : "";
  return (
    <div>
      <p className="text-[11px] tracking-wide text-zinc-500 uppercase">{title}</p>
      <p className="mt-1 font-medium text-zinc-100">${formatPrice(level.price)}</p>
      <p className="text-xs text-zinc-500">
        {level.side} · {formatBtc(level.amount_btc)} BTC · {dir}
        {level.distance_pct.toFixed(2)}%
      </p>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");

  async function load() {
    try {
      const next = await fetchOverview();
      setData(next);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Yükleme hatası");
    }
  }

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
  }, []);

  if (error && !data) {
    return <p className="px-6 py-8 text-sm text-down">{error}</p>;
  }

  if (!data) {
    return <p className="px-6 py-8 text-sm text-zinc-500">Piyasa özeti yükleniyor...</p>;
  }

  const s = data.snapshot;
  const fundTone =
    data.funding_status?.includes("Pozitif") || (s.funding_pct ?? 0) > 0
      ? "text-up"
      : data.funding_status?.includes("Negatif") || (s.funding_pct ?? 0) < 0
        ? "text-down"
        : "text-zinc-200";

  return (
    <div className="px-6 py-6">
      <div className="mb-2 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Piyasa özeti</h1>
          <p className="text-xs text-zinc-500">
            {s.tarih} {s.saat} · 15dk bar
          </p>
        </div>
        {error ? <p className="text-xs text-amber-400">{error}</p> : null}
      </div>

      <section className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
          <p className="text-[11px] tracking-wide text-zinc-500 uppercase">Fiyat</p>
          <p className="mt-1 text-xl font-semibold tracking-tight text-zinc-50">
            ${formatPrice(s.price)}
          </p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
          <p className="text-[11px] tracking-wide text-zinc-500 uppercase">Open interest</p>
          <p className="mt-1 text-xl font-semibold text-zinc-50">{formatUsdCompact(s.oi_usd)}</p>
          <p className="text-xs text-zinc-500">{formatBtc(s.oi_btc, 0)} BTC</p>
          <p className="mt-1 text-[11px] text-zinc-400">
            L {formatBtc(s.oi_linear_btc, 0)} · I {formatBtc(s.oi_inverse_btc, 0)}
          </p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
          <p className="text-[11px] tracking-wide text-zinc-500 uppercase">Funding</p>
          <p className={`mt-1 text-xl font-semibold ${fundTone}`}>{formatFundingPct(s.funding_pct)}</p>
          <p className="text-xs text-zinc-500">{data.funding_status || "—"}</p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3">
          <p className="text-[11px] tracking-wide text-zinc-500 uppercase">CVD gün içi</p>
          <p className={`mt-1 text-sm font-medium ${signedClass(s.cvd_spot_btc)}`}>
            Spot {formatSigned(s.cvd_spot_btc)}
          </p>
          <p className={`text-sm font-medium ${signedClass(s.cvd_perp_btc)}`}>
            Perp {formatSigned(s.cvd_perp_btc)}
          </p>
        </div>

        <div className="col-span-2 grid grid-cols-2 gap-3 rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3 lg:col-span-1 lg:grid-cols-1">
          <LiquidityCell title="Üst likidite" level={data.liquidity.above} />
          <LiquidityCell title="Alt likidite" level={data.liquidity.below} />
        </div>
      </section>

      <h2 className="mb-3 text-sm font-medium text-zinc-400">Timeframe durumları</h2>
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {data.timeframes.map(({ tf, durum, aktif }) => {
          const t = tone(durum?.genel_durum, durum?.trap_etiketi);
          return (
            <article
              key={tf}
              className={`rounded-xl border bg-zinc-950/60 px-4 py-3 ${t.border}`}
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-zinc-100">{tf}</span>
                <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${t.badge}`}>
                  {t.label}
                </span>
              </div>
              <dl className="space-y-1 text-[11px] text-zinc-500">
                <div className="flex justify-between gap-2">
                  <dt>OI</dt>
                  <dd className="text-zinc-300">{durum?.oi_durum || "—"}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>Fiyat</dt>
                  <dd className="text-zinc-300">{durum?.fiyat_durum || "—"}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>CVD</dt>
                  <dd className="truncate text-right text-zinc-300">{durum?.cvd_durum || "—"}</dd>
                </div>
              </dl>
              {aktif?.giris_fiyat ? (
                <p className="mt-2 text-[11px] text-zinc-500">
                  Giriş ${formatPrice(aktif.giris_fiyat)}
                  {aktif.hedef_tp ? ` · TP $${formatPrice(aktif.hedef_tp)}` : ""}
                </p>
              ) : null}
            </article>
          );
        })}
      </section>
    </div>
  );
}