# orb_bot/indicators.py
from __future__ import annotations

from decimal import Decimal

from .models import Candle, Direction


def _true_range(bar: Candle, prev_close: Decimal) -> Decimal:
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(bar.low - prev_close),
    )


class ATR:
    """Wilder's ATR. TR=max(h-l, |h-prevC|, |l-prevC|); ATRn=(ATRn-1*(p-1)+TR)/p.

    Pure module: stdlib + orb_bot.models only. No wall-clock or I/O.
    """

    def __init__(self, period: int):
        if period < 1:
            raise ValueError("ATR period must be >= 1")
        self._period = period
        self._value: Decimal | None = None
        self._prev_close: Decimal | None = None

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def value(self) -> Decimal:
        if self._value is None:
            raise ValueError("ATR not ready: call seed() first")
        return self._value

    def seed(self, bars: list[Candle]) -> None:
        """Seed ATR from historical bars.

        Requires at least period+1 bars: the first bar establishes prevClose,
        then each subsequent bar produces one TR; the first `period` TRs are
        simple-averaged to form the initial ATR (Wilder's standard bootstrap).

        Raises ValueError if too few bars are supplied.
        """
        if len(bars) < self._period + 1:
            raise ValueError(
                f"ATR seed needs at least {self._period + 1} bars, got {len(bars)}"
            )
        prev_close = bars[0].close
        trs: list[Decimal] = []
        for bar in bars[1:]:
            trs.append(_true_range(bar, prev_close))
            prev_close = bar.close
        window = trs[: self._period]
        self._value = sum(window, Decimal(0)) / Decimal(self._period)
        # prev_close after the last bar that fed the seed window
        self._prev_close = bars[self._period].close

    def update(self, bar: Candle) -> Decimal:
        """Apply one bar of Wilder smoothing and return the new ATR value."""
        if self._value is None or self._prev_close is None:
            raise ValueError("ATR must be seeded before calling update()")
        tr = _true_range(bar, self._prev_close)
        p = Decimal(self._period)
        self._value = (self._value * (p - 1) + tr) / p
        self._prev_close = bar.close
        return self._value


def is_strong_close(
    c: Candle, direction: Direction, body_ratio: float, location: float
) -> bool:
    """Spec §5: quantified strong close.

    body = |close-open|; rng = high-low.
    body_ok = body/rng >= body_ratio.
    LONG  loc_ok = (close-low)/rng  >= location.
    SHORT loc_ok = (high-close)/rng >= location.
    Degenerate rng==0 -> False.
    """
    rng = c.high - c.low
    if rng <= 0:
        return False
    body = abs(c.close - c.open)
    body_ratio_d = Decimal(str(body_ratio))
    location_d = Decimal(str(location))
    body_ok = (body / rng) >= body_ratio_d
    if direction is Direction.LONG:
        loc_ok = ((c.close - c.low) / rng) >= location_d
    else:
        loc_ok = ((c.high - c.close) / rng) >= location_d
    return bool(body_ok and loc_ok)


def within(c: Candle, level: Decimal, tol: Decimal) -> bool:
    """Spec §12: candle range touches the [level-tol, level+tol] band (inclusive)."""
    return c.low <= level + tol and c.high >= level - tol
