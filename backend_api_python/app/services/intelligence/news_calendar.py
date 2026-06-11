"""
News / event awareness (LLM-free) — ported from the traidind_kwen intelligence layer.

Two grounded, free overlays that tell the system *when not to trade*:

  * EconomicCalendar  — blackout windows around high-impact scheduled events
                        (NFP, CPI, FOMC, ECB, BOE, BOJ, RBA). Deterministic recurring
                        events are computed locally (no network); precise dates for
                        irregular events (FOMC/ECB/...) are best-effort fetched from the
                        free FinancialModelingPrep calendar and merged.
  * BreakingNewsMonitor — scans free RSS feeds (FXStreet/Investing.com/CNBC) for a
                        curated keyword set (war, tariff, rate hike/cut, crash, ...) to
                        flag a market-moving SHOCK in progress. Keyword-only — NO LLM.

Both fail OPEN (if the network is down, they never block trading), and only ever block
NEW ENTRIES — never exits. This is downside protection, not a direction signal.
"""
from __future__ import annotations
import calendar as _cal
import re
import time
from datetime import datetime, timedelta, timezone

try:
    import requests  # optional; modules fail-open if missing
except Exception:
    requests = None


# ---------------------------------------------------------------- symbol -> currencies
_FX = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"}


def symbol_currencies(symbol: str) -> list[str]:
    """Currencies a symbol is exposed to (for matching scheduled events)."""
    s = (symbol or "").upper().replace("/", "").replace("-", "")
    # metals, crypto, US indices -> USD driven (FOMC/CPI move them via the dollar / risk)
    if s.startswith(("XAU", "XAG", "BTC", "ETH", "US30", "US500", "NAS", "USTEC", "SPX")):
        return ["USD"]
    if len(s) >= 6 and s[:3] in _FX and s[3:6] in _FX:
        return [s[:3], s[3:6]]
    return ["USD"]


# ---------------------------------------------------------------- recurring schedule
# Deterministic, locally-computable high-impact events (UTC).
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    """nth (1-based) `weekday` (Mon=0) of month, as a UTC date at 00:00."""
    cnt = 0
    for day in range(1, _cal.monthrange(year, month)[1] + 1):
        d = datetime(year, month, day, tzinfo=timezone.utc)
        if d.weekday() == weekday:
            cnt += 1
            if cnt == n:
                return d
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _recurring_events(now: datetime) -> list[tuple[str, str, datetime, str]]:
    """Generate this-month + next-month recurring events (name, currency, dt_utc, impact)."""
    out = []
    for off in (0, 1):
        m = now.month - 1 + off
        y = now.year + m // 12
        mo = m % 12 + 1
        nfp = _nth_weekday(y, mo, 4, 1).replace(hour=12, minute=30)   # 1st Friday 12:30 UTC
        cpi = datetime(y, mo, 13, 12, 30, tzinfo=timezone.utc)        # ~13th 12:30 UTC
        rba = _nth_weekday(y, mo, 1, 1).replace(hour=3, minute=30)    # 1st Tuesday 03:30 UTC (AUD)
        out += [("NFP", "USD", nfp, "high"), ("CPI", "USD", cpi, "high"), ("RBA Rate", "AUD", rba, "high")]
    return out


_BLACKOUT = {"high": (60, 120), "medium": (15, 30), "low": (5, 10)}  # (minutes before, after)


class EconomicCalendar:
    def __init__(self, fmp_enabled: bool = True, refresh_sec: int = 3600,
                 mode: str = "block", spike_block_min: int = 15):
        self._fmp = fmp_enabled and requests is not None
        self._refresh_sec = refresh_sec
        # mode 'block'        -> block the full [before, after] window (default protection).
        # mode 'continuation' -> block only the pre-event + violent spike
        #                        [before, spike_block_min], then ALLOW entries so a
        #                        trend strategy can ride the established post-news move.
        self._mode = str(mode or "block").lower()
        self._spike_block_min = int(spike_block_min)
        self._fetched: list[tuple[str, str, datetime, str]] = []
        self._last_fetch = 0.0

    def refresh(self, now: datetime | None = None) -> None:
        """Best-effort merge of precise FMP calendar dates (FOMC/ECB/...). Fail-open."""
        if not self._fmp or (time.time() - self._last_fetch) < self._refresh_sec:
            return
        self._last_fetch = time.time()
        try:
            now = now or datetime.now(timezone.utc)
            frm = now.date().isoformat()
            to = (now + timedelta(days=10)).date().isoformat()
            r = requests.get("https://financialmodelingprep.com/api/v3/economic_calendar",
                             params={"from": frm, "to": to}, timeout=6)
            evs = []
            for e in (r.json() or []):
                if str(e.get("impact", "")).lower() not in ("high",):
                    continue
                cur = str(e.get("currency", "")).upper()
                if cur not in _FX:
                    continue
                try:
                    dt = datetime.fromisoformat(str(e.get("date")).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                evs.append((str(e.get("event", "event")), cur, dt, "high"))
            self._fetched = evs
        except Exception:
            pass  # fail-open

    def should_trade(self, symbol: str, now: datetime | None = None) -> tuple[bool, str]:
        now = now or datetime.now(timezone.utc)
        self.refresh(now)
        curs = set(symbol_currencies(symbol))
        events = _recurring_events(now) + self._fetched
        for name, cur, dt, impact in events:
            if cur not in curs:
                continue
            before, after = _BLACKOUT.get(impact, (60, 120))
            if self._mode == "continuation":
                after = min(after, self._spike_block_min)  # block only the spike, then ride
            if dt - timedelta(minutes=before) <= now <= dt + timedelta(minutes=after):
                when = "upcoming" if now < dt else "settling"
                return False, f"news blackout: {name} ({cur}) {when} [{before}m/{after}m window]"
        return True, ""


# ---------------------------------------------------------------- breaking-news shock
_RSS_FEEDS = [
    "https://www.fxstreet.com/rss/news",
    "https://www.investing.com/rss/news_14.rss",   # economy / geopolitical
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
]
_HIGH_KEYWORDS = [
    "war", "ceasefire", "nuclear", "invasion", "sanctions", "embargo", "missile", "attack",
    "tariff", "trade war", "default", "crash", "collapse",
    "rate cut", "rate hike", "hawkish", "dovish", "fomc", "emergency", "powell", "lagarde",
]
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)


class BreakingNewsMonitor:
    def __init__(self, refresh_sec: int = 300):
        self._refresh_sec = refresh_sec
        self._last_fetch = 0.0
        self._shock = (False, "")

    def refresh(self) -> None:
        """Fetch RSS titles, keyword-scan for a market-moving shock. Fail-open, TTL-gated."""
        if requests is None or (time.time() - self._last_fetch) < self._refresh_sec:
            return
        self._last_fetch = time.time()
        titles = []
        for url in _RSS_FEEDS:
            try:
                r = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
                for m in _TITLE_RE.findall(r.text)[:30]:
                    t = re.sub(r"<.*?>", "", m).strip()
                    if t:
                        titles.append(t.lower())
            except Exception:
                continue
        hits = [kw for kw in _HIGH_KEYWORDS for t in titles if kw in t]
        if hits:
            uniq = sorted(set(hits))[:4]
            self._shock = (True, f"breaking-news shock: {', '.join(uniq)}")
        else:
            self._shock = (False, "")

    def is_shock_active(self) -> tuple[bool, str]:
        return self._shock


# ---------------------------------------------------------------- combined guard
class NewsGuard:
    """Combined news/event entry gate. allow_new_entry() returns (allowed, reason)."""

    def __init__(self, fmp_enabled: bool = True, breaking_news: bool = True,
                 mode: str = "block", spike_block_min: int = 15):
        self.calendar = EconomicCalendar(fmp_enabled=fmp_enabled, mode=mode,
                                         spike_block_min=spike_block_min)
        self.breaking = BreakingNewsMonitor() if breaking_news else None

    def refresh(self, now: datetime | None = None) -> None:
        self.calendar.refresh(now)
        if self.breaking is not None:
            self.breaking.refresh()

    def allow_new_entry(self, symbol: str, now: datetime | None = None) -> tuple[bool, str]:
        ok, reason = self.calendar.should_trade(symbol, now)
        if not ok:
            return False, reason
        if self.breaking is not None:
            shock, sreason = self.breaking.is_shock_active()
            if shock:
                return False, sreason
        return True, ""
