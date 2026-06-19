from datetime import date

import pytest

from orb_bot.execution import alpaca

from .context import orb_bot  # noqa: F401  (path shim — side-effect import)


def test_client_order_id_prefix_and_suffix():
    coid = alpaca.client_order_id(date(2026, 6, 19), "SPY", 1, "ENTRY")
    assert coid == "2026-06-19-SPY-1-ENTRY"
    assert coid.startswith("2026-06-19-SPY-1-")


def test_client_order_id_distinct_legs_share_prefix():
    entry = alpaca.client_order_id(date(2026, 6, 19), "SPY", 2, "ENTRY")
    tp = alpaca.client_order_id(date(2026, 6, 19), "SPY", 2, "TP")
    sl = alpaca.client_order_id(date(2026, 6, 19), "SPY", 2, "SL")
    assert entry.rsplit("-", 1)[0] == tp.rsplit("-", 1)[0] == sl.rsplit("-", 1)[0]
    assert entry.rsplit("-", 1)[0] == "2026-06-19-SPY-2"


def test_leg_role_for_known_suffixes():
    assert alpaca.leg_role_for("2026-06-19-SPY-1-ENTRY", "2026-06-19-SPY-1-ENTRY") == "ENTRY"
    assert alpaca.leg_role_for("2026-06-19-SPY-1-TP", "2026-06-19-SPY-1-ENTRY") == "TP"
    assert alpaca.leg_role_for("2026-06-19-SPY-1-SL", "2026-06-19-SPY-1-ENTRY") == "SL"
    assert alpaca.leg_role_for("2026-06-19-SPY-1-FLATTEN", "2026-06-19-SPY-1-ENTRY") == "FLATTEN"


def test_leg_role_for_unknown_suffix_defaults_flatten():
    # broker-initiated close_all_positions may carry an unknown/auto coid
    assert alpaca.leg_role_for("alpaca-auto-xyz", "2026-06-19-SPY-1-ENTRY") == "FLATTEN"


def test_whole_share_qty_rejects_below_one():
    assert alpaca.whole_share_qty(3) == 3
    with pytest.raises(ValueError):
        alpaca.whole_share_qty(0)
