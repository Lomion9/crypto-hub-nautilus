"""Trend Long yalnız yeni kombinasyonu değiştirir. Eski genel_durum çıktısı sabittir."""

from desktop.readout import TREND_LONG, TREND_SHORT, funding_label, genel_durum, trend_long_bandi, trend_short_bandi


def _eski(fund_status, oi_status, price_status, cvd_spot, cvd_perp):
    """Değişiklikten önceki genel_durum. Yeni argüman yok."""
    fund_positive = fund_status in {"Pozitif", "Aşırı Pozitif"}
    fund_negative = fund_status in {"Negatif", "Aşırı Negatif"}
    long_trap = fund_positive and oi_status == "Artıyor" and price_status == "Düşüyor"
    long_squeeze = fund_positive and oi_status == "Düşüyor" and price_status == "Düşüyor"
    short_trap = fund_negative and oi_status == "Artıyor" and price_status == "Artıyor"
    short_squeeze = fund_negative and oi_status == "Düşüyor" and price_status == "Artıyor"
    if short_squeeze:
        return ("Sağlıklı Long" if cvd_spot > 0 else "Short Squeeze"), None
    if long_squeeze:
        if cvd_spot > 0 and cvd_spot > abs(cvd_perp):
            return "İşlem Açma", None
        return ("Sağlıklı Short" if cvd_spot < 0 else "Long Squeeze"), None
    if long_trap:
        etiket = "Long Trap (Olası Absorption)" if cvd_spot > 0 and cvd_spot > abs(cvd_perp) else "Long Trap"
        return "İşlem Açma", etiket
    if short_trap:
        etiket = "Short Trap (Olası Dağıtım)" if cvd_spot < 0 and abs(cvd_spot) > abs(cvd_perp) else "Short Trap"
        return "İşlem Açma", etiket
    if price_status == "Nötr" and oi_status == "Artıyor":
        if cvd_spot > 0 and cvd_spot >= abs(cvd_perp):
            return "Akümülasyon", None
        if cvd_spot < 0 and abs(cvd_spot) >= abs(cvd_perp):
            return "Dağıtım", None
    return "İşlem Açma", None


def _yeni_short(price, oi, spot, funding) -> bool:
    if not TREND_SHORT["trend_short_aktif"]:
        return False
    if price != "Düşüyor" or oi != "Artıyor" or not spot < 0:
        return False
    if funding is None:
        return False
    if funding_label(funding) == "Nötr":
        return True
    return TREND_SHORT["fund_alt"] <= funding < 0


def _yeni(price, oi, spot, funding) -> bool:
    if not TREND_LONG["trend_long_aktif"]:
        return False
    if price != "Artıyor" or oi != "Artıyor" or not spot > 0:
        return False
    if funding is None:
        return False
    if funding_label(funding) == "Nötr":
        return True
    return 0 < funding <= TREND_LONG["fund_ust"]


def main() -> None:
    prices = ("Artıyor", "Düşüyor", "Nötr")
    ois = ("Artıyor", "Düşüyor", "Nötr")
    funds = ("Negatif", "Aşırı Negatif", "Nötr", "Pozitif", "Aşırı Pozitif", "Veri Yok")
    spots = (-2.0, 0.0, 2.0)
    perps = (-3.0, 0.0, 1.0, 5.0)
    ham = (
        None, -0.00004, 0.0, 0.00004, -0.0001, -0.003, -0.005, -0.007, -0.01,
        -0.01004, -0.02, 0.003, 0.005, 0.007, 0.01, 0.01004, 0.02,
    )
    checked = 0
    for price in prices:
        for oi in ois:
            for fund in funds:
                for spot in spots:
                    for perp in perps:
                        old = _eski(fund, oi, price, spot, perp)
                        assert genel_durum(fund, oi, price, spot, perp) == old
                        for funding in ham:
                            got = genel_durum(fund, oi, price, spot, perp, funding)
                            if old == ("İşlem Açma", None) and _yeni(price, oi, spot, funding):
                                expected = ("Trend Long", None)
                            elif old == ("İşlem Açma", None) and _yeni_short(price, oi, spot, funding):
                                expected = ("Trend Short", None)
                            else:
                                expected = old
                            assert got == expected, (price, oi, fund, spot, perp, funding, got, expected)
                            checked += 1
    assert trend_long_bandi(0.0) == "düşük"
    assert trend_long_bandi(-0.00004) == "düşük"
    assert trend_long_bandi(0.005) == "düşük"
    assert trend_long_bandi(0.0051) == "normal"
    assert trend_long_bandi(0.01) == "normal"
    assert trend_long_bandi(0.02) is None
    assert trend_long_bandi(-0.0001) is None
    assert trend_short_bandi(0.0) == "düşük"
    assert trend_short_bandi(0.00004) == "düşük"
    assert trend_short_bandi(-0.005) == "düşük"
    assert trend_short_bandi(-0.0051) == "normal"
    assert trend_short_bandi(-0.01) == "normal"
    assert trend_short_bandi(-0.02) is None
    assert trend_short_bandi(0.0001) is None
    was_short = TREND_SHORT["trend_short_aktif"]
    TREND_SHORT["trend_short_aktif"] = False
    try:
        assert genel_durum("Negatif", "Artıyor", "Düşüyor", -2.0, 1.0, -0.003) == ("İşlem Açma", None)
    finally:
        TREND_SHORT["trend_short_aktif"] = was_short
    was = TREND_LONG["trend_long_aktif"]
    TREND_LONG["trend_long_aktif"] = False
    try:
        assert genel_durum("Pozitif", "Artıyor", "Artıyor", 2.0, 1.0, 0.003) == ("İşlem Açma", None)
    finally:
        TREND_LONG["trend_long_aktif"] = was
    print("trend long geçti")
    print("kombinasyon", checked)


if __name__ == "__main__":
    main()
