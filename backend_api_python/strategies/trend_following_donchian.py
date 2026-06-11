# @param donchian_entry int 20 Breakout lookback — enter when close breaks the prior N-bar high/low
# @param donchian_exit int 10 Trailing-exit lookback — exit when close crosses the opposite M-bar channel
# @param atr_period int 14 ATR period for the protective hard stop
# @param atr_mult float 2.0 Hard stop distance = atr_mult x ATR from entry
# @param allow_short bool true Trade short breakouts as well as long
# @param risk_pct float 0.1 Per-trade order size (fraction of equity) used as runtime order intent
# @param vol_target_annual float 0.15 Annualized vol target for sizing (0 = flat sizing, off)
# @param bars_per_year float 1512 Bars/year for vol annualization (H4=1512, D1=252, 24/7 H4=2190)
# @param regime_filter bool false Skip entries when the market is clearly mean-reverting (Hurst gate)
# @param regime_min_hurst float 0.45 Block entries when Hurst < this value
# @strategy entryPct 0.1
# @strategy tradeDirection both
# @strategy trailingEnabled false
"""
Trend-Following (Donchian breakout + ATR stop + opposite-channel trailing exit)
==============================================================================

Ported into Murindi's ScriptStrategy contract so it runs unchanged across the
backtester and every live venue (MT5 / CCXT / IBKR / Alpaca). It is symbol- and
timeframe-agnostic: Murindi feeds the bars, this script only reads `ctx.bars()`
and emits intent via `ctx.buy/sell/close_position`.

WHY THIS STRATEGY
-----------------
It is the most robustly validated edge found across the research corpus —
confirmed independently in multiple studies, with out-of-sample performance that
*improves* rather than decays, and a long history through several regimes:

  * Gold H4 momentum (intavidoetrading/GOLD_MOMENTUM.md):
      Profit factor 1.39 (out-of-sample 1.48), expectancy +0.22R,
      max drawdown 12.8%, 7 of 8 years profitable. The (N, stop) heatmap is a
      smooth plateau (N>=20, stop 1.5-3.0xATR) — a real edge, not a curve-fit spike.
  * Multi-asset trend portfolio (BTC/XAU/XAG/NAS): PF 1.54, OOS 1.60, low
      cross-asset correlation.
  * Daily gold trend-following (machine learning pro/trans/FINDINGS.md):
      +379% over ~28 years, profitable in 5/5 multi-decade blocks (incl. gold
      bear markets), realistic costs included.

The edge is momentum-continuation: gold (and other high-momentum instruments)
trend, so we *ride* the move with a trailing channel exit. The right tail (a few
large trend captures) pays for the many small losers — hence the low (~34%) win
rate. **Fixed take-profit targets destroy the edge; the trailing exit is essential.**

RULES (validated config = defaults)
-----------------------------------
  Entry : close breaks the prior `donchian_entry`-bar Donchian high (long) or low (short)
  Stop  : hard stop at `atr_mult` x ATR(`atr_period`) from entry
  Exit  : close beyond the opposite `donchian_exit`-bar channel (trailing — let winners run)
  Side  : long & short, one position at a time

All channels are computed on bars *before* the current bar (no look-ahead); the
engine fills on the next bar open in strict mode, matching live execution.

HONEST CAVEATS
--------------
  * Trend-follower -> ~34% win rate and long flat/chop stretches (e.g. 2021).
    Drawdowns up to ~13% on the 2.0xATR default; keep risk modest.
  * Validated primarily on H4/D1 gold & momentum instruments. Apply on those
    timeframes; re-validate before using elsewhere.
  * Backtest != live. Re-test on your real spread and paper-trade before capital.

Sizing note: per Murindi's contract, saved-strategy backtests size from the
normalized `entryPct`; the `risk_pct` amount passed to ctx.buy/ctx.sell is live
order intent. The standalone validation harness reproduces the exact 1%-risk-at-
ATR-stop sizing used in the original research.
"""


def on_init(ctx):
    ctx.log("trend_following_donchian initialized")


def _vol_mult(closes, window, ppy, target, lo=0.25, hi=2.0):
    """Volatility-targeting size multiplier: clip(target / realized_annual_vol, lo, hi).
    Risk less when vol is high, more when low -> constant risk budget. 1.0 if disabled."""
    if not target or target <= 0:
        return 1.0
    c = np.asarray([x for x in closes if x and x > 0], dtype=float)
    if c.size < 3:
        return 1.0
    w = min(int(window), c.size - 1)
    rets = np.diff(np.log(c[-w - 1:]))
    if rets.size < 2:
        return 1.0
    rv = float(rets.std(ddof=1) * np.sqrt(ppy))
    if rv <= 0:
        return 1.0
    return float(max(lo, min(hi, target / rv)))


def _hurst(closes, max_lag=40):
    """Hurst exponent (variance of lagged differences). >0.55 trending, <0.45 mean-reverting."""
    x = np.asarray([c for c in closes if c is not None], dtype=float)
    n = x.size
    if n < 40:
        return 0.5
    max_lag = max(8, min(int(max_lag), n // 2))
    ll, ts = [], []
    for lag in range(2, max_lag):
        d = x[lag:] - x[:-lag]
        s = d.std()
        if s > 0:
            ll.append(np.log(lag)); ts.append(np.log(s))
    if len(ll) < 3:
        return 0.5
    h = float(np.polyfit(np.asarray(ll), np.asarray(ts), 1)[0])
    return max(0.0, min(1.0, h))


def _atr(highs, lows, closes, period):
    """Average true range over the last `period` bars (includes the current bar)."""
    n = len(closes)
    trs = []
    for i in range(n - period, n):
        prev_close = closes[i - 1]
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        )
        trs.append(tr)
    return (sum(trs) / len(trs)) if trs else 0.0


def on_bar(ctx, bar):
    entry_n = int(ctx.param("donchian_entry", 20))
    exit_n = int(ctx.param("donchian_exit", 10))
    atr_n = int(ctx.param("atr_period", 14))
    atr_mult = float(ctx.param("atr_mult", 2.0))
    allow_short = bool(ctx.param("allow_short", True))
    risk_pct = float(ctx.param("risk_pct", 0.1))
    vol_target = float(ctx.param("vol_target_annual", 0.15))
    ppy = float(ctx.param("bars_per_year", 1512))
    regime_filter = bool(ctx.param("regime_filter", False))
    regime_min_hurst = float(ctx.param("regime_min_hurst", 0.45))

    need = max(entry_n, exit_n, atr_n) + 2
    fetch = max(need, 105) if (vol_target > 0 or regime_filter) else need
    bars = ctx.bars(fetch)
    if len(bars) < need:
        return

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]

    # Prior channels exclude the current (last) bar -> no look-ahead.
    don_high = max(highs[-entry_n - 1:-1])
    don_low = min(lows[-entry_n - 1:-1])
    exit_low = min(lows[-exit_n - 1:-1])   # long trailing-exit floor
    exit_high = max(highs[-exit_n - 1:-1])  # short trailing-exit ceiling
    atr = _atr(highs, lows, closes, atr_n)
    price = bar.close

    # --- Flat: look for a breakout entry (vol-targeted sizing) ---
    if not ctx.position:
        # Optional regime gate: skip entries in clearly mean-reverting markets.
        if regime_filter and _hurst(closes) < regime_min_hurst:
            return
        amt = risk_pct * _vol_mult(closes, 100, ppy, vol_target)
        if price > don_high:
            ctx.buy(price=price, amount=amt, reason="donchian_breakout_long")
        elif allow_short and price < don_low:
            ctx.sell(price=price, amount=amt, reason="donchian_breakout_short")
        return

    # --- Long: ATR hard stop OR trailing channel exit ---
    if ctx.position > 0:
        entry = float(ctx.position["entry_price"] or price)
        stop = entry - atr_mult * atr
        if bar.low <= stop:
            ctx.close_position()
        elif price < exit_low:
            ctx.close_position()
        return

    # --- Short: ATR hard stop OR trailing channel exit ---
    if ctx.position < 0:
        entry = float(ctx.position["entry_price"] or price)
        stop = entry + atr_mult * atr
        if bar.high >= stop:
            ctx.close_position()
        elif price > exit_high:
            ctx.close_position()
        return
