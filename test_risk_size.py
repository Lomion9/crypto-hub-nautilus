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
    raised = plan_risk(equity, risk, entry, stop, 100, user_floor=5)
    assert raised is not None and raised[0] == 5 and raised[1] == equity and raised[2] == equity * 5
    held = plan_risk(equity, risk, entry, 59_520.0, 100, user_floor=5)
    assert held is not None and held[0] == 10 and held[1] == equity
    wide_floor = plan_risk(equity, risk, entry, 48_000.0, 100, user_floor=5)
    assert wide_floor is not None and wide_floor[0] == 5 and wide_floor[1] == equity
    assert wide_floor[2] == equity * 5
    over_max = plan_risk(equity, risk, entry, stop, 100, user_cap=8, user_floor=20)
    assert over_max is not None and over_max[0] == 8 and over_max[1] == equity
    over_cap = plan_risk(equity, risk, entry, 48_000.0, 10, user_floor=50)
    assert over_cap is not None and over_cap[0] == 10 and over_cap[1] == equity
    untouched = plan_risk(equity, risk, entry, 48_000.0, 100, user_floor=1)
    assert untouched == wide
    print("risk hesabı geçti")
    print("kaldıraç", lever, "marjin", round(margin, 2), "büyüklük", round(notional, 2))


if __name__ == "__main__":
    main()
