"""
Portfolio kill-switch — halts trading when risk limits are breached.

Strategy- and broker-agnostic. The live executor (or a paper-trading loop) feeds it
equity updates and closed-trade R-multiples, then asks ``status()`` before placing any
new order. If a limit is breached and ``hard_halt`` is on, ``trading_allowed`` is False
and the executor must skip new entries (existing positions can still be managed/closed).

Limits:
  * max_drawdown_pct  — peak-to-trough equity drawdown (hard stop on the whole book)
  * daily_loss_pct    — loss from the day's starting equity (resets each new day)
  * min_expectancy_R  — rolling mean R over the last `expectancy_window` trades
                        (catches a *decaying edge* before it bleeds the account)

This is defence-in-depth on top of each strategy's own stop. It does NOT predict —
it just enforces discipline the way a professional risk desk would.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class KillSwitchConfig:
    max_drawdown_pct: float = 0.20      # halt if book drawdown exceeds 20%
    daily_loss_pct: float = 0.05        # halt for the day if down 5% from day open
    min_expectancy_R: float = 0.0       # halt if rolling expectancy turns negative.
                                        # Units depend on what is fed to record_trade():
                                        #   * backtest harness feeds R-multiples;
                                        #   * the live executor feeds per-trade NET return
                                        #     (PnL / equity). Either way, the default 0.0
                                        #     means "halt if net-losing on average". Keep
                                        #     the threshold at/near 0 for decay detection.
    expectancy_window: int = 40         # trades in the rolling expectancy window
    hard_halt: bool = True              # True = block entries; False = alert-only


class KillSwitch:
    def __init__(self, config: Optional[KillSwitchConfig] = None):
        self.cfg = config or KillSwitchConfig()
        self.peak_equity: float = 0.0
        self.equity: float = 0.0
        self.day_start_equity: float = 0.0
        self._day = None
        self._recent_R = deque(maxlen=self.cfg.expectancy_window)
        self.manual_halt = False

    # ------------------------------------------------------------------ feeds
    def update_equity(self, equity: float, when: Any = None) -> None:
        equity = float(equity)
        self.equity = equity
        if self.peak_equity <= 0:
            self.peak_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        day = getattr(when, "date", lambda: when)() if when is not None else self._day
        if day != self._day:
            self._day = day
            self.day_start_equity = equity

    def record_trade(self, r_multiple: float) -> None:
        self._recent_R.append(float(r_multiple))

    def halt(self) -> None:
        self.manual_halt = True

    def resume(self) -> None:
        self.manual_halt = False

    # ------------------------------------------------------------------ metrics
    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    @property
    def daily_pnl_pct(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return (self.equity - self.day_start_equity) / self.day_start_equity

    @property
    def rolling_expectancy(self) -> Optional[float]:
        if len(self._recent_R) < self._recent_R.maxlen:
            return None  # not enough trades yet to judge
        return sum(self._recent_R) / len(self._recent_R)

    # ------------------------------------------------------------------ verdict
    def status(self) -> dict:
        breaches = []
        if self.manual_halt:
            breaches.append("manual_halt")
        if self.drawdown >= self.cfg.max_drawdown_pct:
            breaches.append(f"max_drawdown {self.drawdown:.1%} >= {self.cfg.max_drawdown_pct:.0%}")
        if self.daily_pnl_pct <= -self.cfg.daily_loss_pct:
            breaches.append(f"daily_loss {self.daily_pnl_pct:.1%} <= -{self.cfg.daily_loss_pct:.0%}")
        exp = self.rolling_expectancy
        if exp is not None and exp < self.cfg.min_expectancy_R:
            breaches.append(f"expectancy_decay {exp:+.3f}R < {self.cfg.min_expectancy_R:+.3f}R")
        allowed = not (breaches and (self.cfg.hard_halt or self.manual_halt))
        return {
            "trading_allowed": allowed,
            "breaches": breaches,
            "drawdown": round(self.drawdown, 4),
            "daily_pnl_pct": round(self.daily_pnl_pct, 4),
            "rolling_expectancy": (round(exp, 4) if exp is not None else None),
            "equity": self.equity,
            "peak_equity": self.peak_equity,
        }

    def allow_new_entry(self) -> bool:
        return self.status()["trading_allowed"]
