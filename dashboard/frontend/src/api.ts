export type Settings = {
  telegram_bot_token: string;
  telegram_chat_id: string;
  configured: boolean;
};

export type Snapshot = {
  id: number;
  tarih: string;
  saat: string;
  oi_btc: number | null;
  oi_usd: number | null;
  funding_pct: number | null;
  price: number | null;
  price_open: number | null;
  price_high: number | null;
  price_low: number | null;
  oi_linear_btc: number | null;
  oi_inverse_btc: number | null;
  cvd_spot_btc: number | null;
  cvd_perp_btc: number | null;
  premium_pct: number | null;
};

export type DurumRow = {
  id: number;
  tarih: string;
  saat: string;
  funding_durum: string | null;
  oi_durum: string | null;
  fiyat_durum: string | null;
  cvd_durum: string | null;
  genel_durum: string | null;
  trap_etiketi: string | null;
};

export type AktifIslem = {
  genel_durum?: string | null;
  giris_fiyat?: number | null;
  giris_tarih?: string | null;
  giris_saat?: string | null;
  hedef_tp?: number | null;
};

export type LiquidityLevel = {
  price: number;
  side: string;
  amount_btc: number;
  distance_pct: number;
} | null;

export type Overview = {
  snapshot: Snapshot;
  funding_status: string | null;
  liquidity: {
    above: LiquidityLevel;
    below: LiquidityLevel;
  };
  timeframes: Array<{
    tf: string;
    durum: DurumRow | null;
    aktif: AktifIslem | null;
  }>;
};

export async function fetchSettings(): Promise<Settings> {
  const res = await fetch("/api/settings");
  if (!res.ok) {
    throw new Error("Ayarlar okunamadı");
  }
  return res.json();
}

export async function saveSettings(payload: {
  telegram_bot_token: string;
  telegram_chat_id: string;
}): Promise<Settings> {
  const res = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || "Ayarlar kaydedilemedi");
  }
  return res.json();
}

export async function fetchOverview(): Promise<Overview> {
  const res = await fetch("/api/overview");
  if (!res.ok) {
    throw new Error("Özet verisi okunamadı");
  }
  return res.json();
}

export const TIMEFRAMES = ["15dk", "1sa", "2sa", "4sa", "8sa", "24sa"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type CandlesPayload = {
  tf: string;
  price: Candle[];
  oi: Candle[];
  funding: Array<{ time: number; value: number }>;
};

export async function fetchCandles(tf: Timeframe): Promise<CandlesPayload> {
  const res = await fetch(`/api/candles?tf=${encodeURIComponent(tf)}`);
  if (!res.ok) {
    throw new Error("Mum verisi okunamadı");
  }
  return res.json();
}
