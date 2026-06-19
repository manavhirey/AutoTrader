from datetime import datetime, time
from zoneinfo import ZoneInfo

from orb_bot.aggregation import bucket_start

ET = ZoneInfo("America/New_York")


def _dt(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 6, 19, h, m, s, tzinfo=ET)


def test_bucket_start_anchors_at_0930():
    # 09:30:00 -> 09:30; 09:44:59 still in first 15m bucket -> 09:30
    assert bucket_start(_dt(9, 30, 0), 15) == _dt(9, 30, 0)
    assert bucket_start(_dt(9, 44, 59), 15) == _dt(9, 30, 0)


def test_bucket_start_advances_per_timeframe():
    # 09:45 opens the second 15m bucket
    assert bucket_start(_dt(9, 45, 0), 15) == _dt(9, 45, 0)
    assert bucket_start(_dt(9, 59, 59), 15) == _dt(9, 45, 0)
    assert bucket_start(_dt(10, 0, 0), 15) == _dt(10, 0, 0)


def test_bucket_start_passthrough_1m():
    # tf_min=1 -> every minute is its own bucket
    assert bucket_start(_dt(9, 30, 0), 1) == _dt(9, 30, 0)
    assert bucket_start(_dt(9, 31, 30), 1) == _dt(9, 31, 0)


def test_bucket_start_before_anchor_floors_below_open():
    # 09:20 is 10 min before anchor -> floors to 09:15 (one 15m bucket below)
    assert bucket_start(_dt(9, 20, 0), 15) == _dt(9, 15, 0)


def test_bucket_start_custom_anchor():
    assert bucket_start(_dt(10, 7, 0), 15, anchor=time(10, 0)) == _dt(10, 0, 0)
