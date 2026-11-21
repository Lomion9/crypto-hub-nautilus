"""Riskten pozisyon büyüklüğü ve kaldıraç. Ağ yok."""

from desktop.okx_trade import plan_risk


def main() -> None:
    equity = 1000.0
    risk = 8.0
    entry = 60_000.0
    stop = 58_200.0
    sized = plan_risk(equity, risk, entry, stop, 100)
    assert sized is not None
    lever, margin, notional = sized
    stake = equity * risk / 100
    assert abs(notional / stake - (1 / 0.03)) < 0.01
    assert isinstance(lever, int)
    assert 1 <= lever <= 100
    assert lever == int((1 / (0.03 + 0.004)) // 1)
    assert abs(margin * lever - notional) < 1e-6
    capped = plan_risk(equity, risk, entry, stop, 100, tier_cap=20)
    assert capped is not None and capped[0] == 20
    limited = plan_risk(equity, risk, entry, stop, 10)
    assert limited is not None and limited[0] == 10
    tight = plan_risk(100.0, 8.0, 60_000.0, 59_970.0, 50)
    assert tight is not None
    assert tight[0] <= 50
    assert tight[1] <= 100.0
    assert plan_risk(equity, risk, entry, entry, 100) is None
    print("risk hesabı geçti")
    print("kaldıraç", lever, "büyüklük", round(notional, 2), "marjin", round(margin, 2))


if __name__ == "__main__":
    main()
