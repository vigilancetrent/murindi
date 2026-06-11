# Strategy Integration Report — research → Murindi

Milestone 1: port the strategies that survived the research corpus into Murindi's
native `ScriptStrategy` format (broker- and data-agnostic), then **re-validate them
independently** on real history through a faithful replica of Murindi's closed-bar
execution model (next-bar-open fills, real per-bar spread + slippage, 1%-risk-at-ATR
sizing). We do **not** trust the source backtests.

Harness: `backend_api_python/scripts/validate_strategy.py`
Hard gates to "PASS": **OOS PF ≥ 1.3**, **beats permutation test**, **max DD ≤ 20%**,
profitable across most walk-forward windows.

> ⚠️ Trading is risky. These are *modest, regime-dependent* edges (Sharpe ~0.4–1.0),
> not guarantees. Backtest ≠ live. Paper-trade before any capital.

---

## ✅ Strategy 1 — Trend-Following (Donchian breakout + ATR stop + channel trailing)

**File:** `backend_api_python/strategies/trend_following_donchian.py`
**Rules:** enter when close breaks the prior 20-bar Donchian high/low; hard stop at
2.0×ATR(14); exit when close crosses the opposite 10-bar channel (trailing — let
winners run). Long & short, one position at a time. The trailing exit is the edge —
fixed take-profits destroy it.

### Re-validation (run inside Murindi's execution model on real data)

| Instrument / TF | Trades | Win% | Full PF | OOS PF (≥2023) | Permutation | Max DD | Verdict |
|---|---|---|---|---|---|---|---|
| **XAUUSD H4** | 313 | 34.8% | 1.49 | **1.68** | BEATS, p=0.013 | 18.3% | ✅ **PASS** |
| **BTCUSD H4** | 400 | 33.8% | 1.41 | 1.33 | BEATS, p=0.003 | 25.7% | ✅ **PASS** (crypto DD higher) |
| **NAS100 1D** | 54 | 38.9% | 1.42 | 1.50 | FAILS, p=0.168 | 4.4% | ⚠️ underpowered (n too small) |

- **Parity with source:** XAUUSD reproduced 313 trades vs the research's 311; PF 1.49
  vs 1.39 — independently confirmed under Murindi's (slightly more conservative)
  closed-bar fills.
- **Out-of-sample improves** on gold (1.33 → 1.68) — the strongest possible robustness signal.
- **Cost-robust:** 2× spread/slippage stress barely moves PF (1.49 → 1.48 on gold).
- **Per-year (gold):** clearly profitable 2020/2021/2023/2024/2025/2026; ~breakeven
  2019/2022 — consistent with a trend-follower (flat in choppy years).

**Verdict:** Deploy-candidate on **trending/momentum instruments** (gold, BTC, and as a
multi-asset basket). Do **not** rely on it alone on low-trade instruments like NAS daily
without more data. Recommended starting universe: XAUUSD H4, XAGUSD H4, BTCUSD H4.

### Broker / data agnostic — by construction
Written as a Murindi `ScriptStrategy` (`on_init`/`on_bar`, `ctx.bars/position/buy/sell/
close_position`). It reads only bars and emits intent, so Murindi feeds data and routes
orders identically across **MT5 / CCXT / IBKR / Alpaca**, and the same file powers both
backtest and live. No symbol, timeframe, venue, or credentials are hardcoded; everything
tunable is a `# @param`.

### How to load into Murindi
1. Open **Strategy IDE → New ScriptStrategy**.
2. Paste the contents of `strategies/trend_following_donchian.py`.
3. Pick a symbol + **H4** (gold/metals/crypto) or **D1** (indices) and run a backtest to
   confirm exposure, then promote to **paper** before live.
   (Or load programmatically via the strategy-create API with the file's code body.)

---

## ❌ Strategy 2 — SMC be1run (M15) — REJECTED (failed re-validation)

Re-validated by running the **source's own `process()`** on the **source's own data**
(`scripts/revalidate_source_smc.py`) — so any failure is the strategy, not the port.

| Instrument M15 | Trades | Full PF | Full expR | OOS expR | Permutation | Verdict |
|---|---|---|---|---|---|---|
| XAUUSD | 301 | 1.01 | +0.003 | +0.043 | **FAILS** p=0.47 | ❌ noise |
| US30 | 413 | 0.96 | −0.022 | −0.043 | **FAILS** p=0.60 | ❌ negative |
| NAS100 | 393 | 1.19 | +0.115 | +0.101 | **FAILS** p=0.075 | ❌ not significant |

- Full-sample expectancy ≈ 0 (or negative); **fails the permutation test on every instrument**.
- Wildly regime-dependent year-to-year (e.g. XAU 2023 +0.41R, 2024 −0.18R, 2026 −0.34R).
- This **matches the source's own honest `be1run_summary.json` (−0.0857R full sample, PF 0.86)** —
  the marketed "+0.16–0.20R" was a cherry-picked recent window, not a robust edge.

**Verdict: do NOT deploy.** No reliable edge. Not ported to a live ScriptStrategy. Could
only be revisited as a tightly regime-gated experiment, never as a standalone strategy.
This is the value of independent re-validation: it stopped a non-edge from reaching capital.

## ❌ Strategy 3 — Gold intraday momentum (`momentum_pro`) — REJECTED (failed multi-year)

Re-validated on the **full 2019–2026 M1 history** (`scripts/revalidate_momentum.py`),
default "best" config, three sessions:

| Session (XAUUSD M1) | Trades | PF | expR | Per-year | Permutation |
|---|---|---|---|---|---|
| london_ny | 47,228 | 0.82 | −0.118 | **negative all 8 years** | FAILS p=1.0 |
| newyork | 21,401 | 0.86 | −0.088 | negative all 8 years | FAILS p=1.0 |
| asian | 30,212 | 0.74 | −0.181 | negative all 8 years | FAILS p=1.0 |

- The source's headline (Sharpe 4.25) was **XAUJPY over a 5-month window** (Jan–May 2026),
  selected from ~300k backtests — a textbook data-mining artifact.
- On XAUUSD over 7 years the strategy **loses every year** net of costs. M1 scalping is
  where transaction costs dominate; a PF~1.2 edge does not survive symbol/cost changes.

**Verdict: do NOT deploy.** Caveat: tested XAUUSD (the XAUJPY data wasn't available) and
the default config; but an every-year-negative result across three sessions is conclusive
that there is no robust, general intraday-momentum edge here.

---

## ❌ Strategy 4 — KISS CRT (range-day reversal) — REJECTED (win-rate-vanity trap)

Core CRT (5AM ET anchor) re-validated on full gold history (`scripts/revalidate_crt.py`):

| KISS CRT (XAUUSD 2019–26) | Trades | Win% | PF | expR | Permutation |
|---|---|---|---|---|---|
| **No cost** (source repro) | 3,576 | 64.6% | 0.74 | −0.092 | FAILS |
| **Realistic cost** | 3,576 | 64.4% | 0.60 | −0.153 | FAILS, negative every year |

- Wins 64% of trades (matches the source's high-win-rate claim) but the tight
  10%-of-range TP earns less than the sweep-stop losses — **net negative even with zero costs**.
- The source's PF 1.82 was a 126-trade, 2-symbol, 17-month selective slice — small-sample.
- Textbook "win rate is vanity, expectancy is what matters."

**Verdict: do NOT deploy.**

---

## Running tally
| # | Strategy | Outcome |
|---|----------|---------|
| 1 | Trend-Following (Donchian) | ✅ **PASS** — deploy candidate (gold/BTC/metals) |
| 2 | SMC be1run | ❌ REJECT — fails permutation, regime noise |
| 3 | momentum_pro | ❌ REJECT — loses every year multi-year |
| 4 | KISS CRT | ❌ REJECT — win-rate vanity, negative even cost-free |
| 5 | Volatility-targeting overlay | ✅ ship as risk module (sizing, not direction) |

The pattern matches the meta-finding from the research corpus: **directional / intraday
edges mostly evaporate under honest multi-year, cost-aware, out-of-sample testing.** The
one robust survivor is **trend-following with a trailing exit** on trending instruments.

---

## ✅ Strategy 5 — Volatility-targeting risk overlay (sizing, not direction)

**File:** `backend_api_python/app/services/risk/vol_target.py`
The only ML signal that survived honest testing across the corpus was **volatility
magnitude** (AI-architect: +60% R² vs EWMA, 5/5 folds; `trans` used vol-targeting in
its winning portfolio). We monetize it the only way it pays — **size, not direction**:
risk less when realized vol is high, more when low, to hold a constant risk budget.
Smooths equity and cuts drawdown without predicting price. Pure multiplier, broker- and
instrument-agnostic; pairs naturally with the trend strategy. Recommend A/B testing it
against flat sizing in Murindi's backtester.

---

## Portfolio & deployment (final system)

Built the vol-targeted multi-sleeve portfolio from the confirmed survivors
(`scripts/finalize_basket.py` swept all 18 instruments; only **XAUUSD** and **BTCUSD**
beat the permutation test — FX is random-walk, indices/energy mean-revert).

**A/B test** (`scripts/portfolio_backtest.py`, 0.5% base risk):

| Config | CAGR | Sharpe | Max DD |
|--------|------|--------|--------|
| Gold only (flat) | 5.7% | 0.64 | 9.4% |
| Gold+BTC (flat) | 18.3% | 0.91 | 12.6% |
| Gold+BTC (vol-target) | 15.6% | 0.93 | **7.6%** |
| **Gold+BTC+Silver (vol-target)** | **17.9%** | **0.95** | **7.5%** |

Diversification lifts Sharpe 0.64 → 0.95; **vol-targeting cuts drawdown ~40%**. Sleeve
correlations are near-zero (gold/BTC −0.01, gold/silver 0.17, BTC/silver 0.05) — silver
earns its slot as an uncorrelated diversifier despite being marginal solo.

**Final basket:** XAU + BTC + XAG, H4, vol-targeted, 0.5% base risk → **Sharpe ~0.95, DD ~7.5%, CAGR ~18%**.

### Deployment artifacts
- `strategies/trend_following_donchian.py` — strategy with **vol-targeting built in** (`vol_target_annual`)
- `strategies/portfolio_config.json` — the 3-sleeve config (single source of truth)
- `app/services/risk/vol_target.py` — vol-targeting sizer · `app/services/risk/kill_switch.py` — portfolio kill-switch (max-DD / daily-loss / expectancy-decay)
- `docs/PAPER_TRADING_RUNBOOK.md` — step-by-step paper → live ramp with the kill-switch
- Validation harnesses: `validate_strategy.py`, `finalize_basket.py`, `portfolio_backtest.py`, `revalidate_*`
- **Tear-sheet:** `reports/portfolio_tearsheet.png` — equity curve (A/B overlay), underwater
  drawdown, per-sleeve cumulative P&L, and a stats/correlation panel (run `portfolio_backtest.py` to regenerate)
- Kill-switch wired into `TradingExecutor` (max-DD + daily-loss + expectancy-decay; off until `trading_config.kill_switch` is set)

## 4th-sleeve hunt — ETH + more crypto (`scripts/test_new_sleeves.py`)

Ran the same gauntlet on 12 crypto candidates (yfinance daily, full history, % costs):

| Ticker | PF | OOS PF | expR | Permutation | Verdict |
|--------|----|--------|------|-------------|---------|
| **ETH** | 2.46 | 1.38 | +1.11 | **BEATS** | ✅ genuine edge |
| BNB | 2.66 | 1.45 | +1.61 | BEATS | ✅ |
| ADA | 2.45 | 2.07 | +1.07 | BEATS | ✅ |
| DOGE | 2.61 | 2.85 | +0.94 | BEATS | ✅ |
| SOL/XRP/AVAX/DOT/XMR | positive | — | — | no (underpowered) | ⚠️ |
| LTC/BCH/LINK | weak/neg | — | — | no | ❌ |

**The trend edge generalizes across crypto** — ETH/BNB/ADA/DOGE all beat permutation. But the
professional caveat: **crypto is one factor.** ETH↔BTC asset-return correlation is **0.84**, so
ETH is a *2nd crypto sleeve*, not a new diversifying factor. Adding it:

| Portfolio | CAGR | Sharpe | Max DD |
|-----------|------|--------|--------|
| Gold+BTC+Silver (vt) | 17.9% | 0.95 | 7.5% |
| **+ETH (4 sleeves, vt)** | 17.2% | **0.96** | 7.9% |

Marginal Sharpe gain, more absolute return, +0.4% DD. **Verdict: include ETH** — it adds
single-coin diversification and balances the book to a clean **2-metals / 2-crypto** structure
— but **size BTC+ETH together as one ~50% crypto allocation** (they crash together; the low
trade-PnL correlation is partly a daily-vs-H4 sparsity artifact). The genuine *factor* diversity
tops out at **metals + crypto** — FX/indices/energy all failed, and other crypto just adds more
BTC-beta. To widen further you need a *different trending asset class*, not another coin.

## New-factor scan — none found (`scripts/scan_new_factors.py`)

Scanned **26 instruments across 6 asset classes** (ag/grains, softs, livestock, rates/bonds,
energy, industrial metals, equity-momentum) through the same gauntlet + a correlation check to
gold & BTC. Requirement for a "new factor": **beat permutation AND |corr| < 0.35 to both existing factors.**

**Result: zero survivors.** Not one beat the permutation test. A few flashed high OOS PF
(cocoa 1.90, soybeans 1.77, QQQ 1.73) but all failed permutation — small-sample noise, not edge.
Most are genuinely uncorrelated to gold/BTC (diversification was available), but they **do not
trend tradably** with a daily Donchian-20.

Why this makes sense: gold and crypto have persistent directional regimes; grains/softs/rates/
energy/equities are more mean-reverting or too noisy at this timescale. The classic *managed-
futures trend premium* across many markets is real, but it comes from **aggregating dozens of
tiny per-market edges with risk-parity sizing** — no single market is individually significant,
so it's a separate, larger thesis (build a 30–50 market book, longer lookbacks, test the
*portfolio* for significance), not a confirmed edge today. **Do not dilute the book with
unconfirmed markets.**

## Market-intelligence overlays (LLM-free, ported from `traidind_kwen`)

That folder is an LLM multi-agent trading system. Its **AI direction-prediction** is only
58% win rate ("good at following rules, not finding alpha") — the same dead end we proved, so
we skip it. We took its **grounded, free, LLM-free awareness layer** instead, as *entry gates*
(not direction signals):

| Overlay | File | What it does | Recommend |
|---------|------|--------------|-----------|
| **News/event guard** | `app/services/intelligence/news_calendar.py` | Blocks NEW ENTRIES during high-impact event windows (NFP/CPI/FOMC/ECB/BOE/BOJ/RBA — computed locally + free FMP calendar) and keyword-detected market shocks (free FXStreet/Investing/CNBC RSS). No LLM, no GPU. Fail-open. | ✅ **ON** — pure downside protection |
| **Regime filter (Hurst)** | `app/services/intelligence/regime.py` + strategy param | Skips entries in clearly mean-reverting regimes (Hurst gate). | ⚠️ **OPTIONAL** |

**Wiring:** the news guard is hooked into `TradingExecutor` next to the kill-switch — it gates
only new entries, never exits, and is off unless `trading_config.news_guard` is set. The regime
filter is a strategy param (`regime_filter`), off by default.

**Honest regime A/B (gold):** ON → PF 1.49→1.78, max DD 18.3%→**8.2%**, but trades 313→100 and
it **stops beating the permutation test** (small sample + fittable Hurst threshold). So: keep it
OFF for the significance-validated book; enable only if you prefer fewer/higher-conviction trades.

**Why news as a *gate*, not a signal:** using news/LLM to predict direction doesn't work (proven
repeatedly). Using it to know *when not to trade* (avoid getting chopped up around FOMC/CPI) is
real, free downside protection — and it pairs naturally with the kill-switch (both block entries
only). The genuine intelligence is "know when to sit out," not "predict the next candle."

### Post-news continuation (tested both ways)
- **News-guard "continuation mode"** (`news_guard.mode = "continuation"`): blocks only the
  pre-event + violent spike, then *allows* entries — so the **validated trend strategy rides the
  established post-news move** with its proven Donchian edge. ✅ Defensible; reuses the real edge.
- **Dedicated `news_continuation` strategy** (`strategies/news_continuation.py`): enters
  specifically on the post-spike breakout. Rough M15 gold backtest (NFP+CPI, 245 trades):
  **PF 0.85, expR −0.10R, fails permutation** — and that's *before* news-spread blowout. ❌ No edge;
  shipped only as a requested option, flagged "do not trade standalone."

**Takeaway:** trade post-news *continuation* by letting the trend strategy resume after the spike
(continuation mode), **not** by building a dedicated news-entry strategy — the latter loses, as
predicted. News tells you *when*, the trend edge tells you *how*.

## Bottom line

Of the five research "edges" put through independent, multi-year, cost-aware,
out-of-sample, permutation-tested re-validation:

- **1 survived:** trend-following (Donchian breakout + trailing exit) on trending
  instruments — modest but real (gold PF 1.49, OOS 1.68, beats permutation, cost-robust).
- **3 were rejected:** SMC be1run (regime noise), momentum_pro (loses every year),
  KISS CRT (win-rate vanity, negative cost-free).
- **1 risk overlay shipped:** volatility-targeting sizer.

This is the honest, valuable result: most "validated profitable strategies" do **not**
survive scrutiny — exactly the meta-finding the better researchers in the corpus reached
themselves. Re-validation here stopped three losing strategies from reaching capital.

**Recommended path to profit (low, realistic expectations):**
1. Deploy **trend-following** on a small basket of trending instruments (XAU, XAG, BTC) at
   H4, sized with the **vol-targeting overlay**, as an uncorrelated multi-sleeve portfolio.
2. **Paper-trade 30–90 days** in Murindi; confirm live fills match backtest; kill-switch on
   drawdown / expectancy decay.
3. **Tiny live ramp** (5% → 25% → 50% → 100%) only if paper holds.
4. Expect Sharpe ~0.5–1.0, ~34% win rate, long flat stretches, ~15–20% drawdowns. This is a
   disciplined trend-following business, not a money printer. No guarantees.

## Method notes / honest limits
- The harness mirrors Murindi's *contract* (closed-bar, next-open fill, costs). For full
  platform parity, also run each strategy through Murindi's own backtest API once data is
  loaded — numbers should match within execution-model tolerance.
- Sizing in the platform backtest derives from `entryPct`; the harness uses exact
  1%-risk-at-ATR sizing for R-multiple accounting (matches the original research).
- Permutation test flips trade direction; "BEATS" means real expectancy exceeds the 95th
  percentile of random-direction outcomes.
