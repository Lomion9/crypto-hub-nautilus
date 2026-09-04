from __future__ import annotations


def signal_stats(rows: list[dict]) -> dict:
    wins = 0
    losses = 0
    flats = 0
    pct_sum = 0.0
    counted = 0
    by_tf: dict[str, dict] = {}

    for row in rows:
        tf = row.get("tf") or "?"
        bucket = by_tf.setdefault(tf, {"count": 0, "wins": 0, "losses": 0, "avg_pct": None, "win_rate": None, "_pct_sum": 0.0})
        bucket["count"] += 1
        pct = row.get("kar_yuzde")
        if pct is None:
            continue
        pct = float(pct)
        pct_sum += pct
        counted += 1
        bucket["_pct_sum"] += pct
        if pct > 0:
            wins += 1
            bucket["wins"] += 1
        elif pct < 0:
            losses += 1
            bucket["losses"] += 1
        else:
            flats += 1

    for bucket in by_tf.values():
        n = bucket["count"]
        closed = bucket["wins"] + bucket["losses"]
        bucket["avg_pct"] = bucket["_pct_sum"] / n if n else None
        bucket["win_rate"] = bucket["wins"] / closed if closed else None
        del bucket["_pct_sum"]

    closed_pnl = wins + losses
    return {
        "count": len(rows),
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "avg_pct": (pct_sum / counted) if counted else None,
        "win_rate": (wins / closed_pnl) if closed_pnl else None,
        "by_tf": by_tf,
    }
