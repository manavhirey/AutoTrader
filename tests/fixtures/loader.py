"""CSV scenario loader for engine/aggregation tests and future backtest regressions.

Named scenarios live in ``tests/fixtures/candles/<name>.csv`` (spec §18). Each
CSV has the header ``ts_open,open,high,low,close,volume`` with ``ts_open`` as a
naive ET wall-clock string ``YYYY-MM-DD HH:MM``. Prices are parsed as Decimal,
timestamps localized to America/New_York, and ``ts_close = ts_open + tf``.
Lines starting with ``#`` and blank lines are ignored.
"""
import csv
import datetime as _dt
import os
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot.models import Candle

_CANDLES_DIR = os.path.join(os.path.dirname(__file__), "candles")

SCENARIOS = ("trend_long", "range_day", "weak_breakout", "retest_hold")


def scenario_path(name: str) -> str:
    """Absolute path to a named scenario CSV (raises if the name is unknown)."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; known: {SCENARIOS}")
    return os.path.join(_CANDLES_DIR, f"{name}.csv")


def _rows(path: str):
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            yield line


def load_scenario(
    name: str,
    *,
    timeframe_min: int = 15,
    tz: str = "America/New_York",
) -> list[Candle]:
    """Load a named scenario into a list of tz-aware ET ``Candle``s.

    ``timeframe_min`` sets each candle's ``timeframe_min`` and the
    ``ts_close = ts_open + timeframe_min`` derivation (default 15 = strategy T).
    """
    zone = ZoneInfo(tz)
    delta = _dt.timedelta(minutes=timeframe_min)

    lines = list(_rows(scenario_path(name)))
    if not lines:
        return []
    reader = csv.DictReader(lines)
    expected = {"ts_open", "open", "high", "low", "close", "volume"}
    missing = expected - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"{name}.csv missing columns: {sorted(missing)}")

    candles: list[Candle] = []
    for row in reader:
        ts_open = _dt.datetime.strptime(row["ts_open"], "%Y-%m-%d %H:%M").replace(
            tzinfo=zone
        )
        candles.append(
            Candle(
                ts_open=ts_open,
                ts_close=ts_open + delta,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=int(row["volume"]),
                timeframe_min=timeframe_min,
            )
        )
    return candles
