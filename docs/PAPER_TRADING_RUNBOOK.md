# Paper-Trading Runbook — Murindi Trend Portfolio v1

Deploy the vol-targeted multi-sleeve trend portfolio in **paper mode** first, with a
kill-switch, and only ramp to live capital if paper matches the backtest. This is the
professional path: prove it in shadow before risking money.

**Config (single source of truth):** `backend_api_python/strategies/portfolio_config.json`
**Strategy:** `backend_api_python/strategies/trend_following_donchian.py`
**Risk module:** `app/services/risk/vol_target.py` (sizing) + `app/services/risk/kill_switch.py` (halt)

---

## The portfolio
| Sleeve | Symbol | TF | Why | bars_per_year |
|--------|--------|----|-----|---------------|
| 1 | XAUUSD | H4 | Confirmed edge (PF 1.49, OOS 1.68, beats permutation) | 1512 |
| 2 | BTCUSD | H4 | Confirmed edge (PF 1.41), **uncorrelated to gold (−0.01)** | 2190 (24/7) |
| 3 | XAGUSD | H4 | Diversifier (marginal solo, low correlation) | 1512 |

All three run the **same** strategy file with vol-targeting on (`vol_target_annual=0.15`),
base risk **0.5%/trade**. Expected (2019–26 backtest): **Sharpe ~0.95, max DD ~7.5%, CAGR ~18%, ~34% win**.

---

## Steps

### 1. Load the strategy
- Strategy IDE → New ScriptStrategy → paste `trend_following_donchian.py`. Save it once;
  you'll instantiate it three times (one per sleeve).

### 2. Create the three sleeves
For each sleeve in `portfolio_config.json`, create a strategy instance with its `symbol`,
`timeframe`, and `params`. Set **venue = MT5 demo** (or your paper/demo account), risk
**0.5%** per trade. Confirm a backtest first to verify exposure.

### 3. Enable the kill-switch (now wired into the executor — no extra code)
The kill-switch is built into `TradingExecutor`. Just add a `kill_switch` block to each
sleeve's **trading_config** (off by default if absent). The executor then automatically:
feeds it account equity each bar, and **blocks new entries** (`open_long`/`open_short`/
`add_*`) when a limit is breached — **exits/management always proceed**, so you're never trapped.

```json
"trading_config": {
  "kill_switch": { "max_drawdown_pct": 0.15, "daily_loss_pct": 0.04,
                   "min_expectancy_R": 0.0, "expectancy_window": 40, "hard_halt": true }
}
```

All three limits are **active**:
- **max-DD 15%** and **daily-loss 4%** — from live account equity (fed each bar).
- **expectancy-decay** — the executor feeds per-trade **net return** (`PnL/equity`) on every
  close; if the rolling mean over the last `expectancy_window` (40) trades drops below
  `min_expectancy_R` (default 0.0 = net-losing), new entries are blocked. This catches a
  *decaying edge* before a drawdown stop would. (In live the unit is per-trade return, not
  strict R; keep the threshold at/near 0 — see `kill_switch.py`.)

When a sleeve blocks an entry it logs `KILL-SWITCH blocked open_long ...` with the breach reason.
Backtest DD was 7.5%, so the 15% DD limit leaves headroom; tighten once paper data confirms.

### 4. Run 30–90 days
Log every signal, requested price, actual fill, and slippage. Track rolling Sharpe / DD /
win rate per sleeve and for the book.

### 5. Promotion gate → live (tiny ramp)
Go live **only if** all hold:
- ≥ 30 paper days, live expectancy ≥ **+0.05R**,
- fills match backtest (slippage within tolerance),
- kill-switch never tripped on a normal market.

Then ramp capital **5% → 25% → 50% → 100%**, re-checking the gate at each step.

---

## What to watch (and when to stop)
- **Edge decay:** rolling 40-trade expectancy turns negative → kill-switch halts; investigate
  before resuming. Trend edges are regime-dependent — expect flat/chop stretches, but a
  *persistent* negative expectancy means the regime changed.
- **Fill quality:** if live fills are materially worse than the next-bar-open backtest
  assumption (esp. BTC/XAG spreads at H4 close), the thin edge erodes — re-test with your
  real spreads.
- **Correlation spike:** in a crisis, gold/BTC/silver can correlate; portfolio DD may exceed
  7.5%. The 15% kill-switch is the backstop.

## Honest expectations
Sharpe ~0.95, ~34% win rate, long flat stretches, ~7–15% drawdowns. A disciplined
trend-following business — **not** a money printer, and **no guarantees**. Paper-trade first.
