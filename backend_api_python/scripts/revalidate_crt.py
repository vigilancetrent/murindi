#!/usr/bin/env python3
"""Cost-aware re-validation of KISS CRT (range-day reversal) on gold.

Core CRT logic (5AM ET anchor, owner's main setup):
  anchor = the 05:00-09:00 ET 4H candle; range = high-low
  entry window 09:00-11:30 ET: wait for a SWEEP of anchor high/low, then a close
    back inside the range within 8 bars -> reversal entry
  stop  = sweep_extreme +/- 5% of range
  TP    = 10% of range from entry (tight -> high win rate, sub-1 RR)
  manage forward until stop/TP or hard close 15:55 ET; up to 4 entries/day
The source modeled NO costs; here we charge real spread + slippage.
"""
import argparse, math, pathlib
import numpy as np, pandas as pd
from datetime import time

ET = "America/New_York"
SWEEP_REJECT_BARS = 8
RISK_BUFFER = 0.05
TP_FRAC = 0.10
MAX_ENTRIES = 4
HARD_CLOSE = time(15, 55)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--point", type=float, default=0.01)
    ap.add_argument("--slip", type=float, default=3.0)
    ap.add_argument("--oos-year", type=int, default=2024)
    ap.add_argument("--no-cost", action="store_true", help="reproduce source (costs off)")
    args = ap.parse_args()

    df = pd.read_pickle(args.data)
    if "ny_time" in df.columns:
        df = df.set_index(pd.to_datetime(df["ny_time"], utc=True).dt.tz_convert(ET))
    else:
        df = df.tz_localize("UTC").tz_convert(ET)
    df = df[["open", "high", "low", "close"] + (["spread"] if "spread" in df.columns else [])]
    naive = df.tz_localize(None)
    m5 = naive.resample("5min").agg({"open": "first", "high": "max", "low": "min",
                                     "close": "last", **({"spread": "mean"} if "spread" in df.columns else {})}).dropna(subset=["open"])
    if "spread" not in m5.columns:
        m5["spread"] = 0.0
    sess = naive.resample("4h", offset="1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

    cost_px = 0.0 if args.no_cost else None
    Rs, yrs = [], []
    for day, win in m5.groupby(m5.index.date):
        anchor_ts = pd.Timestamp(day).replace(hour=5)
        if anchor_ts not in sess.index:
            continue
        a = sess.loc[anchor_ts]
        rng = a["high"] - a["low"]
        if rng <= 0:
            continue
        w = win.between_time(time(9, 0), time(11, 30))
        after = win.between_time(time(9, 0), time(15, 55))
        if w.empty:
            continue
        rows = list(w.iterrows())
        after_rows = list(after.iterrows())
        sweep = None; sweep_ext = None; sweep_i = None; last_entry = -1; entries = 0
        for i, (ts, b) in enumerate(rows):
            if i <= last_entry or entries >= MAX_ENTRIES:
                continue
            if sweep is None:
                if b["high"] > a["high"]:
                    sweep, sweep_ext, sweep_i = "H", b["high"], i
                elif b["low"] < a["low"]:
                    sweep, sweep_ext, sweep_i = "L", b["low"], i
                continue
            if i - sweep_i > SWEEP_REJECT_BARS:
                sweep = None; continue
            entry = direction = stop = tp = None
            if sweep == "H" and b["close"] < a["high"]:
                direction = -1; entry = b["close"]; stop = sweep_ext + RISK_BUFFER * rng
                tp = entry - TP_FRAC * rng
            elif sweep == "L" and b["close"] > a["low"]:
                direction = 1; entry = b["close"]; stop = sweep_ext - RISK_BUFFER * rng
                tp = entry + TP_FRAC * rng
            if direction is None:
                continue
            risk = abs(entry - stop)
            if risk <= 0:
                sweep = None; last_entry = i; continue
            # cost in price
            cps = (b["spread"] * args.point + args.slip * args.point) if cost_px is None else 0.0
            entry_f = entry + direction * cps
            # manage forward from this ts
            exit_px = after_rows[-1][1]["close"]
            for ts2, b2 in after_rows:
                if ts2 < ts:
                    continue
                if ts2.time() >= HARD_CLOSE:
                    exit_px = b2["close"]; break
                if direction == 1:
                    if b2["low"] <= stop:
                        exit_px = stop; break
                    if b2["high"] >= tp:
                        exit_px = tp; break
                else:
                    if b2["high"] >= stop:
                        exit_px = stop; break
                    if b2["low"] <= tp:
                        exit_px = tp; break
            exit_f = exit_px - direction * cps
            r = ((exit_f - entry_f) * direction) / risk
            Rs.append(r); yrs.append(ts.year)
            sweep = None; last_entry = i; entries += 1

    R = np.array(Rs); yrs = np.array(yrs)
    sym = pathlib.Path(args.data).stem
    tag = "NO-COST (source repro)" if args.no_cost else "real cost"
    print(f"\n=== KISS CRT  {sym}  [{tag}]  trades={len(R)} ===")

    def stats(r, label):
        if len(r) == 0:
            print(f"  {label:<14} no trades"); return
        w = r[r > 0]; ls = r[r < 0]
        pf = w.sum() / abs(ls.sum()) if ls.sum() != 0 else float("inf")
        print(f"  {label:<14} n={len(r):>4} win={100*(r>0).mean():4.1f}% PF={pf:.3f} "
              f"expR={r.mean():+.4f} netR={r.sum():+.1f}")
    stats(R, "FULL")
    stats(R[yrs < args.oos_year], f"IS <{args.oos_year}")
    stats(R[yrs >= args.oos_year], f"OOS >={args.oos_year}")
    print("  per-year:")
    for y in sorted(set(yrs.tolist())):
        ry = R[yrs == y]
        pf = ry[ry > 0].sum() / abs(ry[ry < 0].sum()) if ry[ry < 0].sum() != 0 else float("inf")
        print(f"    {y}: n={len(ry):>4} PF={pf:.3f} expR={ry.mean():+.4f}")
    if len(R) > 5:
        rng_ = np.random.default_rng(0); real = R.mean()
        perms = np.array([(R * rng_.choice([-1, 1], size=len(R))).mean() for _ in range(300)])
        p95 = np.percentile(perms, 95)
        print(f"  permutation: real={real:+.4f} vs p95={p95:+.4f} -> "
              f"{'BEATS (edge)' if real > p95 else 'FAILS (noise)'}")


if __name__ == "__main__":
    main()
