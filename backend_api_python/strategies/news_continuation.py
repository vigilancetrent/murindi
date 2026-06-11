# @param data_utc_offset_hours float 0 Hours to subtract from bar time to get UTC (set to your broker/data offset; ignored if bars are tz-aware)
# @param spike_block_min int 15 Skip this many minutes after the event (the violent spike) before entering
# @param window_min int 180 Length of the post-spike continuation window (minutes)
# @param ref_window_min int 60 Pre-event range lookback (minutes) used as the breakout reference
# @param atr_period int 14 ATR period for the stop
# @param atr_mult float 2.0 Hard stop = atr_mult x ATR
# @param exit_n int 10 Trailing exit: leave when close crosses the opposite N-bar channel
# @param risk_pct float 0.1 Per-trade order size (fraction of equity) used as runtime order intent
# @param extra_events str "" Comma-separated ISO-UTC datetimes for irregular events (FOMC/ECB), e.g. "2026-06-17T18:00,2026-07-29T18:00"
# @strategy entryPct 0.1
# @strategy tradeDirection both
# @strategy trailingEnabled false
"""
News Continuation (post-event breakout) — OPTIONAL event-driven strategy.

Trades the *continuation* AFTER a high-impact event, not the spike. It:
  1. waits `spike_block_min` after the event (lets spreads normalize, lets the violent
     first move whipsaw itself out),
  2. inside a `window_min` continuation window, enters in the direction that BROKE the
     pre-event range (close above the pre-event high -> long; below the low -> short),
  3. manages with an ATR stop + opposite-channel trailing exit, and flattens when the
     continuation window ends.

It NEVER stands in the print and NEVER predicts the number — it only rides a move that
has already established itself. Auto-knows the deterministic US events (NFP = 1st Friday
12:30 UTC, CPI ~13th 12:30 UTC); pass irregular events (FOMC/ECB) via `extra_events`.

INTENDED USE: intraday (M5/M15) on USD-driven instruments (XAUUSD, indices, USD pairs).
On H4+ the windows are sub-bar and this won't trigger meaningfully.

⚠️ HONEST RESULT: a rough M15 gold backtest (NFP+CPI, 245 trades) came out NEGATIVE —
PF 0.85, expectancy -0.10R, fails the permutation test — and that is BEFORE modeling the
news-time spread blowout that would make it worse. So entering specifically on the event,
even the "safe" post-spike continuation, shows NO edge. This file is provided because it
was requested as an option; it is NOT a confirmed edge and should not be traded standalone.

THE DEFENSIBLE WAY to trade post-news continuation is the core trend strategy run with the
news-guard in "continuation" mode (block the spike, then ride the established move with the
proven Donchian edge). Use that instead of this.
"""


def on_init(ctx):
    ctx.log("news_continuation initialized")


def _to_utc(ts, offset_hours):
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        return t.tz_convert("UTC").tz_localize(None)
    return t - pd.Timedelta(hours=offset_hours)


def _first_friday(year, month):
    d = pd.Timestamp(year=year, month=month, day=1)
    while d.weekday() != 4:  # Friday
        d += pd.Timedelta(days=1)
    return d + pd.Timedelta(hours=12, minutes=30)


def _events_around(now_utc, extra_events):
    """High-impact event datetimes (UTC) for this and previous month + user extras."""
    evs = []
    for off in (0, -1):
        m = now_utc.month - 1 + off
        y = now_utc.year + (m // 12)
        mo = m % 12 + 1
        evs.append(_first_friday(y, mo))                                  # NFP
        evs.append(pd.Timestamp(year=y, month=mo, day=13, hour=12, minute=30))  # CPI
    for s in str(extra_events or "").split(","):
        s = s.strip()
        if s:
            try:
                evs.append(pd.Timestamp(s).tz_localize(None) if pd.Timestamp(s).tzinfo is None
                           else pd.Timestamp(s).tz_convert("UTC").tz_localize(None))
            except Exception:
                pass
    # keep only events within a few days of now (intraday windows)
    return [e for e in evs if abs((e - now_utc).total_seconds()) < 5 * 86400]


def _atr(highs, lows, closes, period):
    n = len(closes)
    trs = []
    for i in range(n - period, n):
        pc = closes[i - 1]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return (sum(trs) / len(trs)) if trs else 0.0


def on_bar(ctx, bar):
    off = float(ctx.param("data_utc_offset_hours", 0))
    spike = int(ctx.param("spike_block_min", 15))
    window = int(ctx.param("window_min", 180))
    ref_win = int(ctx.param("ref_window_min", 60))
    atr_n = int(ctx.param("atr_period", 14))
    atr_mult = float(ctx.param("atr_mult", 2.0))
    exit_n = int(ctx.param("exit_n", 10))
    risk_pct = float(ctx.param("risk_pct", 0.1))
    extra = ctx.param("extra_events", "")

    need = max(atr_n, exit_n, 30) + 5
    bars = ctx.bars(need)
    if len(bars) < atr_n + 3:
        return
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    times = [_to_utc(b.timestamp, off) for b in bars]
    now = times[-1]
    price = bar.close
    atr = _atr(highs, lows, closes, atr_n)

    events = _events_around(now, extra)

    # find an event whose continuation window contains 'now', plus its pre-event range
    active = None
    for E in events:
        win_start = E + pd.Timedelta(minutes=spike)
        win_end = E + pd.Timedelta(minutes=spike + window)
        if win_start <= now <= win_end:
            ref_hi = ref_lo = None
            for i, t in enumerate(times):
                if E - pd.Timedelta(minutes=ref_win) <= t <= E:
                    ref_hi = highs[i] if ref_hi is None else max(ref_hi, highs[i])
                    ref_lo = lows[i] if ref_lo is None else min(ref_lo, lows[i])
            active = (E, ref_hi, ref_lo)
            break

    # --- manage open position: ATR stop, trailing channel, or window-end flatten ---
    if ctx.position:
        exit_low = min(lows[-exit_n - 1:-1]) if len(lows) > exit_n else lows[0]
        exit_high = max(highs[-exit_n - 1:-1]) if len(highs) > exit_n else highs[0]
        entry = float(ctx.position["entry_price"] or price)
        if active is None:
            ctx.close_position()           # continuation window ended -> flat
            return
        if ctx.position > 0:
            if bar.low <= entry - atr_mult * atr or price < exit_low:
                ctx.close_position()
            return
        if ctx.position < 0:
            if bar.high >= entry + atr_mult * atr or price > exit_high:
                ctx.close_position()
            return

    # --- flat: enter the post-event breakout in the established direction ---
    if active is not None and atr > 0:
        _E, ref_hi, ref_lo = active
        if ref_hi is None or ref_lo is None:
            return
        if price > ref_hi:
            ctx.buy(price=price, amount=risk_pct, reason="news_continuation_long")
        elif price < ref_lo:
            ctx.sell(price=price, amount=risk_pct, reason="news_continuation_short")
