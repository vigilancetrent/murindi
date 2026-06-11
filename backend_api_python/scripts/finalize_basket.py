#!/usr/bin/env python3
"""Sweep the trend-following strategy across every available instrument at H4 to
finalize the tradeable basket. Keeps only survivors (OOS PF >= 1.3, beats
permutation, >= 60 trades) and saves their per-symbol trade streams for the
portfolio builder.
"""
import importlib.util, json, pathlib, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("vs", HERE / "validate_strategy.py")
vs = importlib.util.module_from_spec(spec); spec.loader.exec_module(vs)

DATA = pathlib.Path(r"C:\Users\Hp\Downloads\intavidoetrading\data")
STRAT = HERE.parent / "strategies" / "trend_following_donchian.py"
OUT = HERE.parent / "strategies" / "_basket_trades.json"

# point (price increment) + per-side slippage(points) + crypto flag, per symbol
SPEC = {
    "XAUUSD": (0.01, 2, False), "XAGUSD": (0.001, 2, False),
    "WTI": (0.01, 3, False), "BRENT": (0.01, 3, False), "NATGAS": (0.001, 3, False),
    "BTCUSD": (0.01, 50, True),
    "NAS100": (0.1, 10, False), "US30": (1.0, 5, False), "US500": (0.1, 3, False),
    "EURUSD": (0.00001, 2, False), "GBPUSD": (0.00001, 2, False), "AUDUSD": (0.00001, 2, False),
    "NZDUSD": (0.00001, 2, False), "EURGBP": (0.00001, 2, False),
    "AUDJPY": (0.001, 2, False), "EURJPY": (0.001, 2, False), "GBPJPY": (0.001, 2, False),
    "NZDJPY": (0.001, 2, False),
}


def main():
    code = STRAT.read_text(encoding="utf-8")
    ns = {"np": np, "pd": __import__("pandas")}
    exec(code, ns, ns)
    on_bar = ns["on_bar"]
    import re
    params = {}
    for name, typ, default, _ in re.findall(r"# @param (\w+) (\w+) (\S+) (.+)", code):
        params[name] = {"int": int, "float": float, "bool": lambda x: x.lower() == "true"}.get(typ, str)(default)

    rows = []
    saved = {}
    for sym, (point, slip, crypto) in SPEC.items():
        f = DATA / f"{sym}_M1.pkl"
        if not f.exists():
            continue
        try:
            df = vs.load_resample(str(f), "4h")
        except Exception as e:
            print(f"  {sym}: load error {e}"); continue
        trades, _eq = vs.backtest(on_bar, df, params, point, slip, cost_mult=1.0, risk_pct=0.01)
        m = vs.metrics(trades)
        if m.get("trades", 0) == 0:
            continue
        oos = vs.metrics([t for t in trades if vs.pd.Timestamp(t[0]).year >= 2023])
        pt = vs.permutation_test(trades) or {}
        survivor = (oos.get("profit_factor", 0) >= 1.3 and pt.get("beats_permutation") and m["trades"] >= 60)
        rows.append((sym, m, oos, pt, survivor))
        if survivor:
            saved[sym] = [[str(t[0]), float(t[1]), float(t[2])] for t in trades]  # (time, R, pnl)

    rows.sort(key=lambda r: (r[4], r[2].get("profit_factor", 0)), reverse=True)
    print(f"\n{'SYM':<8}{'n':>5}{'FULL PF':>9}{'OOS PF':>8}{'expR':>8}{'Sharpe':>8}{'maxDD':>7}{'perm':>7}  verdict")
    print("-" * 74)
    for sym, m, oos, pt, surv in rows:
        print(f"{sym:<8}{m['trades']:>5}{m['profit_factor']:>9.2f}{oos.get('profit_factor',0):>8.2f}"
              f"{m['expectancy_R']:>+8.3f}{m['sharpe']:>+8.2f}{m['max_dd']*100:>6.1f}%"
              f"{'  yes' if pt.get('beats_permutation') else '   no':>7}  {'SURVIVOR' if surv else '-'}")

    OUT.write_text(json.dumps(saved), encoding="utf-8")
    print(f"\nSurvivors: {list(saved.keys())}")
    print(f"saved trade streams -> {OUT.name}")


if __name__ == "__main__":
    main()
