"""Pure 1m->T aggregation. No clock, no I/O, no datetime.now()."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from orb_bot.models import Candle


def bucket_start(ts: datetime, tf_min: int, anchor: time = time(9, 30)) -> datetime:
    """Floor ``ts`` to the start of its ``tf_min``-minute bucket anchored at ``anchor``.

    Returns ``base + floor((ts - base) / tf) * tf`` where ``base`` is ``anchor``
    applied to ``ts``'s calendar date in ``ts``'s timezone. Pure; no clock.
    """
    base = ts.replace(
        hour=anchor.hour,
        minute=anchor.minute,
        second=anchor.second,
        microsecond=anchor.microsecond,
    )
    delta = ts - base
    n = delta // timedelta(minutes=tf_min)  # floor division (works for negatives too)
    return base + n * timedelta(minutes=tf_min)


class Aggregator:
    """Rolls a 1-minute transport stream up to a single ``tf_min`` timeframe.

    Anchored at ``anchor`` wall-clock buckets (NOT bar-count). Pure: emission is
    driven only by the timestamps of the bars passed in (and ``force_close``),
    never by a wall clock. Idempotent on duplicate 1m bars (reconnect-safe).
    """

    def __init__(self, tf_min: int, anchor: time = time(9, 30)) -> None:
        self.tf_min = tf_min
        self.anchor = anchor
        self._bucket_ts: datetime | None = None  # start of the open bucket
        self._children: list[Candle] = []        # 1m bars in the open bucket
        self._seen_opens: set[datetime] = set()  # ts_open dedupe for the open bucket

    def add(self, one_min: Candle) -> Candle | None:
        """Add a 1m bar; emit the prior closed aggregate when the bucket advances."""
        b = bucket_start(one_min.ts_open, self.tf_min, self.anchor)
        emitted: Candle | None = None

        if self._bucket_ts is None:
            self._bucket_ts = b
        elif b != self._bucket_ts:
            emitted = self._emit()
            self._bucket_ts = b
            self._children = []
            self._seen_opens = set()

        if one_min.ts_open in self._seen_opens:
            return emitted  # duplicate 1m bar -> idempotent, do not re-count
        self._seen_opens.add(one_min.ts_open)
        self._children.append(one_min)
        return emitted

    def _emit(self) -> Candle:
        """Build the closed T-candle for the currently-open bucket.

        Derives ``bars_present`` from the current children snapshot so callers
        cannot pass a stale count.
        """
        assert self._bucket_ts is not None
        ts_open = self._bucket_ts
        ts_close = ts_open + timedelta(minutes=self.tf_min)
        children = list(self._children)
        bars_present = len(children)
        if bars_present == 0:
            zero = Decimal("0")
            return Candle(
                ts_open=ts_open,
                ts_close=ts_close,
                open=zero,
                high=zero,
                low=zero,
                close=zero,
                volume=0,
                timeframe_min=self.tf_min,
                data_incomplete=True,
                bars_present=0,
            )
        return Candle(
            ts_open=ts_open,
            ts_close=ts_close,
            open=children[0].open,
            high=max(c.high for c in children),
            low=min(c.low for c in children),
            close=children[-1].close,
            volume=sum(c.volume for c in children),
            timeframe_min=self.tf_min,
            data_incomplete=bars_present < self.tf_min,
            bars_present=bars_present,
        )

    def force_close(self, boundary_ts: datetime) -> Candle | None:
        """Timer-driven flush of the open bucket once its boundary has passed.

        Emits a partial aggregate (``data_incomplete=True`` when children < tf_min)
        or, if zero children buffered, a no-OHLC candle the engine refuses
        (spec §10 #13). Idempotent: returns ``None`` when there is nothing open or
        the boundary has not yet crossed the open bucket.
        """
        if self._bucket_ts is None:
            return None
        if bucket_start(boundary_ts, self.tf_min, self.anchor) <= self._bucket_ts:
            return None  # boundary still inside the open bucket -> not closeable yet
        emitted = self._emit()
        self._bucket_ts = None
        self._children = []
        self._seen_opens = set()
        return emitted
