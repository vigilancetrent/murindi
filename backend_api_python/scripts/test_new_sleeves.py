#!/usr/bin/env python3
"""Hunt for a genuine 4th+ sleeve: run the trend strategy through the same gauntlet
on new instruments (crypto via yfinance, daily full history). A candidate only
qualifies if it BEATS the permutation test with positive OOS and enough trades.
"""
import importlib.util, pathlib, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

HERE = pathlib.Path(__file__).resolve().parent
vs = importlib.util.module_from_spec(importlib.util.spec_from_file_location("vs", HERE / "validate_strategy.py"))
importlib.util.spec_from_file_location("vs", HERE / "validate_strategy.py").loader.exec_module(vs)
import yfinance as yf

STRAT = HERE.parent / "strategies" / "trend_following_donchian.py"
COST_FRAC = 0.0005   # 5 bps per side (liquid crypto), modeled as % of price
# candidate crypto (Yahoo tickers)
CANDIDATES = ["ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "LTC-USD", "ADA-USD",
              "DOGE-USD", "AVAX-USD", "DOT-USD", "LINK-USD", "BCH-USD", "XMR-USD"]


def fetch_daily(ticker):
    df = yf.download(ticker, period="max", interval="1d", progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df["spread"] = df["close"] * COST_FRAC   # cost = spread*point(=1) per side
    df.index = pd.to_datetime(df.index)
    return df


def main():
    code = STRAT.read_text(encoding="utf-8")
    ns = {"np": np, "pd": pd}; exec(code, ns, ns); on_bar = ns["on_bar"]
    import re
    params = {n: {"int": int, "float": float, "bool": lambda x: x.lower() == "true"}.get(t, str)(d)
              for n, t, d, _ in re.findall(r"# @param (\w+) (\w+) (\S+) (.+)", code)}

    print(f"\n{'TICKER':<10}{'bars':>6}{'n':>5}{'FULL PF':>9}{'OOS PF':>8}{'expR':>8}{'maxDD':>7}{'perm':>7}  verdict")
    print("-" * 72)
    survivors = []
    for tk in CANDIDATES:
        df = fetch_daily(tk)
        if df is None or len(df) < 400:
            print(f"{tk:<10}  (insufficient history)")
            continue
        trades, _ = vs.backtest(on_bar, df, params, point=1.0, slip_points=0.0, cost_mult=1.0, risk_pct=0.01)
        m = vs.metrics(trades)
        if m.get("trades", 0) == 0:
            print(f"{tk:<10}{len(df):>6}   no trades"); continue
        oos = vs.metrics([t for t in trades if pd.Timestamp(t[0]).year >= 2023])
        pt = vs.permutation_test(trades) or {}
        beats = pt.get("beats_permutation", False)
        enough = m["trades"] >= 60
        surv = beats and oos.get("profit_factor", 0) >= 1.3 and enough
        verdict = "SURVIVOR" if surv else ("underpowered" if (beats and not enough) else "-")
        if surv:
            survivors.append(tk)
        print(f"{tk:<10}{len(df):>6}{m['trades']:>5}{m['profit_factor']:>9.2f}{oos.get('profit_factor',0):>8.2f}"
              f"{m['expectancy_R']:>+8.3f}{m['max_dd']*100:>6.1f}%{'  yes' if beats else '   no':>7}  {verdict}")

    print(f"\nNew survivors: {survivors or 'none'}")
    print("(daily crypto -> fewer trades than the H4 core; 'underpowered' = positive but n<60)")


if __name__ == "__main__":
    main()
