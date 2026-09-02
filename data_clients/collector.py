from datetime import datetime, timedelta, timezone

from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.identifiers import InstrumentId

from borsa import get_global_macro_data, get_btc_ohlc_15m, get_btc_price, get_binance_cvd, get_binance_premium_index
from data_types.market_snapshot import MarketSnapshotData

BTC_INSTRUMENT_ID = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")


def collect_market_snapshot() -> MarketSnapshotData | None:
    """
    main.py'deki run_snapshot_and_report()'ın veri toplama kısmıyla birebir
    aynı mantık -- OI/funding çoklu borsadan toplanır, OHLC Binance'ten REST
    ile çekilir. CVD-perp burada REST'ten çekiliyor ama bu artık SADECE
    FALLBACK -- asıl güncel değer BinanceFeedLoggerStrategy'nin native trade
    tick akışından hesaplayıp yayınladığı CVDUpdate (bkz. snapshot_logger.py'nin
    _latest_cvd_perp'i); bu REST değeri sadece CVDUpdate henüz hiç gelmediyse
    (ör. bot yeni başladı, ilk 100 tick birikmedi) kullanılır. Premium index
    (arb-riski göstergesi) tamamlayıcı bir alan -- çekilemezse (None) tüm
    snapshot iptal edilmez, sadece o alan boş kalır. Herhangi bir borsa
    başarısız olursa (main.py'deki failed_borsalar davranışıyla tutarlı
    olarak) bu tur atlanır, None döner.
    """
    total_oi, global_funding, failed_borsalar, oi_linear, oi_inverse = get_global_macro_data()
    if failed_borsalar:
        return None

    ohlc = get_btc_ohlc_15m()
    if ohlc is None or ohlc['close'] <= 0:
        price = get_btc_price()
        if price <= 0:
            return None
        ohlc = {'open': price, 'high': price, 'low': price, 'close': price}
    else:
        price = ohlc['close']

    cvd_spot = get_binance_cvd('spot', 'BTCUSDT', interval='1h')
    cvd_perp = get_binance_cvd('futures', 'BTCUSDT', interval='1h')  # fallback -- bkz. yukarıdaki not

    premium = get_binance_premium_index('BTCUSDT')
    premium_pct = premium['premium_pct'] if premium else None

    now_ns = dt_to_unix_nanos(datetime.now(timezone.utc))

    return MarketSnapshotData(
        instrument_id=BTC_INSTRUMENT_ID,
        oi_btc=total_oi,
        oi_usd=total_oi * price,
        oi_linear_btc=oi_linear,
        oi_inverse_btc=oi_inverse,
        funding_pct=global_funding,
        price=price,
        price_open=ohlc['open'],
        price_high=ohlc['high'],
        price_low=ohlc['low'],
        cvd_spot_btc=cvd_spot,
        cvd_perp_btc=cvd_perp,
        premium_pct=premium_pct,
        ts_event=now_ns,
        ts_init=now_ns,
    )