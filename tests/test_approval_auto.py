from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot.approval.auto import AutoApprover
from orb_bot.interfaces import Approver
from orb_bot.models import ApprovalRequest, Direction, Model, Setup

ET = ZoneInfo("America/New_York")


def _req() -> ApprovalRequest:
    setup = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["strong close"],
    )
    return ApprovalRequest(
        setup=setup,
        symbol="AAPL",
        qty=10,
        risk_dollars=50.0,
        mode="PAPER",
        feed="IEX",
        or_high=Decimal("100.50"),
        or_low=Decimal("99.50"),
        bars_present=15,
        data_warning=None,
        approval_ttl_s=90,
    )


def test_autoapprover_satisfies_protocol():
    assert isinstance(AutoApprover(), Approver)


async def test_autoapprover_returns_approve_instantly():
    appr = AutoApprover()
    await appr.start()
    decision = await appr.request(_req())
    assert decision == "APPROVE"
    await appr.close()


async def test_autoapprover_start_close_are_noops_idempotent():
    appr = AutoApprover()
    await appr.start()
    await appr.start()
    await appr.close()
    await appr.close()
    assert await appr.request(_req()) == "APPROVE"
