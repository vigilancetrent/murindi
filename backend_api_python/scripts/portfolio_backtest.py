#!/usr/bin/env python3
"""Vol-targeted multi-sleeve portfolio backtest + A/B test.

Core basket = the instruments where trend-following BEATS the permutation test
(gold + BTC). Silver is offered as an optional satellite (borderline). Each sleeve
trades the SAME ScriptStrategy; trades compound on one shared equity. We A/B:
  single vs portfolio, and flat vs volatility-targeted sizing.
"""
import importlib.util, math, pathlib
import numpy as np, pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
vs = importlib.util.module_from_spec(importlib.util.spec_from_file_location("vs", HERE / "validate_strategy.py"))
importlib.util.spec_from_file_location("vs", HERE / "validate_strategy.py").loader.exec_module(vs)
import sys; sys.path.insert(0, str(HERE.parent))
from app.services.risk.vol_target import realized_vol, vol_target_multiplier

DATA = pathlib.Path(r"C:\Users\Hp\Downloads\intavidoetrading\data")
STRAT = HERE.parent / "strategies" / "trend_following_donchian.py"
BASKET = {  # sym -> (point, slip, crypto)
    "XAUUSD": (0.01, 2, False), "BTCUSD": (0.01, 50, True), "XAGUSD": (0.001, 2, False),
}


def gen_trades(on_bar, df, params, point, slip, crypto, ppy=None):
    """Per-symbol trades as dicts: entry_time, exit_time, R, vol_mult."""
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float); spr = df["spread"].to_numpy(float); t = df.index.to_numpy()
    n = len(df)
    ctx = vs._Ctx(params); ctx._o, ctx._h, ctx._l, ctx._c, ctx._v, ctx._t = o, h, l, c, v, t
    atr_n = int(params.get("atr_period", 14)); atr_mult = float(params.get("atr_mult", 2.0))
    warmup = max(int(params.get("donchian_entry", 20)), atr_n, int(params.get("donchian_exit", 10))) + 3
    ppy = ppy if ppy else 6 * (365 if crypto else 252)
    pos = 0; entry = 0.0; size_dummy = 0.0; risk_d = 0.0; ent_t = None; vmult = 1.0
    out = []
    for i in range(warmup, n - 1):
        ctx.current_index = i; ctx.equity = 10000.0
        ctx.position.direction = pos; ctx.position.entry_price = entry; ctx.position.size = 0.0
        ctx._orders = []
        on_bar(ctx, vs._Bar(o[i], h[i], l[i], c[i], v[i], t[i]))
        cps = (spr[i] * point + slip * point)
        for action, _ in ctx._orders:
            if action == "close" and pos != 0:
                exit_px = o[i + 1] - pos * cps
                R = (exit_px - entry) * pos / risk_d if risk_d else 0.0
                out.append({"entry_time": ent_t, "exit_time": t[i + 1], "R": R, "vol_mult": vmult})
                pos = 0
            elif action in ("buy", "sell") and pos == 0:
                d = 1 if action == "buy" else -1
                atr = vs._atr_at(h, l, c, i, atr_n); sd = atr_mult * atr
                if sd <= 0:
                    continue
                entry = o[i + 1] + d * cps; risk_d = sd; pos = d; ent_t = t[i + 1]
                rv = realized_vol(c[max(0, i - 120):i + 1].tolist(), 100, ppy)
                vmult = vol_target_multiplier(rv, target_annual=0.15)
    return out


def portfolio(streams, syms, base_risk=0.005, vol_target=False, start=10000.0):
    trades = []
    for s in syms:
        for tr in streams[s]:
            trades.append((pd.Timestamp(tr["exit_time"]), tr["R"], tr["vol_mult"], s))
    trades.sort(key=lambda x: x[0])
    eq = start; curve = []
    per_sleeve = {s: [] for s in syms}
    for ts, R, vm, s in trades:
        risk = base_risk * (vm if vol_target else 1.0)
        pnl = R * risk * eq
        eq += pnl
        curve.append((ts, eq)); per_sleeve[s].append((ts, pnl))
    return curve, per_sleeve, len(trades)


def stats(curve):
    if len(curve) < 3:
        return {}
    df = pd.DataFrame(curve, columns=["t", "eq"]).set_index("t")
    daily = df["eq"].resample("1D").last().ffill().dropna()
    rets = daily.pct_change().dropna()
    years = (daily.index[-1] - daily.index[0]).days / 365.25
    sharpe = rets.mean() / rets.std() * math.sqrt(252) if rets.std() > 0 else 0
    peak = daily.cummax(); dd = ((peak - daily) / peak).max()
    cagr = (daily.iloc[-1] / daily.iloc[0]) ** (1 / max(years, 1e-9)) - 1
    return {"final": daily.iloc[-1], "cagr": cagr, "sharpe": sharpe, "maxDD": dd, "years": years}


def make_tearsheet(streams, out_path):
    """Render a professional multi-panel tear-sheet PNG for the recommended portfolio."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    INK, PANEL, PAPER = "#0E1116", "#171B22", "#F5F6F7"
    TEAL, AMBER, MUTED, RED, GRID = "#1FA697", "#E0A33E", "#8A929A", "#E0556E", "#2A2F38"
    SLEEVE = {"XAUUSD": AMBER, "BTCUSD": TEAL, "XAGUSD": MUTED, "ETHUSD": "#5BD1C0"}

    full = [s for s in ("XAUUSD", "BTCUSD", "XAGUSD", "ETHUSD") if s in streams]
    gb = [s for s in ("XAUUSD", "BTCUSD") if s in streams]
    curve_vt, per_vt, _ = portfolio(streams, full, base_risk=0.005, vol_target=True)
    curve_flat, _, _ = portfolio(streams, gb, base_risk=0.005, vol_target=False)
    curve_gold, _, _ = portfolio(streams, ["XAUUSD"], base_risk=0.005, vol_target=False)

    def daily(curve):
        return pd.DataFrame(curve, columns=["t", "eq"]).set_index("t")["eq"].resample("1D").last().ffill().dropna()

    eq_vt, eq_flat, eq_gold = daily(curve_vt), daily(curve_flat), daily(curve_gold)
    m = stats(curve_vt)
    peak = eq_vt.cummax(); dd = (eq_vt - peak) / peak * 100.0

    def style(ax, title=None):
        ax.set_facecolor(PANEL)
        for sp in ax.spines.values():
            sp.set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=9)
        ax.grid(True, color=GRID, lw=0.6, alpha=0.6)
        if title:
            ax.set_title(title, color=PAPER, fontsize=12, fontweight="bold", loc="left", pad=8)

    fig = plt.figure(figsize=(12, 14), facecolor=INK)
    gs = GridSpec(4, 1, height_ratios=[3, 1.5, 2, 1.5], hspace=0.5)

    ax0 = fig.add_subplot(gs[0]); style(ax0, "Murindi Trend Portfolio — Equity Curve (2019–2026, 0.5% risk/trade)")
    ax0.plot(eq_vt.index, eq_vt.values, color=TEAL, lw=2.2, label=f"{'+'.join(full)} (vol-target)")
    ax0.plot(eq_flat.index, eq_flat.values, color=AMBER, lw=1.3, alpha=0.85, label="Gold+BTC (flat)")
    ax0.plot(eq_gold.index, eq_gold.values, color=MUTED, lw=1.0, alpha=0.6, label="Gold only (flat)")
    ax0.set_yscale("log"); ax0.set_ylabel("equity ($, log)", color=MUTED, fontsize=9)
    leg = ax0.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=PAPER, fontsize=9, loc="upper left")

    ax1 = fig.add_subplot(gs[1]); style(ax1, "Drawdown (underwater)")
    ax1.fill_between(dd.index, dd.values, 0, color=RED, alpha=0.35)
    ax1.plot(dd.index, dd.values, color=RED, lw=0.9)
    ax1.set_ylabel("drawdown %", color=MUTED, fontsize=9)

    ax2 = fig.add_subplot(gs[2]); style(ax2, "Per-sleeve cumulative P&L (diversification)")
    for s in full:
        rows = per_vt.get(s, [])
        if rows:
            ser = pd.DataFrame(rows, columns=["t", "p"]).set_index("t")["p"].cumsum()
            ax2.plot(ser.index, ser.values, color=SLEEVE.get(s, PAPER), lw=1.6, label=s)
    ax2.axhline(0, color=GRID, lw=0.8)
    ax2.set_ylabel("cumulative $", color=MUTED, fontsize=9)
    ax2.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=PAPER, fontsize=9, loc="upper left")

    ax3 = fig.add_subplot(gs[3]); ax3.set_facecolor(INK); ax3.axis("off")
    dailies = {s: pd.DataFrame(r, columns=["t", "p"]).set_index("t")["p"].resample("1D").sum()
               for s, r in per_vt.items() if r}
    corr = pd.DataFrame(dailies).fillna(0).corr() if len(dailies) > 1 else None
    lines = [
        f"FINAL EQUITY   ${m.get('final',0):,.0f}        CAGR  {m.get('cagr',0)*100:5.1f}%",
        f"SHARPE         {m.get('sharpe',0):+.2f}            MAX DD  {m.get('maxDD',0)*100:4.1f}%",
        f"SLEEVES        {', '.join(full)}    risk 0.5%/trade   vol-target 15%",
    ]
    if corr is not None:
        lines.append("")
        lines.append("SLEEVE DAILY-PnL CORRELATION (near-zero = real diversification):")
        hdr = "            " + "".join(f"{c:>9}" for c in corr.columns)
        lines.append(hdr)
        for r in corr.index:
            lines.append(f"  {r:<9}" + "".join(f"{corr.loc[r,c]:>9.2f}" for c in corr.columns))
    lines += ["", "Trend-following + vol-targeting + diversification. Modest, regime-dependent edge —",
              "not a money printer. Paper-trade before capital. No guarantees."]
    ax3.text(0.01, 0.98, "\n".join(lines), color=PAPER, fontsize=10.5, family="monospace",
             va="top", ha="left", transform=ax3.transAxes)

    fig.text(0.5, 0.005, "Murindi — backtest, not live. Re-validate on your real spreads.",
             color=MUTED, fontsize=8, ha="center")
    out_path = pathlib.Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130, facecolor=INK, bbox_inches="tight")
    plt.close(fig)
    print(f"\ntear-sheet -> {out_path}")


def main():
    code = STRAT.read_text(encoding="utf-8"); ns = {"np": np, "pd": pd}; exec(code, ns, ns)
    on_bar = ns["on_bar"]; import re
    params = {n_: {"int": int, "float": float, "bool": lambda x: x.lower() == "true"}.get(ty, str)(d)
              for n_, ty, d, _ in re.findall(r"# @param (\w+) (\w+) (\S+) (.+)", code)}

    streams = {}
    for sym, (point, slip, crypto) in BASKET.items():
        f = DATA / f"{sym}_M1.pkl"
        if not f.exists():
            continue
        df = vs.load_resample(str(f), "4h")
        streams[sym] = gen_trades(on_bar, df, params, point, slip, crypto)
        print(f"  {sym}: {len(streams[sym])} trades")

    # 4th-sleeve candidate: ETH (daily, via yfinance). Confirmed trend edge; correlates
    # ~0.84 with BTC, so it's a 2nd crypto sleeve -> balances the book to ~50/50 metals/crypto.
    try:
        import yfinance as yf
        raw = yf.download("ETH-USD", period="max", interval="1d", progress=False, auto_adjust=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0] for c in raw.columns]
        raw = raw.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        edf = raw[["open", "high", "low", "close", "volume"]].dropna()
        edf["spread"] = edf["close"] * 0.0005
        edf.index = pd.to_datetime(edf.index)
        streams["ETHUSD"] = gen_trades(on_bar, edf, params, 1.0, 0.0, True, ppy=365)
        print(f"  ETHUSD: {len(streams['ETHUSD'])} trades (daily)")
    except Exception as e:
        print("  ETHUSD fetch skipped:", e)

    print(f"\n{'CONFIG':<38}{'final$':>10}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}")
    print("-" * 68)
    configs = [
        ("Gold only (flat)", ["XAUUSD"], False),
        ("Gold only (vol-target)", ["XAUUSD"], True),
        ("Gold+BTC (flat)", ["XAUUSD", "BTCUSD"], False),
        ("Gold+BTC (vol-target)", ["XAUUSD", "BTCUSD"], True),
        ("Gold+BTC+Silver (vol-target)", ["XAUUSD", "BTCUSD", "XAGUSD"], True),
        ("Gold+BTC+Silver+ETH (vol-target)", ["XAUUSD", "BTCUSD", "XAGUSD", "ETHUSD"], True),
    ]
    for label, syms, vt in configs:
        syms = [s for s in syms if s in streams]
        curve, per, n = portfolio(streams, syms, base_risk=0.005, vol_target=vt)
        m = stats(curve)
        print(f"{label:<38}{m.get('final',0):>10,.0f}{m.get('cagr',0)*100:>7.1f}%"
              f"{m.get('sharpe',0):>+8.2f}{m.get('maxDD',0)*100:>7.1f}%")

    # sleeve correlation (daily PnL) for the full basket
    curve, per, _ = portfolio(streams, list(streams.keys()), vol_target=True)
    dailies = {}
    for s, rows in per.items():
        if rows:
            dser = pd.DataFrame(rows, columns=["t", "p"]).set_index("t")["p"].resample("1D").sum()
            dailies[s] = dser
    if len(dailies) > 1:
        corr = pd.DataFrame(dailies).fillna(0).corr()
        print("\nSleeve daily-PnL correlation:")
        print(corr.round(2).to_string())

    make_tearsheet(streams, HERE.parent / "reports" / "portfolio_tearsheet.png")


if __name__ == "__main__":
    main()
