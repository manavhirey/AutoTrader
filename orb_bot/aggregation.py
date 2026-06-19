"""Pure 1m->T aggregation. No clock, no I/O, no datetime.now()."""
from __future__ import annotations

from datetime import datetime, time, timedelta


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
