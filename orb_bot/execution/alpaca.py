from __future__ import annotations

from datetime import date

_KNOWN_LEGS = {"ENTRY", "TP", "SL", "FLATTEN"}


def client_order_id(session_date: date, symbol: str, seq: int, leg_role: str) -> str:
    """Deterministic id: prefix ``{date}-{symbol}-{seq}`` shared across a trade's legs,
    suffix ``-{leg_role}``. Prefix is the P/L grouping key (spec §5/§17a)."""
    if leg_role not in _KNOWN_LEGS:
        raise ValueError(f"unknown leg_role {leg_role!r}")
    return f"{session_date.isoformat()}-{symbol}-{seq}-{leg_role}"


def leg_role_for(coid: str, parent_coid: str) -> str:
    """Classify a fill's client_order_id by its suffix. Unknown/auto ids (e.g. a
    broker-initiated close_all_positions) are treated as FLATTEN (spec §13/§16)."""
    suffix = coid.rsplit("-", 1)[-1]
    return suffix if suffix in _KNOWN_LEGS else "FLATTEN"


def whole_share_qty(qty: int) -> int:
    """Defense-in-depth: brackets are whole-share; reject qty < 1 (spec §16)."""
    if qty < 1:
        raise ValueError(f"qty must be >= 1, got {qty}")
    return int(qty)
