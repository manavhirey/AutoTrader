import asyncio
from decimal import Decimal
from zoneinfo import ZoneInfo

from orb_bot.approval.manual import (
    ApprovalView,
    DiscordApprover,
    build_proposal_embed,
)
from orb_bot.interfaces import Approver
from orb_bot.models import ApprovalRequest, Direction, Model, Setup

ET = ZoneInfo("America/New_York")
APPROVER_ID = 555


def _req(data_warning=None, feed="SIP", mode="LIVE", bars_present=15) -> ApprovalRequest:
    setup = Setup(
        direction=Direction.LONG,
        model=Model.BREAKOUT,
        entry=Decimal("100.00"),
        stop=Decimal("99.00"),
        target=Decimal("102.00"),
        rr=2.0,
        reason=["strong close", "above OR high"],
    )
    return ApprovalRequest(
        setup=setup,
        symbol="AAPL",
        qty=10,
        risk_dollars=Decimal("50"),
        mode=mode,
        feed=feed,
        or_high=Decimal("100.50"),
        or_low=Decimal("99.50"),
        bars_present=bars_present,
        data_warning=data_warning,
        approval_ttl_s=90,
    )


def _embed_text(embed) -> str:
    parts = [str(embed.title or ""), str(embed.description or "")]
    for f in embed.fields:
        parts.append(str(f.name))
        parts.append(str(f.value))
    return "\n".join(parts)


def test_build_proposal_embed_has_core_fields():
    em = build_proposal_embed(_req())
    text = _embed_text(em)
    assert "AAPL" in text
    assert "LONG" in text
    assert "BREAKOUT" in text
    assert "100.00" in text  # entry
    assert "99.00" in text   # stop
    assert "102.00" in text  # target
    assert "2.0" in text     # rr
    assert "50.0" in text or "50" in text  # risk $
    assert "10" in text      # qty
    assert "strong close" in text
    assert "SIP" in text     # feed
    assert "15" in text      # bars_present / T


def test_build_proposal_embed_shows_mode_and_iex_warning():
    em = build_proposal_embed(_req(data_warning="iex_partial", feed="IEX", mode="PAPER"))
    text = _embed_text(em)
    assert "PAPER" in text
    assert "IEX" in text
    assert "iex_partial" in text


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeResponse:
    def __init__(self, raises=False):
        self.edited = None
        self._raises = raises

    async def edit_message(self, content=None, view=None):
        if self._raises:
            raise RuntimeError("interaction expired")
        self.edited = {"content": content, "view": view}


class _FakeInteraction:
    def __init__(self, uid, raises=False):
        self.user = _FakeUser(uid)
        self.response = _FakeResponse(raises=raises)


async def test_view_approve_acks_then_resolves_future():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    interaction = _FakeInteraction(APPROVER_ID)
    await view.approve.callback(interaction)
    assert interaction.response.edited == {"content": "✅ Approved", "view": None}
    assert fut.result() == "APPROVE"


async def test_view_reject_acks_then_resolves_future():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    interaction = _FakeInteraction(APPROVER_ID)
    await view.reject.callback(interaction)
    assert interaction.response.edited == {"content": "❌ Rejected", "view": None}
    assert fut.result() == "REJECT"


async def test_interaction_check_allows_only_approver():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    assert await view.interaction_check(_FakeInteraction(APPROVER_ID)) is True
    assert await view.interaction_check(_FakeInteraction(999)) is False


async def test_view_callback_error_is_non_approval():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    interaction = _FakeInteraction(APPROVER_ID, raises=True)
    await view.approve.callback(interaction)  # edit_message raises => non-approval
    assert fut.result() == "REJECT"


async def test_view_on_timeout_resolves_timeout():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    await view.on_timeout()
    assert fut.result() == "TIMEOUT"


async def test_view_does_not_double_resolve():
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)
    await view.approve.callback(_FakeInteraction(APPROVER_ID))
    await view.on_timeout()  # already resolved; must not overwrite
    assert fut.result() == "APPROVE"


async def test_discordapprover_satisfies_protocol():
    class _StubClient:
        is_ready = True

        async def resolve_channel(self):
            raise AssertionError("not called in this test")

    appr = DiscordApprover(_StubClient(), APPROVER_ID)
    assert isinstance(appr, Approver)


async def test_start_and_close_drive_shared_client_lifecycle():
    """DiscordApprover.start()/close() must drive the shared client's
    start_in_background()/close() — without this the live gateway never connects
    and every trade is REJECTED (is_ready False)."""

    class _RecordingClient:
        def __init__(self):
            self.started = 0
            self.closed = 0

        async def start_in_background(self):
            self.started += 1

        async def close(self):
            self.closed += 1

    client = _RecordingClient()
    appr = DiscordApprover(client, APPROVER_ID)
    await appr.start()
    assert client.started == 1
    await appr.close()
    assert client.closed == 1


async def test_request_posts_embed_with_view_and_returns_decision():
    class _FakeChannel:
        def __init__(self):
            self.sent = None

        async def send(self, embed=None, view=None):
            self.sent = {"embed": embed, "view": view}
            # simulate the approver clicking Approve once the message is posted
            view._fut.set_result("APPROVE")

    class _FakeClient:
        is_ready = True

        def __init__(self, channel):
            self._channel = channel

        async def resolve_channel(self):
            return self._channel

    channel = _FakeChannel()
    appr = DiscordApprover(_FakeClient(channel), APPROVER_ID)
    decision = await appr.request(_req())
    assert decision == "APPROVE"
    assert channel.sent["embed"] is not None
    assert isinstance(channel.sent["view"], ApprovalView)


# --- Fail-closed hardening tests ---

async def test_request_rejects_when_client_not_ready():
    """is_ready == False → request() returns REJECT and channel.send is never called."""

    class _FakeChannel:
        def __init__(self):
            self.send_called = False

        async def send(self, embed=None, view=None):
            self.send_called = True

    class _NotReadyClient:
        is_ready = False

        async def resolve_channel(self):
            # Should never be reached
            return _FakeChannel()

    channel = _FakeChannel()
    appr = DiscordApprover(_NotReadyClient(), APPROVER_ID)
    decision = await appr.request(_req())
    assert decision == "REJECT"
    assert not channel.send_called


async def test_request_rejects_when_resolve_channel_raises():
    """resolve_channel() raising → request() returns REJECT."""

    class _BrokenClient:
        is_ready = True

        async def resolve_channel(self):
            raise RuntimeError("cannot connect to Discord")

    appr = DiscordApprover(_BrokenClient(), APPROVER_ID)
    decision = await appr.request(_req())
    assert decision == "REJECT"


async def test_request_rejects_when_send_raises():
    """channel.send raising → request() returns REJECT."""

    class _BrokenChannel:
        async def send(self, embed=None, view=None):
            raise RuntimeError("HTTP 500 from Discord")

    class _FakeClient:
        is_ready = True

        async def resolve_channel(self):
            return _BrokenChannel()

    appr = DiscordApprover(_FakeClient(), APPROVER_ID)
    decision = await appr.request(_req())
    assert decision == "REJECT"


# --- New hardening tests (Task 33 review findings) ---


async def test_request_rejects_when_client_has_no_is_ready_attribute():
    """Client with NO is_ready attribute → fail-closed → REJECT, resolve_channel never called.

    Locks Fix A: getattr default must be False so a missing attribute rejects.
    """

    class _NoReadyAttrClient:
        # deliberately no is_ready attribute
        async def resolve_channel(self):
            raise AssertionError("resolve_channel must NOT be called when client has no is_ready")

    appr = DiscordApprover(_NoReadyAttrClient(), APPROVER_ID)
    decision = await appr.request(_req())
    assert decision == "REJECT"


async def test_request_returns_timeout_when_send_does_not_resolve_future():
    """send() that leaves the Future pending → on_timeout drives TIMEOUT resolution.

    We call view.on_timeout() from within send() to simulate the View's own timeout
    firing, so the test completes immediately (no sleep needed).
    """

    class _PendingChannel:
        async def send(self, embed=None, view=None):
            # Simulate the View timing out without any button click.
            await view.on_timeout()

    class _FakeClient:
        is_ready = True

        async def resolve_channel(self):
            return _PendingChannel()

    appr = DiscordApprover(_FakeClient(), APPROVER_ID)
    # approval_ttl_s=90 means wait_for adds 5s — but on_timeout() resolves the
    # future immediately inside send(), so request() returns before any wall-clock wait.
    decision = await appr.request(_req())
    assert decision == "TIMEOUT"


async def test_auth_gate_false_leaves_future_unresolved():
    """Non-approver interaction_check returns False and the Future stays unresolved.

    discord.py's _scheduled_task does `if not allow: return` after interaction_check,
    so a False check means the button callback never runs and the Future is not touched.
    """
    fut = asyncio.get_running_loop().create_future()
    view = ApprovalView(APPROVER_ID, fut, timeout=90.0)

    non_approver = _FakeInteraction(uid=999)
    allowed = await view.interaction_check(non_approver)

    assert allowed is False
    # discord.py returns without calling the callback when check returns False,
    # so the Future must remain unresolved here.
    assert not fut.done()
