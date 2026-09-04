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

export type LiqLevel = {
  price: number;
  side: string;
  amount_btc: number;
};

export type EstimatedMap = {
  layer: string;
  window_h: number;
  current_price: number | null;
  guncel_oi: { linear?: number | null; inverse?: number | null };
  levels: LiqLevel[];
};

export async function fetchLiquidationMap(
  layer: "linear" | "inverse",
  window: 12 | 24,
): Promise<EstimatedMap> {
  const res = await fetch(`/api/liquidation-map?layer=${layer}&window=${window}`);
  if (!res.ok) {
    throw new Error("Likidasyon haritası okunamadı");
  }
  return res.json();
}

export type RealizedLiq = {
  id: number;
  tarih: string;
  saat: string;
  yon: string;
  kontrat_tipi: string;
  gercek_fiyat: number;
  notional_usd: number;
  tahmini_kume_fiyat: number | null;
  tahmini_katman: string | null;
  tahmini_pencere: number | null;
  fark_usd: number | null;
  fark_yuzde: number | null;
  time: number | null;
};

export async function fetchRealizedLiquidations(start: string, end: string): Promise<{ events: RealizedLiq[] }> {
  const params = new URLSearchParams({ start, end });
  const res = await fetch(`/api/liquidations?${params}`);
  if (!res.ok) {
    throw new Error("Gerçekleşen likidasyonlar okunamadı");
  }
  return res.json();
}

export type HistoryPoint = {
  time: number;
  oi_btc: number | null;
  funding_pct: number | null;
  cvd_spot_btc: number | null;
  cvd_perp_btc: number | null;
  price: number | null;
};

export type ClosedSignal = {
  tf: string;
  kapanis_tarih: string;
  kapanis_saat: string;
  sinyal: string;
  yon: string;
  giris_tarih: string;
  giris_saat: string;
  giris_fiyat: number;
  cikis_fiyat: number;
  kar_yuzde: number | null;
  kapanis_tipi: string | null;
};

export type HistoryPayload = {
  series: HistoryPoint[];
  signals: ClosedSignal[];
  stats: {
    count: number;
    wins: number;
    losses: number;
    flats: number;
    avg_pct: number | null;
    win_rate: number | null;
    by_tf: Record<string, { count: number; wins: number; losses: number; avg_pct: number | null; win_rate: number | null }>;
  };
};

export async function fetchHistory(start: string, end: string): Promise<HistoryPayload> {
  const params = new URLSearchParams({ start, end });
  const res = await fetch(`/api/history?${params}`);
  if (!res.ok) {
    throw new Error("Geçmiş verisi okunamadı");
  }
  return res.json();
}
