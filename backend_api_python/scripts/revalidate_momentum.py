#!/usr/bin/env python3
"""Multi-year re-validation of the `momentum_pro` intraday gold scalper.

The source only validated Jan-May 2026 (5 months) across ~300k backtests -> high
data-mining risk. This runs the same signal logic on the FULL M1 history with a
fast cost-aware bracket simulator (fixed SL/TP, session-flat, max-hold) and the
gauntlet (per-year, OOS, permutation), to see whether the edge persists.
"""
import argparse, math, pathlib
import numpy as np, pandas as pd

SESSIONS = {"all": (0, 1440), "asian": (0, 480), "london": (420, 960),
            "newyork": (780, 1260), "london_ny": (420, 1260)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--session", default="london_ny", choices=list(SESSIONS))
    ap.add_argument("--lookback", type=int, default=15)
    ap.add_argument("--thrust_atr", type=float, default=1.0)
    ap.add_argument("--ema_fast", type=int, default=50)
    ap.add_argument("--ema_slow", type=int, default=100)
    ap.add_argument("--atr_period", type=int, default=14)
    ap.add_argument("--sl_atr", type=float, default=2.0)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--vol_mult", type=float, default=1.0)
    ap.add_argument("--max_hold", type=int, default=180)
    ap.add_argument("--point", type=float, default=0.01)
    ap.add_argument("--slip", type=float, default=3.0)
    ap.add_argument("--oos-year", type=int, default=2024)
    args = ap.parse_args()

    df = pd.read_pickle(args.data)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    spr = df["spread"].to_numpy(float) if "spread" in df.columns else np.zeros(len(c))
    utc = (pd.to_datetime(df["ny_time"], utc=True) if "ny_time" in df.columns
           else pd.to_datetime(df.index, utc=True))
    mod = (utc.dt.hour * 60 + utc.dt.minute).to_numpy()
    years = utc.dt.year.to_numpy()
    n = len(c)

    cs = pd.Series(c)
    ef = cs.ewm(span=args.ema_fast, adjust=False).mean().to_numpy()
    es = cs.ewm(span=args.ema_slow, adjust=False).mean().to_numpy()
    tr = np.maximum.reduce([h - l, np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))])
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).rolling(args.atr_period, min_periods=args.atr_period).mean().to_numpy()
    med = pd.Series(atr).rolling(500, min_periods=120).median().to_numpy()
    thrust = (c - np.roll(c, args.lookback)) / np.where(atr == 0, np.nan, atr)

    s0, s1 = SESSIONS[args.session]
    in_sess = (mod >= s0) & (mod < s1)
    expansion = atr >= args.vol_mult * med
    active = in_sess & expansion & ~np.isnan(thrust) & ~np.isnan(med)
    up = (thrust >= args.thrust_atr) & (ef > es) & active
    dn = (thrust <= -args.thrust_atr) & (ef < es) & active

    warmup = max(args.ema_slow, 500, args.atr_period, args.lookback) + 2
    pos = 0; entry = sl = tp = 0.0; ebar = 0; risk = 0.0
    Rs = []; yrs = []
    def cost(i):
        return spr[i] * args.point + args.slip * args.point
    for i in range(warmup, n - 1):
        if pos != 0:
            exit_px = None
            if pos == 1:
                if l[i] <= sl: exit_px = sl
                elif h[i] >= tp: exit_px = tp
            else:
                if h[i] >= sl: exit_px = sl
                elif l[i] <= tp: exit_px = tp
            if exit_px is None and (i - ebar >= args.max_hold or not in_sess[i]):
                exit_px = c[i]
            if exit_px is not None:
                r = ((exit_px - entry) * pos - cost(i)) / risk
                Rs.append(r); yrs.append(years[i]); pos = 0
                continue
        if pos == 0:
            sig = 1 if up[i] else (-1 if dn[i] else 0)
            if sig != 0 and atr[i] > 0:
                pos = sig; entry = o[i + 1] + sig * cost(i)
                risk = args.sl_atr * atr[i]
                sl = entry - sig * risk; tp = entry + sig * args.rr * risk; ebar = i + 1

    R = np.array(Rs); yrs = np.array(yrs)
    sym = pathlib.Path(args.data).stem
    print(f"\n=== momentum_pro  {sym} M1  session={args.session}  {utc.iloc[0].date()}..{utc.iloc[-1].date()} ===")
    print(f"params: lb={args.lookback} thrust={args.thrust_atr} ema={args.ema_fast}/{args.ema_slow} "
          f"sl={args.sl_atr}xATR rr={args.rr} vol_mult={args.vol_mult}")

    def stats(r, label):
        if len(r) == 0:
            print(f"  {label:<16} no trades"); return
        w = r[r > 0]; ls = r[r < 0]
        pf = w.sum() / abs(ls.sum()) if ls.sum() != 0 else float("inf")
        sharpe = r.mean() / r.std() * math.sqrt(len(r) / 7.4) if r.std() > 0 else 0
        print(f"  {label:<16} n={len(r):>5} win={100*(r>0).mean():4.1f}% PF={pf:.3f} "
              f"expR={r.mean():+.4f} Sharpe~{sharpe:+.2f} netR={r.sum():+.1f}")
    stats(R, "FULL")
    stats(R[yrs < args.oos_year], f"IS <{args.oos_year}")
    stats(R[yrs >= args.oos_year], f"OOS >={args.oos_year}")
    print("  per-year:")
    for y in sorted(set(yrs.tolist())):
        ry = R[yrs == y]
        if len(ry):
            pf = ry[ry > 0].sum() / abs(ry[ry < 0].sum()) if ry[ry < 0].sum() != 0 else float("inf")
            print(f"    {y}: n={len(ry):>4} PF={pf:.3f} expR={ry.mean():+.4f}")
    if len(R) > 5:
        rng = np.random.default_rng(0); real = R.mean()
        perms = np.array([(R * rng.choice([-1, 1], size=len(R))).mean() for _ in range(300)])
        p95 = np.percentile(perms, 95)
        print(f"  permutation: real expR={real:+.4f} vs p95={p95:+.4f} -> "
              f"{'BEATS (edge)' if real > p95 else 'FAILS (noise)'} (p={(perms>=real).mean():.3f})")


if __name__ == "__main__":
    main()
