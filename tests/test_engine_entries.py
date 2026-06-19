from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot import engine as eng
from orb_bot import indicators  # noqa: F401
from orb_bot.config import StrategyConfig
from orb_bot.models import Candle, Direction, Model, OpeningRange, Setup, State

from .context import orb_bot

ET = ZoneInfo("America/New_York")


def _cfg(**over):
    return StrategyConfig(**over)


def _candle(o, h, lo, c, ts_open, tf=15, bars=15):
    return Candle(
        ts_open=ts_open,
        ts_close=ts_open + timedelta(minutes=tf),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(lo)), close=Decimal(str(c)),
        volume=1000, timeframe_min=tf, bars_present=bars,
    )


def test_project_target_long():
    tgt = eng.project_target(Decimal("100"), Decimal("99"), Direction.LONG, 2.0)
    assert tgt == Decimal("102")


def test_project_target_short():
    tgt = eng.project_target(Decimal("100"), Decimal("101"), Direction.SHORT, 2.0)
    assert tgt == Decimal("98")


def _setup(direction, entry, stop, target, rr, model=Model.BREAKOUT):
    return Setup(
        direction=direction, model=model,
        entry=Decimal(str(entry)), stop=Decimal(str(stop)),
        target=Decimal(str(target)), rr=rr, reason=["x"],
    )


def test_validate_setup_valid_long():
    s = _setup(Direction.LONG, 100, 99, 102, 2.0)
    assert eng.validate_setup(s, _cfg()) == []


def test_validate_setup_valid_short():
    s = _setup(Direction.SHORT, 100, 101, 98, 2.0)
    assert eng.validate_setup(s, _cfg()) == []


def test_validate_setup_inverted_long_stop():
    # LONG stop must be below entry; here stop above entry
    s = _setup(Direction.LONG, 100, 101, 102, 2.0)
    reasons = eng.validate_setup(s, _cfg())
    assert any("invert" in r.lower() or "stop" in r.lower() for r in reasons)
    assert reasons != []


def test_validate_setup_inverted_long_target():
    # LONG target must be above entry; here target below entry
    s = _setup(Direction.LONG, 100, 99, 99.5, 2.0)
    reasons = eng.validate_setup(s, _cfg())
    assert reasons != []


def test_validate_setup_inverted_short_stop():
    # SHORT stop must be above entry; here stop below entry
    s = _setup(Direction.SHORT, 100, 99, 98, 2.0)
    reasons = eng.validate_setup(s, _cfg())
    assert reasons != []


def test_validate_setup_inverted_short_target():
    # SHORT target must be below entry; here target above entry
    s = _setup(Direction.SHORT, 100, 101, 100.5, 2.0)
    reasons = eng.validate_setup(s, _cfg())
    assert reasons != []


def test_validate_setup_low_rr():
    s = _setup(Direction.LONG, 100, 99, 101, 1.0)  # rr below floor 2.0
    reasons = eng.validate_setup(s, _cfg())
    assert any("rr" in r.lower() or "reward" in r.lower() for r in reasons)


def test_validate_setup_min_stop_distance():
    s = _setup(Direction.LONG, 100, Decimal("99.995"), 100.01, 2.0)  # stop dist 0.005 < 0.02
    reasons = eng.validate_setup(s, _cfg())
    assert any("stop" in r.lower() and "dist" in r.lower() for r in reasons) or reasons != []


def _mk_candle(o, h, lo, c, minute, tf=15, bars=15):
    ts_open = datetime(2026, 6, 19, 9, 30, tzinfo=ET) + timedelta(minutes=minute)
    return Candle(
        ts_open=ts_open, ts_close=ts_open + timedelta(minutes=tf),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(lo)), close=Decimal(str(c)),
        volume=1000, timeframe_min=tf, bars_present=bars,
    )


def _ctx_in_wait_entry(cfg, direction=Direction.LONG, break_level="101"):
    e = eng.Engine(cfg, date(2026, 6, 19))
    ctx = e.ctx
    ctx.state = State.WAIT_ENTRY
    ctx.direction = direction
    ctx.break_level = Decimal(break_level)
    ctx.opening_range = OpeningRange(
        high=Decimal("101"), low=Decimal("99"),
        established_at=datetime(2026, 6, 19, 9, 45, tzinfo=ET),
        width=Decimal("2"), feed="IEX", bars_present=15, low_confidence=False,
    )
    # seed ATR so it's ready with a known value
    seed = [_mk_candle(100, 100.5, 99.5, 100, m * 15, tf=15) for m in range(-cfg.atr_period - 1, 0)]
    ctx.atr.seed(seed)
    return e, ctx


def test_entry_breakout_requires_displacement(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 15)
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "detect_displacement", lambda *a, **k: None)
    assert eng.entry_breakout(ctx, candle, cfg) is None


def test_entry_breakout_builds_setup_with_displacement(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 15)
    ctx.entry_window = [candle]
    from orb_bot.models import Displacement
    disp = Displacement(type="IMPULSE", upper_candle=candle, lower_candle=candle, size=Decimal("1"))
    monkeypatch.setattr(eng, "detect_displacement", lambda *a, **k: disp)
    s = eng.entry_breakout(ctx, candle, cfg)
    assert s is not None
    assert s.direction is Direction.LONG
    assert s.model is Model.BREAKOUT
    assert s.entry == candle.close
    assert s.stop < s.entry < s.target


def test_entry_breakout_short_stop_above_break_level(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.SHORT, "99")
    candle = _mk_candle(99, 99.1, 98, 98.1, 15)
    ctx.entry_window = [candle]
    from orb_bot.models import Displacement
    disp = Displacement(type="IMPULSE", upper_candle=candle, lower_candle=candle, size=Decimal("1"))
    monkeypatch.setattr(eng, "detect_displacement", lambda *a, **k: disp)
    s = eng.entry_breakout(ctx, candle, cfg)
    assert s is not None
    assert s.direction is Direction.SHORT
    assert s.entry == candle.close
    assert s.target < s.entry < s.stop  # short: stop above, target below


def test_entry_retest_no_return_increments_only(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    # price far from break_level => within() False => no setup, wait increments via try_build_entry
    candle = _mk_candle(105, 105.5, 104.5, 105, 30)
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "within", lambda *a, **k: False)
    assert eng.entry_retest(ctx, candle, cfg) is None


def test_entry_retest_uses_swing_stop(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 101.3, 100.8, 101.2, 30)  # returns to break_level, holds
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "within", lambda *a, **k: True)
    monkeypatch.setattr(eng, "is_strong_close", lambda *a, **k: True)
    monkeypatch.setattr(eng, "find_swing", lambda *a, **k: Decimal("100.50"))
    s = eng.entry_retest(ctx, candle, cfg)
    assert s is not None
    assert s.model is Model.RETEST
    assert s.stop == Decimal("100.50")


def test_entry_retest_swing_none_fallback(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 101.3, 100.8, 101.2, 30)
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "within", lambda *a, **k: True)
    monkeypatch.setattr(eng, "is_strong_close", lambda *a, **k: True)
    monkeypatch.setattr(eng, "find_swing", lambda *a, **k: None)
    s = eng.entry_retest(ctx, candle, cfg)
    assert s is not None
    buf = eng._buffer(cfg, ctx.atr.value)
    assert s.stop == ctx.break_level - buf  # fallback break_level - buffer for LONG


def test_entry_retest_short_swing_none_fallback(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.SHORT, "99")
    candle = _mk_candle(99, 99.2, 98.7, 98.8, 30)
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "within", lambda *a, **k: True)
    monkeypatch.setattr(eng, "is_strong_close", lambda *a, **k: True)
    monkeypatch.setattr(eng, "find_swing", lambda *a, **k: None)
    s = eng.entry_retest(ctx, candle, cfg)
    assert s is not None
    buf = eng._buffer(cfg, ctx.atr.value)
    assert s.stop == ctx.break_level + buf  # fallback break_level + buffer for SHORT


def test_entry_retest_no_strong_close_returns_none(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 101.3, 100.8, 101.2, 30)
    ctx.entry_window = [candle]
    monkeypatch.setattr(eng, "within", lambda *a, **k: True)
    monkeypatch.setattr(eng, "is_strong_close", lambda *a, **k: False)
    assert eng.entry_retest(ctx, candle, cfg) is None


def test_try_build_entry_prefers_retest(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 101.3, 100.8, 101.2, 30)
    ctx.entry_window = [candle]
    rsetup = Setup(Direction.LONG, Model.RETEST, Decimal("101.2"), Decimal("100.5"),
                   Decimal("102.6"), 2.0, ["r"])
    bsetup = Setup(Direction.LONG, Model.BREAKOUT, Decimal("101.2"), Decimal("100.5"),
                   Decimal("102.6"), 2.0, ["b"])
    monkeypatch.setattr(eng, "entry_retest", lambda *a, **k: rsetup)
    monkeypatch.setattr(eng, "entry_breakout", lambda *a, **k: bsetup)
    s = eng.try_build_entry(ctx, candle, cfg)
    assert s is rsetup  # retest preferred


def test_try_build_entry_falls_back_to_breakout(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=True)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 101.3, 100.8, 101.2, 30)
    ctx.entry_window = [candle]
    bsetup = Setup(Direction.LONG, Model.BREAKOUT, Decimal("101.2"), Decimal("100.5"),
                   Decimal("102.6"), 2.0, ["b"])
    monkeypatch.setattr(eng, "entry_retest", lambda *a, **k: None)
    monkeypatch.setattr(eng, "entry_breakout", lambda *a, **k: bsetup)
    s = eng.try_build_entry(ctx, candle, cfg)
    assert s is bsetup


def test_try_build_entry_increments_and_abandons(monkeypatch):
    cfg = _cfg(enable_breakout=False, enable_retest=True, retest_max_wait_candles=2)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    monkeypatch.setattr(eng, "entry_retest", lambda *a, **k: None)
    c1 = _mk_candle(105, 105.5, 104.5, 105, 30)
    c2 = _mk_candle(105, 105.5, 104.5, 105, 45)
    ctx.entry_window = [c1]
    assert eng.try_build_entry(ctx, c1, cfg) is None
    assert ctx.retest_wait == 1
    assert ctx.retest_dead is False
    ctx.entry_window = [c1, c2]
    assert eng.try_build_entry(ctx, c2, cfg) is None
    assert ctx.retest_wait == 2
    assert ctx.retest_dead is True  # hit cap


def test_try_build_entry_rejects_invalid_setup(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 30)
    ctx.entry_window = [candle]
    bad = Setup(Direction.LONG, Model.BREAKOUT, Decimal("101"), Decimal("100"),
                Decimal("101.5"), 0.5, ["b"])  # rr below floor
    monkeypatch.setattr(eng, "entry_breakout", lambda *a, **k: bad)
    assert eng.try_build_entry(ctx, candle, cfg) is None


def test_on_candle_wait_entry_emits_setup_proposed(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 30)
    good = Setup(Direction.LONG, Model.BREAKOUT, Decimal("101.9"), Decimal("100.9"),
                 Decimal("103.9"), 2.0, ["b"])
    monkeypatch.setattr(eng, "try_build_entry", lambda *a, **k: good)
    events = e.on_candle(candle)
    assert any(isinstance(ev, orb_bot.models.SetupProposed) and ev.setup is good for ev in events)


def test_on_candle_wait_entry_noop_when_no_setup(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    candle = _mk_candle(101, 102, 100.9, 101.9, 30)
    monkeypatch.setattr(eng, "try_build_entry", lambda *a, **k: None)
    events = e.on_candle(candle)
    assert all(not isinstance(ev, orb_bot.models.SetupProposed) for ev in events)
    assert e.ctx.state is State.WAIT_ENTRY  # stays waiting


def test_on_candle_wait_entry_buffers_candle(monkeypatch):
    cfg = _cfg(enable_breakout=True, enable_retest=False)
    e, ctx = _ctx_in_wait_entry(cfg, Direction.LONG, "101")
    ctx.entry_window = []
    candle = _mk_candle(101, 102, 100.9, 101.9, 30)
    monkeypatch.setattr(eng, "try_build_entry", lambda *a, **k: None)
    e.on_candle(candle)
    assert e.ctx.entry_window[-1] is candle
    assert e.ctx.last_ts == candle.ts_close
