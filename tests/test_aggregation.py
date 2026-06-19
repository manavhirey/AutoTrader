from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot.aggregation import Aggregator, bucket_start
from orb_bot.models import Candle as Candle_t

ET = ZoneInfo("America/New_York")


def _dt(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 6, 19, h, m, s, tzinfo=ET)


def _c1m(h: int, m: int, o: str, hi: str, lo: str, cl: str, vol: int = 100) -> Candle_t:
    ts_open = _dt(h, m, 0)
    return Candle_t(
        ts_open=ts_open,
        ts_close=ts_open + timedelta(minutes=1),
        open=Decimal(o),
        high=Decimal(hi),
        low=Decimal(lo),
        close=Decimal(cl),
        volume=vol,
        timeframe_min=1,
    )


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


def test_add_buffers_until_bucket_advances():
    agg = Aggregator(15)
    # 09:30..09:44 -> all None (still filling first bucket)
    out = [agg.add(_c1m(9, mm, "10", "11", "9", "10")) for mm in range(30, 45)]
    assert out == [None] * 15
    # 09:45 belongs to the next bucket -> emits the 09:30 aggregate
    emitted = agg.add(_c1m(9, 45, "20", "21", "19", "20"))
    assert emitted is not None
    assert emitted.timeframe_min == 15
    assert emitted.ts_open == _dt(9, 30, 0)
    assert emitted.ts_close == _dt(9, 45, 0)
    assert emitted.bars_present == 15
    assert emitted.data_incomplete is False


def test_add_ohlc_merge_rules():
    agg = Aggregator(15)
    agg.add(_c1m(9, 30, "10", "12", "8", "11"))   # first open = 10
    agg.add(_c1m(9, 31, "11", "15", "7", "9"))    # high 15, low 7
    agg.add(_c1m(9, 32, "9", "13", "9", "14"))    # last close (so far) = 14
    emitted = agg.add(_c1m(9, 45, "20", "20", "20", "20"))
    assert emitted.open == Decimal("10")
    assert emitted.close == Decimal("14")
    assert emitted.high == Decimal("15")
    assert emitted.low == Decimal("7")
    assert emitted.volume == 300
    assert emitted.bars_present == 3
    assert emitted.data_incomplete is True  # 3 < 15


def test_add_idempotent_on_duplicate_1m():
    agg = Aggregator(15)
    agg.add(_c1m(9, 30, "10", "12", "8", "11", vol=100))
    agg.add(_c1m(9, 30, "10", "12", "8", "11", vol=100))  # duplicate ts_open -> ignored
    agg.add(_c1m(9, 31, "11", "11", "11", "11", vol=50))
    emitted = agg.add(_c1m(9, 45, "20", "20", "20", "20"))
    assert emitted.bars_present == 2          # duplicate not double-counted
    assert emitted.volume == 150              # 100 + 50, not 250


def test_add_passthrough_1m_timeframe():
    agg = Aggregator(1)
    assert agg.add(_c1m(9, 30, "10", "11", "9", "10")) is None      # buffers first
    emitted = agg.add(_c1m(9, 31, "11", "12", "10", "11"))          # advance -> emit 09:30
    assert emitted.timeframe_min == 1
    assert emitted.ts_open == _dt(9, 30, 0)
    assert emitted.bars_present == 1
    assert emitted.data_incomplete is False
