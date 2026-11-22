"""Kasanın tümü teminat. Stopta kayıp risk yüzdesini geçmez. Ağ yok."""

from desktop.okx_trade import plan_risk


def main() -> None:
    equity = 1000.0
    risk = 8.0
    entry = 60_000.0
    stop = 58_200.0
    sized = plan_risk(equity, risk, entry, stop, 100)
    assert sized is not None
    lever, margin, notional = sized
    assert lever == 2
    assert margin == equity
    assert notional == equity * lever
    assert notional * 0.03 <= equity * risk / 100 + 1e-6
    tight = plan_risk(equity, risk, entry, 59_940.0, 100)
    assert tight is not None and tight[0] == 80 and tight[1] == equity
    limited = plan_risk(equity, risk, entry, 59_940.0, 100, user_cap=8)
    assert limited is not None and limited[0] == 8 and limited[1] == equity
    assert limited[2] * (60.0 / entry) <= equity * risk / 100 + 1e-6
    capped = plan_risk(equity, risk, entry, 59_940.0, 10)
    assert capped is not None and capped[0] == 10
    wide = plan_risk(equity, risk, entry, 48_000.0, 100)
    assert wide is not None and wide[0] == 1 and wide[1] < equity
    assert abs(wide[2] * 0.20 - equity * risk / 100) < 1e-6
    assert plan_risk(equity, risk, entry, entry, 100) is None
    print("risk hesabı geçti")
    print("kaldıraç", lever, "marjin", round(margin, 2), "büyüklük", round(notional, 2))


if __name__ == "__main__":
    main()
