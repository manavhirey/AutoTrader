"""Validates the CSV scenario fixtures and their loader (spec §18)."""
from decimal import Decimal

import pytest

from .fixtures import loader


@pytest.mark.parametrize("name", loader.SCENARIOS)
def test_scenario_loads_nonempty(name):
    candles = loader.load_scenario(name)
    assert candles, f"{name} produced no candles"


@pytest.mark.parametrize("name", loader.SCENARIOS)
def test_candle_types_and_invariants(name):
    candles = loader.load_scenario(name, timeframe_min=15)
    for c in candles:
        # Decimal prices.
        assert isinstance(c.open, Decimal)
        assert isinstance(c.high, Decimal)
        assert isinstance(c.low, Decimal)
        assert isinstance(c.close, Decimal)
        assert isinstance(c.volume, int)
        # tz-aware ET timestamps.
        assert c.ts_open.tzinfo is not None
        assert c.ts_close.tzinfo is not None
        assert "New_York" in str(c.ts_open.tzinfo)
        # ts_close = ts_open + timeframe.
        assert (c.ts_close - c.ts_open).total_seconds() == 15 * 60
        assert c.timeframe_min == 15
        # OHLC self-consistency.
        assert c.low <= c.open <= c.high
        assert c.low <= c.close <= c.high
        assert c.high >= c.low


@pytest.mark.parametrize("name", loader.SCENARIOS)
def test_first_candle_is_the_opening_range_bar(name):
    candles = loader.load_scenario(name)
    first = candles[0]
    assert (first.ts_open.hour, first.ts_open.minute) == (9, 30), (
        f"{name}: first candle must open at 09:30 (the opening range bar)"
    )


@pytest.mark.parametrize("name", loader.SCENARIOS)
def test_candles_are_contiguous_15m(name):
    candles = loader.load_scenario(name)
    for prev, nxt in zip(candles, candles[1:], strict=False):
        assert nxt.ts_open == prev.ts_close, (
            f"{name}: gap between {prev.ts_open} and {nxt.ts_open}"
        )


def test_timeframe_min_override_changes_spacing():
    c5 = loader.load_scenario("trend_long", timeframe_min=5)
    assert all(x.timeframe_min == 5 for x in c5)
    assert (c5[0].ts_close - c5[0].ts_open).total_seconds() == 5 * 60


def test_unknown_scenario_rejected():
    with pytest.raises(ValueError):
        loader.scenario_path("does_not_exist")
