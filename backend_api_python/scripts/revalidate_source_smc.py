#!/usr/bin/env python3
"""Independent re-validation of the *source* SMC be1run logic on real data.

Runs the original be1run_strategy.process() (limit fills, intrabar stop/target,
session filter) on real M15 history and reports the gauntlet. This confirms
whether the IDEA holds, separate from how it maps onto Murindi's executor.
"""
import argparse, importlib.util, math, pathlib
import numpy as np, pandas as pd


def load_be1run(src):
    spec = importlib.util.spec_from_file_location("be1run", src)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def m15_with_utc(pkl):
    df = pd.read_pickle(pkl)
    if "ny_time" in df.columns:
        df["utc"] = pd.to_datetime(df["ny_time"], utc=True)
    else:
        df["utc"] = pd.to_datetime(df.index, utc=True)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "utc": "first"}
    if "spread" in df.columns:
        agg["spread"] = "mean"
    m = df.resample("15min").agg(agg).dropna(subset=["open", "high", "low", "close"])
    if "spread" not in m.columns:
        m["spread"] = 0.0
    return m


def metrics(r, years_span):
    r = np.asarray(r, float)
    if len(r) == 0:
        return None
    wins = r[r > 0]; losses = r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    tpy = len(r) / max(years_span, 1e-9)
    sharpe = (r.mean() / r.std() * math.sqrt(tpy)) if r.std() > 0 else 0.0
    eq = np.cumsum(r); peak = np.maximum.accumulate(np.concatenate([[0], eq]))
    dd = (peak - np.concatenate([[0], eq])).max()
    return dict(n=len(r), win=float((r > 0).mean()), pf=float(pf),
                expR=float(r.mean()), sharpe=float(sharpe), maxDD_R=float(dd), netR=float(r.sum()))


def perm(r, n_perm=400, seed=0):
    r = np.asarray(r, float); rng = np.random.default_rng(seed)
    real = r.mean()
    perms = np.array([(r * rng.choice([-1, 1], size=len(r))).mean() for _ in range(n_perm)])
    return real, float(np.percentile(perms, 95)), float((perms >= real).mean())


def fmt(m, label):
    if not m:
        return f"  {label:<14} no trades"
    return (f"  {label:<14} n={m['n']:>4}  win={m['win']*100:4.1f}%  PF={m['pf']:.2f}  "
            f"expR={m['expR']:+.3f}  Sharpe={m['sharpe']:+.2f}  maxDD={m['maxDD_R']:.1f}R")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=r"C:\Users\Hp\Downloads\intavidoetrading\be1run_strategy.py")
    ap.add_argument("--data", required=True)
    ap.add_argument("--point", type=float, default=0.01)
    ap.add_argument("--oos-year", type=int, default=2023)
    args = ap.parse_args()

    be = load_be1run(args.src)
    sym = pathlib.Path(args.data).stem
    m = m15_with_utc(args.data)
    td = be.process(m, sym, args.point, {})
    print(f"\n=== SOURCE SMC be1run  {sym} M15  bars={len(m)}  {m.index[0].date()}..{m.index[-1].date()} ===")
    if td is None or len(td) == 0:
        print("  no trades"); return
    td["year"] = td["entry_dt"].dt.year
    span = (td["entry_dt"].iloc[-1] - td["entry_dt"].iloc[0]).days / 365.25
    r_all = td["r_multiple"].to_numpy()
    print(fmt(metrics(r_all, span), "FULL"))
    is_r = td[td.year < args.oos_year]["r_multiple"].to_numpy()
    oos_r = td[td.year >= args.oos_year]["r_multiple"].to_numpy()
    print(fmt(metrics(is_r, max(span/2, 1)), f"IS <{args.oos_year}"))
    print(fmt(metrics(oos_r, max(span/2, 1)), f"OOS >={args.oos_year}"))
    print("  per-year:")
    for y in sorted(td.year.unique()):
        yr = td[td.year == y]["r_multiple"].to_numpy()
        my = metrics(yr, 1)
        print(f"    {y}: n={my['n']:>3} PF={my['pf']:.2f} expR={my['expR']:+.3f}")
    real, p95, pval = perm(r_all)
    print(f"  permutation: real expR={real:+.3f} vs p95={p95:+.3f} -> "
          f"{'BEATS (edge)' if real > p95 else 'FAILS (noise)'} (p={pval:.3f})")


if __name__ == "__main__":
    main()
