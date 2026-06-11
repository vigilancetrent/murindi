"""
Volatility-targeting position sizer (risk overlay — sizing, not direction).

WHY THIS, AND ONLY THIS, FROM THE ML SIDE
------------------------------------------
Across the research corpus, every attempt to *predict direction* with ML/RL/DL
failed (≈50% accuracy, fails permutation). The single signal that survived honest
out-of-sample testing was **volatility magnitude**: the AI-architect study showed a
vol model beating a causal EWMA baseline by ~+60% R² on 5/5 walk-forward folds, and
`trans`'s winning trend portfolio scaled exposure to a constant volatility budget.

So we monetize volatility the only way it pays: **size**, not direction. Risk less
when realized volatility is high, more when it is low, to hold a roughly constant
risk budget. Empirically this smooths the equity curve and cuts drawdown without
needing to predict price at all. It is a pure multiplier you apply on top of any
strategy's base size — broker- and instrument-agnostic.

USAGE
-----
    from app.services.risk.vol_target import vol_target_multiplier, realized_vol

    # `closes`: recent close prices (list/np array), oldest..newest
    rv = realized_vol(closes, window=100, periods_per_year=ppy)   # annualized
    mult = vol_target_multiplier(rv, target_annual=0.15)          # e.g. 0.25..2.0
    size = base_size * mult

In a Murindi ScriptStrategy:
    closes = [b.close for b in ctx.bars(120)]
    mult = vol_target_multiplier(realized_vol(closes, 100, ppy), 0.15)
    ctx.buy(price=bar.close, amount=base_risk * mult)
"""
from __future__ import annotations
from typing import Sequence
import math


def realized_vol(closes: Sequence[float], window: int = 100, periods_per_year: float = 1512.0) -> float:
    """Annualized realized volatility from the last `window` log returns.

    periods_per_year defaults to ~H4 (6 bars/day * 252). For other bars pass:
        D1≈252, H1≈6048, M15≈24192, crypto 24/7 multiply by 365/252.
    Returns 0.0 if there is not enough data.
    """
    c = [float(x) for x in closes if x and x > 0]
    if len(c) < 3:
        return 0.0
    w = min(int(window), len(c) - 1)
    rets = [math.log(c[i] / c[i - 1]) for i in range(len(c) - w, len(c))]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def vol_target_multiplier(current_vol: float, target_annual: float = 0.15,
                          lo: float = 0.25, hi: float = 2.0) -> float:
    """Size multiplier to hold a constant volatility budget.

    multiplier = clip(target_annual / current_vol, lo, hi)

    - current_vol high  -> multiplier < 1 (risk less)
    - current_vol low   -> multiplier > 1 (risk more)
    Clamped to [lo, hi] so a quiet/blown vol estimate can't size you to ruin or zero.
    Returns 1.0 (neutral) if vol is unavailable.
    """
    if not current_vol or current_vol <= 0:
        return 1.0
    return max(lo, min(hi, float(target_annual) / float(current_vol)))


# periods-per-year helpers for the common Murindi timeframes (252 trading days;
# pass crypto=True for 24/7 instruments to use 365 days)
def periods_per_year(timeframe: str, crypto: bool = False) -> float:
    days = 365.0 if crypto else 252.0
    per_day = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48,
               "1h": 24, "4h": 6, "1d": 1}.get(timeframe.lower().replace("min", "m"), 6)
    return per_day * days
