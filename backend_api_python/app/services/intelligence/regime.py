"""
Regime classification (LLM-free) — Hurst exponent on closes.

The Hurst exponent describes the *current* market structure (a description, not a
prediction): H > ~0.55 = trending (momentum persists), H < ~0.45 = mean-reverting,
~0.5 = random walk. For a trend-following system this is a clean, deterministic filter:
avoid opening trend entries in clearly mean-reverting regimes, and (optionally) scale
size with regime quality.

Grounded and reproducible — no model, no network, no look-ahead (uses only past closes).
"""
from __future__ import annotations
import math
from typing import Sequence


def hurst_exponent(closes: Sequence[float], max_lag: int = 40) -> float:
    """Hurst via the variance-of-lagged-differences method. Returns ~0.5 if undefined."""
    x = [float(c) for c in closes if c is not None]
    n = len(x)
    if n < 40:
        return 0.5
    max_lag = max(8, min(int(max_lag), n // 2))
    lags = range(2, max_lag)
    tau = []
    ll = []
    for lag in lags:
        diffs = [x[i + lag] - x[i] for i in range(n - lag)]
        if len(diffs) < 2:
            continue
        mean = sum(diffs) / len(diffs)
        var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
        std = math.sqrt(var)
        if std <= 0:
            continue
        tau.append(math.log(std))
        ll.append(math.log(lag))
    if len(ll) < 3:
        return 0.5
    # slope of log(std) vs log(lag) == Hurst exponent
    m = len(ll)
    mx = sum(ll) / m
    my = sum(tau) / m
    num = sum((ll[i] - mx) * (tau[i] - my) for i in range(m))
    den = sum((ll[i] - mx) ** 2 for i in range(m))
    if den <= 0:
        return 0.5
    h = num / den
    return max(0.0, min(1.0, h))


def classify(closes: Sequence[float]) -> str:
    h = hurst_exponent(closes)
    if h >= 0.55:
        return "trending"
    if h <= 0.45:
        return "mean_reverting"
    return "random"


class RegimeFilter:
    """Entry gate / size hint based on Hurst regime.

    allow_trend_entry(): block only when *clearly* mean-reverting (conservative — we don't
    want to filter out the start of trends, which is when Hurst is still ramping).
    size_multiplier(): optional sizing (down in chop, up in clean trend).
    """

    def __init__(self, block_below: float = 0.45):
        self.block_below = float(block_below)

    def allow_trend_entry(self, closes: Sequence[float]) -> tuple[bool, str]:
        h = hurst_exponent(closes)
        if h < self.block_below:
            return False, f"regime mean-reverting (Hurst {h:.2f} < {self.block_below:.2f})"
        return True, ""

    def size_multiplier(self, closes: Sequence[float], lo: float = 0.5, hi: float = 1.3) -> float:
        h = hurst_exponent(closes)
        # map Hurst 0.45..0.65 -> lo..hi
        frac = (h - 0.45) / 0.20
        return max(lo, min(hi, lo + frac * (hi - lo)))
