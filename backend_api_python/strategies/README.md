# Ported strategies (Murindi `ScriptStrategy`)

Research-validated strategies ported into Murindi's native `ScriptStrategy` contract.
Each file defines `on_init(ctx)` + `on_bar(ctx, bar)` and is **broker- and data-agnostic**:
Murindi feeds the bars and routes orders to whatever venue is configured (MT5 / CCXT /
IBKR / Alpaca), and the *same file* backtests and trades live. Nothing about symbol,
timeframe, venue, or credentials is hardcoded — all tunables are `# @param`.

| File | Strategy | Best markets | Status |
|------|----------|--------------|--------|
| `trend_following_donchian.py` | Donchian breakout + ATR stop + channel trailing | XAU/XAG/BTC H4 | ✅ validated (PF 1.49, OOS 1.68, permutation p=0.013) |
| _(soon)_ `smc_be1run.py` | M15 structure + order-block, breakeven@1R | XAU/indices M15 | ⏳ porting |
| _(soon)_ `gold_momentum_pro.py` | gold intraday momentum | XAU intraday | ⏳ queued (gated) |

## Load into Murindi
- **UI:** Strategy IDE → New ScriptStrategy → paste the file → pick symbol/timeframe → backtest → paper → live.
- **API:** create a strategy with the file's code as the script body.

## Re-validate before trusting
```
python scripts/validate_strategy.py \
  --strategy strategies/<file>.py \
  --data "<path>/XAUUSD_M1.pkl" --tf 4H --point 0.01 --slip 2
```
Gates to pass: OOS PF ≥ 1.3, beats permutation, max DD ≤ 20%, profitable across most years.

> These are modest, regime-dependent edges — not guarantees. Paper-trade before capital.
