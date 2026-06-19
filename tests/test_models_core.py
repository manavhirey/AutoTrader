import dataclasses
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from orb_bot import models

ET = ZoneInfo("America/New_York")


def test_direction_enum_values():
    assert models.Direction.LONG.value == "LONG"
    assert models.Direction.SHORT.value == "SHORT"
    assert [d.name for d in models.Direction] == ["LONG", "SHORT"]


def test_model_enum_members():
    assert [m.name for m in models.Model] == ["BREAKOUT", "RETEST", "REVERSAL"]


def test_state_enum_members():
    assert [s.name for s in models.State] == [
        "IDLE",
        "BUILDING_RANGE",
        "RANGE_SET",
        "WAIT_CONFIRMATION",
        "WAIT_ENTRY",
        "IN_TRADE",
        "DONE",
    ]


def test_candle_construction_defaults():
    ts_open = datetime(2026, 6, 19, 9, 30, tzinfo=ET)
    ts_close = datetime(2026, 6, 19, 9, 45, tzinfo=ET)
    c = models.Candle(
        ts_open=ts_open,
        ts_close=ts_close,
        open=Decimal("100.00"),
        high=Decimal("101.50"),
        low=Decimal("99.75"),
        close=Decimal("101.00"),
        volume=12345,
        timeframe_min=15,
    )
    assert c.ts_open.tzinfo == ET
    assert isinstance(c.high, Decimal)
    assert c.volume == 12345
    assert c.timeframe_min == 15
    assert c.data_incomplete is False
    assert c.bars_present is None


def test_candle_is_frozen():
    c = models.Candle(
        ts_open=datetime(2026, 6, 19, 9, 30, tzinfo=ET),
        ts_close=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        volume=1,
        timeframe_min=15,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.close = Decimal("9")  # type: ignore[misc]


def _candle(close: str = "100") -> models.Candle:
    return models.Candle(
        ts_open=datetime(2026, 6, 19, 9, 30, tzinfo=ET),
        ts_close=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=1,
        timeframe_min=15,
    )


def test_opening_range_construction_and_frozen():
    orng = models.OpeningRange(
        high=Decimal("101.50"),
        low=Decimal("99.75"),
        established_at=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        width=Decimal("1.75"),
        feed="IEX",
        bars_present=15,
        low_confidence=False,
    )
    assert isinstance(orng.width, Decimal)
    assert orng.feed == "IEX"
    assert orng.bars_present == 15
    assert orng.low_confidence is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        orng.high = Decimal("200")  # type: ignore[misc]


def test_setup_construction_and_frozen():
    s = models.Setup(
        direction=models.Direction.LONG,
        model=models.Model.BREAKOUT,
        entry=Decimal("101.50"),
        stop=Decimal("100.00"),
        target=Decimal("104.50"),
        rr=2.0,
        reason=["close above OR high", "strong body"],
    )
    assert s.direction is models.Direction.LONG
    assert s.model is models.Model.BREAKOUT
    assert isinstance(s.entry, Decimal)
    assert s.rr == 2.0
    assert s.reason == ["close above OR high", "strong body"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.entry = Decimal("0")  # type: ignore[misc]


def test_displacement_construction_and_frozen():
    up = _candle("102")
    lo = _candle("98")
    d = models.Displacement(
        type="IMPULSE",
        upper_candle=up,
        lower_candle=lo,
        size=Decimal("4.0"),
    )
    assert d.type == "IMPULSE"
    assert d.upper_candle is up
    assert d.lower_candle is lo
    assert isinstance(d.size, Decimal)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.size = Decimal("0")  # type: ignore[misc]
