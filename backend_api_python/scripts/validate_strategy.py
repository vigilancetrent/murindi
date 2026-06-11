#!/usr/bin/env python3
"""
Strategy re-validation harness for ported Murindi ScriptStrategy files.

It runs the *exact* on_bar() code from a ported strategy through a faithful
replica of Murindi's closed-bar ScriptStrategy execution model:
  - on_bar is called on each CLOSED bar
  - orders (buy/sell/close_position) fill at the NEXT bar's open
  - real per-bar spread + slippage are charged on entry and exit
  - position sizing risks `risk_pct` of equity at the strategy's ATR stop
    (R-multiple accounting), matching the original research

This is an independent re-validation on real history — we do NOT trust the
source backtests. Metrics: trades, win rate, profit factor, expectancy (R),
Sharpe, max drawdown, CAGR, plus in-sample/out-of-sample split, per-year
walk-forward, and a permutation test.

Usage:
    python validate_strategy.py --strategy ../strategies/trend_following_donchian.py \
        --data "C:/Users/Hp/Downloads/intavidoetrading/data/XAUUSD_M1.pkl" --tf 4H
"""
from __future__ import annotations
import argparse
import math
import pathlib
import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- ctx replica
class _Bar:
    __slots__ = ("open", "high", "low", "close", "volume", "timestamp")

    def __init__(self, o, h, l, c, v, t):
        self.open, self.high, self.low, self.close, self.volume, self.timestamp = o, h, l, c, v, t


class _Position:
    def __init__(self):
        self.direction = 0
        self.entry_price = 0.0
        self.size = 0.0

    def __bool__(self):
        return self.direction != 0

    def __int__(self):
        return self.direction

    def __gt__(self, other):
        return self.direction > int(other)

    def __lt__(self, other):
        return self.direction < int(other)

    def __getitem__(self, k):
        return {"entry_price": self.entry_price, "side": "long" if self.direction > 0 else ("short" if self.direction < 0 else ""),
                "size": self.size, "direction": self.direction}[k]


class _Ctx:
    def __init__(self, params):
        self._params = dict(params or {})
        self._o = np.empty(0); self._h = np.empty(0); self._l = np.empty(0)
        self._c = np.empty(0); self._v = np.empty(0); self._t = None
        self.current_index = -1
        self.position = _Position()
        self.equity = 0.0
        self.balance = 0.0
        self._orders = []

    def param(self, name, default=None):
        return self._params.get(name, default)

    def log(self, *_a, **_k):
        pass

    def bars(self, n=1):
        i = self.current_index
        start = max(0, i - int(n) + 1)
        out = []
        for j in range(start, i + 1):
            out.append(_Bar(self._o[j], self._h[j], self._l[j], self._c[j], self._v[j],
                            self._t[j] if self._t is not None else None))
        return out

    def buy(self, price=None, amount=None, **kw):
        self._orders.append(("buy", kw.get("intent", "auto")))

    def sell(self, price=None, amount=None, **kw):
        self._orders.append(("sell", kw.get("intent", "auto")))

    def close_position(self):
        self._orders.append(("close", None))

    # bot/hedge intents not used by trend strategy; accept & ignore for safety
    def open_long(self, *a, **k): self._orders.append(("buy", "open_long"))
    def open_short(self, *a, **k): self._orders.append(("sell", "open_short"))
    def close_long(self, *a, **k): self._orders.append(("close", None))
    def close_short(self, *a, **k): self._orders.append(("close", None))
    def add_long(self, *a, **k): pass
    def add_short(self, *a, **k): pass


# --------------------------------------------------------------------------- data
def load_resample(path: str, tf: str) -> pd.DataFrame:
    df = pd.read_pickle(path)
    df = df.rename(columns={"tick_volume": "volume"})
    vol = "volume" if "volume" in df.columns else None
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if vol:
        agg["volume"] = "sum"
    if "spread" in df.columns:
        agg["spread"] = "mean"
    r = df.resample(tf).agg(agg).dropna(subset=["open", "high", "low", "close"])
    if "volume" not in r.columns:
        r["volume"] = 0.0
    if "spread" not in r.columns:
        r["spread"] = 0.0
    return r


# --------------------------------------------------------------------------- backtest
def _atr_at(h, l, c, i, period):
    trs = []
    for k in range(i - period + 1, i + 1):
        trs.append(max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1])))
    return sum(trs) / len(trs) if trs else 0.0


def backtest(on_bar, df: pd.DataFrame, params: dict, point: float, slip_points: float,
             cost_mult: float = 1.0, risk_pct: float = 0.01, start_equity: float = 10000.0):
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float); spr = df["spread"].to_numpy(float)
    t = df.index.to_numpy()
    n = len(df)

    ctx = _Ctx(params)
    ctx._o, ctx._h, ctx._l, ctx._c, ctx._v, ctx._t = o, h, l, c, v, t

    atr_n = int(params.get("atr_period", 14))
    atr_mult = float(params.get("atr_mult", 2.0))
    warmup = max(int(params.get("donchian_entry", 20)), atr_n, int(params.get("donchian_exit", 10))) + 3

    equity = start_equity
    pos_dir = 0; entry_px = 0.0; size = 0.0; risk_dollar = 0.0
    trades = []          # (exit_time, R, pnl, equity_after)
    eq_curve = [(t[warmup], equity)]

    def cost_price(i):
        # round-trip-ish per-side cost in price units
        return (spr[i] * point + slip_points * point) * cost_mult

    for i in range(warmup, n - 1):
        ctx.current_index = i
        ctx.equity = equity
        ctx.position.direction = pos_dir
        ctx.position.entry_price = entry_px
        ctx.position.size = size
        ctx._orders = []
        on_bar(ctx, _Bar(o[i], h[i], l[i], c[i], v[i], t[i]))

        nxt_open = o[i + 1]
        cps = cost_price(i)
        for action, _intent in ctx._orders:
            if action == "close" and pos_dir != 0:
                exit_px = nxt_open - pos_dir * cps
                pnl = size * (exit_px - entry_px) * pos_dir
                equity += pnl
                R = pnl / risk_dollar if risk_dollar else 0.0
                trades.append((t[i + 1], R, pnl, equity))
                eq_curve.append((t[i + 1], equity))
                pos_dir = 0; entry_px = 0.0; size = 0.0; risk_dollar = 0.0
            elif action in ("buy", "sell") and pos_dir == 0:
                d = 1 if action == "buy" else -1
                atr = _atr_at(h, l, c, i, atr_n)
                stop_dist = atr_mult * atr
                if stop_dist <= 0:
                    continue
                entry_px = nxt_open + d * cps
                risk_dollar = equity * risk_pct
                size = risk_dollar / stop_dist
                pos_dir = d

    return trades, eq_curve


# --------------------------------------------------------------------------- metrics
def metrics(trades):
    if not trades:
        return {"trades": 0}
    R = np.array([x[1] for x in trades]); pnl = np.array([x[2] for x in trades])
    wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    # annualization from trade timestamps
    times = pd.to_datetime([x[0] for x in trades])
    years = max((times[-1] - times[0]).days / 365.25, 1e-9)
    tpy = len(trades) / years
    sharpe = (R.mean() / R.std() * math.sqrt(tpy)) if R.std() > 0 else 0.0
    eq = np.concatenate([[10000.0], 10000.0 + np.cumsum(pnl)])
    peak = np.maximum.accumulate(eq); dd = (peak - eq) / peak
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1
    return {"trades": len(trades), "win_rate": float((R > 0).mean()),
            "profit_factor": float(pf), "expectancy_R": float(R.mean()),
            "sharpe": float(sharpe), "max_dd": float(dd.max()),
            "cagr": float(cagr), "net_R": float(R.sum()), "years": float(years)}


def permutation_test(trades, n_perm=400, seed=0):
    """Shuffle trade DIRECTION (flip sign of each R) — does real expectancy beat random?"""
    if not trades:
        return None
    R = np.array([x[1] for x in trades]); rng = np.random.default_rng(seed)
    real = R.mean()
    perms = np.array([(R * rng.choice([-1, 1], size=len(R))).mean() for _ in range(n_perm)])
    p95 = np.percentile(perms, 95)
    pval = float((perms >= real).mean())
    return {"real_expectancy_R": float(real), "perm_p95": float(p95),
            "p_value": pval, "beats_permutation": bool(real > p95)}


def fmt(m, label):
    if m.get("trades", 0) == 0:
        return f"  {label:<14} no trades"
    return (f"  {label:<14} n={m['trades']:>4}  win={m['win_rate']*100:4.1f}%  "
            f"PF={m['profit_factor']:.2f}  expR={m['expectancy_R']:+.3f}  "
            f"Sharpe={m['sharpe']:+.2f}  maxDD={m['max_dd']*100:4.1f}%  CAGR={m['cagr']*100:+.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--tf", default="4H")
    ap.add_argument("--point", type=float, default=0.01)
    ap.add_argument("--slip", type=float, default=2.0, help="slippage in points per side")
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--oos-year", type=int, default=2023, help="trades on/after this year are out-of-sample")
    args = ap.parse_args()

    code = pathlib.Path(args.strategy).read_text(encoding="utf-8")
    ns = {"np": np, "pd": pd}
    exec(code, ns, ns)
    on_bar = ns["on_bar"]
    # pull defaults from @param headers
    import re
    params = {}
    for name, typ, default, _desc in re.findall(r"# @param (\w+) (\w+) (\S+) (.+)", code):
        params[name] = {"int": int, "float": float, "bool": lambda x: str(x).lower() == "true"}.get(typ, str)(default)

    df = load_resample(args.data, args.tf)
    sym = pathlib.Path(args.data).stem
    print(f"\n=== {sym}  tf={args.tf}  bars={len(df)}  {df.index[0].date()}..{df.index[-1].date()} ===")
    print(f"params: {params}")

    for cm, tag in [(1.0, "real cost"), (2.0, "2x cost-stress")]:
        trades, eq = backtest(on_bar, df, params, args.point, args.slip, cost_mult=cm, risk_pct=args.risk)
        m = metrics(trades)
        print(f"\n[{tag}]")
        print(fmt(m, "FULL"))
        # in-sample / out-of-sample
        is_t = [x for x in trades if pd.Timestamp(x[0]).year < args.oos_year]
        oos_t = [x for x in trades if pd.Timestamp(x[0]).year >= args.oos_year]
        print(fmt(metrics(is_t), f"IS <{args.oos_year}"))
        print(fmt(metrics(oos_t), f"OOS >={args.oos_year}"))
        if cm == 1.0:
            # per-year walk-forward
            print("  per-year:")
            yrs = sorted({pd.Timestamp(x[0]).year for x in trades})
            for y in yrs:
                yt = [x for x in trades if pd.Timestamp(x[0]).year == y]
                my = metrics(yt)
                print(f"    {y}: n={my['trades']:>3} PF={my['profit_factor']:.2f} expR={my['expectancy_R']:+.3f}")
            pt = permutation_test(trades)
            if pt:
                print(f"  permutation: real expR={pt['real_expectancy_R']:+.3f} vs p95={pt['perm_p95']:+.3f} "
                      f"-> {'BEATS (edge)' if pt['beats_permutation'] else 'FAILS (noise)'} (p={pt['p_value']:.3f})")


if __name__ == "__main__":
    main()
