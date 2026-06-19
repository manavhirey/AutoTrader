"""Paper-mode approver: auto-fires every setup with no human gate.

Reused later as the backtest auto-approver. Order placement stays mode-uniform:
the orchestrator only submits on ``APPROVE``, which this returns instantly, so
the placement path is identical and equally testable in paper and live.
"""
from __future__ import annotations

from orb_bot.models import ApprovalRequest


class AutoApprover:
    async def start(self) -> None:  # no-op
        return None

    async def request(self, req: ApprovalRequest) -> str:
        return "APPROVE"

    async def close(self) -> None:  # no-op
        return None
