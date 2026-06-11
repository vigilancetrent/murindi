#!/usr/bin/env python3
"""Scan diverse asset classes for a genuinely NEW trending factor.

A candidate qualifies only if it BOTH (a) beats the permutation test on the trend
strategy AND (b) is uncorrelated (|corr| < 0.35) to BOTH existing factors (gold + BTC).
Most will fail — that's expected. yfinance daily, full history, realistic % costs.
"""
import importlib.util, pathlib, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

HERE = pathlib.Path(__file__).resolve().parent
vs = importlib.util.module_from_spec(importlib.util.spec_from_file_location("vs", HERE / "validate_strategy.py"))
importlib.util.spec_from_file_location("vs", HERE / "validate_strategy.py").loader.exec_module(vs)
import yfinance as yf

STRAT = HERE.parent / "strategies" / "trend_following_donchian.py"
COST_FRAC = 0.0005

CANDIDATES = {
    # ticker: asset class
    "ZC=F": "ag/grain", "ZW=F": "ag/grain", "ZS=F": "ag/grain", "ZL=F": "ag/oil",
    "KC=F": "soft", "SB=F": "soft", "CT=F": "soft", "CC=F": "soft", "OJ=F": "soft",
    "LE=F": "livestock", "HE=F": "livestock",
    "ZN=F": "rates", "ZB=F": "rates", "TLT": "rates", "IEF": "rates",
    "NG=F": "energy", "HO=F": "energy", "RB=F": "energy",
    "HG=F": "ind-metal", "PL=F": "ind-metal", "PA=F": "ind-metal",
    "DX=F": "dollar",
    "MTUM": "equity-mom", "QQQ": "equity", "SMH": "equity-semi", "XLE": "equity-energy",
}


def fetch(ticker):
    df = yf.download(ticker, period="max", interval="1d", progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df["spread"] = df["close"] * COST_FRAC
    df.index = pd.to_datetime(df.index)
    return df


def main():
    code = STRAT.read_text(encoding="utf-8")
    ns = {"np": np, "pd": pd}; exec(code, ns, ns); on_bar = ns["on_bar"]
    import re
    params = {n: {"int": int, "float": float, "bool": lambda x: x.lower() == "true"}.get(t, str)(d)
              for n, t, d, _ in re.findall(r"# @param (\w+) (\w+) (\S+) (.+)", code)}

    # existing factors for correlation
    ref = yf.download(["GC=F", "BTC-USD"], period="10y", interval="1d", progress=False, auto_adjust=False)["Close"]
    ref.columns = [str(c) for c in ref.columns]
    ref_ret = ref.pct_change()

    print(f"\n{'TICKER':<8}{'class':<12}{'n':>5}{'PF':>7}{'OOS':>6}{'perm':>6}{'cGOLD':>7}{'cBTC':>7}  verdict")
    print("-" * 70)
    winners = []
    for tk, cls in CANDIDATES.items():
        df = fetch(tk)
        if df is None or len(df) < 400:
            print(f"{tk:<8}{cls:<12} (insufficient history)"); continue
        trades, _ = vs.backtest(on_bar, df, params, point=1.0, slip_points=0.0, cost_mult=1.0, risk_pct=0.01)
        m = vs.metrics(trades)
        if m.get("trades", 0) == 0:
            print(f"{tk:<8}{cls:<12}  no trades"); continue
        oos = vs.metrics([t for t in trades if pd.Timestamp(t[0]).year >= 2023])
        pt = vs.permutation_test(trades) or {}
        beats = pt.get("beats_permutation", False)
        # correlation of daily returns to gold & BTC
        cr = pd.Series(df["close"].values, index=df.index).pct_change()
        aligned = pd.concat([cr, ref_ret], axis=1, join="inner").dropna()
        cg = aligned.iloc[:, 0].corr(aligned["GC=F"]) if "GC=F" in aligned else float("nan")
        cb = aligned.iloc[:, 0].corr(aligned["BTC-USD"]) if "BTC-USD" in aligned else float("nan")
        new_factor = (beats and oos.get("profit_factor", 0) >= 1.3 and m["trades"] >= 60
                      and abs(cg) < 0.35 and abs(cb) < 0.35)
        verdict = "NEW FACTOR" if new_factor else ("edge (corr)" if beats else "-")
        if new_factor:
            winners.append((tk, cls))
        print(f"{tk:<8}{cls:<12}{m['trades']:>5}{m['profit_factor']:>7.2f}{oos.get('profit_factor',0):>6.2f}"
              f"{'  y' if beats else '  n':>6}{cg:>7.2f}{cb:>7.2f}  {verdict}")

    print(f"\nGenuine NEW factors (edge + uncorrelated to gold & BTC): {winners or 'none'}")


if __name__ == "__main__":
    main()
