"""Recorded time, invitation deadline boundaries and recovery after a saved claim."""

import asyncio
from pathlib import Path

import pytest
from test_actions import campaign, engine
from test_v1_api import api as api

from wayfarer.contracts import Campaign, CommandReceipt, TurnResult
from wayfarer.orchestration import entropy
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.transport.v1 import invitations
from wayfarer.transport.v1.common import Fault, Obj, obj
from wayfarer.transport.v1.ledger import Ledger
from wayfarer.transport.v1.service import V1Service


def test_claim_deadline_uses_supplied_instant() -> None:
    invite: Obj = {"claim": None, "expires": 10.0}
    assert invitations.claim_is_valid(invite, "a", CommandInstant(9_999_999), retry=False)
    assert invitations.claim_is_valid(invite, "a", CommandInstant(10_000_000), retry=False)
    assert not invitations.claim_is_valid(invite, "a", CommandInstant(10_000_001), retry=False)
    assert invitations.claim_is_valid(
        {**invite, "claim": "a"}, "a", CommandInstant(99_000_000), retry=True
    )
    assert not invitations.claim_is_valid(
        {**invite, "claim": "a"}, "b", CommandInstant(1), retry=True
    )
    assert CommandInstant(1_000_001).isoformat() == "1970-01-01T00:00:01.000Z"


async def test_clock_captured_before_callback_and_retry_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AsyncSQLiteStore(tmp_path / "time.sqlite")
    initial = campaign(engine())
    await store.insert(initial)
    instants = iter((CommandInstant(10), CommandInstant(20)))
    monkeypatch.setattr(entropy, "capture_instant", lambda: next(instants))
    calls = []

    def reduce(state: Campaign) -> CommandReceipt:
        calls.append(state["revision"])
        state["revision"] += 1
        return CommandReceipt(action="legacy", outcome="done")

    await entropy.commit_command(store, initial["id"], "clock", 0, "clock", reduce)
    await entropy.commit_command(store, initial["id"], "clock", 0, "clock", reduce)
    assert calls == [0]
    row = (await store.history(initial["id"]))[0]
    assert row.recorded_at_us == 10
    # An explicit replay instant never consults the clock (the iterator is exhausted).
    await entropy.commit_command(
        store, initial["id"], "next", 1, "next", reduce, instant=CommandInstant(30)
    )
    assert (await store.history(initial["id"]))[-1].recorded_at_us == 30


async def test_ledger_uses_one_captured_or_supplied_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.transport.v1 import ledger

    captures: list[int] = []

    def capture() -> CommandInstant:
        captures.append(77)
        return CommandInstant(77)

    monkeypatch.setattr(ledger, "capture_instant", capture)
    store = Ledger(tmp_path / "boundary.sqlite")
    async with store.transaction() as tx:
        assert captures == [77]
        await asyncio.sleep(0)
        assert tx.instant.unix_microseconds == 77
        await tx.put("saved", {"at": tx.instant.unix_microseconds})
    async with store.transaction(instant=CommandInstant(88)) as tx:
        assert captures == [77]
        assert await tx.get("saved") == {"at": 77}


async def test_saved_invitation_claim_resumes_after_expiry(
    api: tuple[str, str, V1Service], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, cid, service = api
    now = [CommandInstant(1_000_000_000)]
    monkeypatch.setattr(invitations, "capture_instant", lambda: now[0])
    async with service.ledger.transaction(instant=now[0]) as tx:
        version = obj((await service.view(tx, cid, "gm")).campaign["membership"])["version"]
    request: Obj = {
        "command_id": "create-clock",
        "expected_membership_version": version,
        "role": "player",
        "expires_in_seconds": 60,
    }
    invite = await invitations.invitation(service, "gm", cid, "/invitations", request, redeem=False)
    redeem: Obj = {"command_id": "claim-clock", "token": invite["token"]}
    original = entropy.commit_command

    async def crash(*args: object, **kwargs: object) -> TurnResult:
        raise RuntimeError("after claim commit")

    monkeypatch.setattr(invitations, "commit_command", crash)
    with pytest.raises(RuntimeError, match="after claim"):
        await invitations.invitation(service, "new", cid, "/redeem", redeem, redeem=True)
    now[0] = CommandInstant(2_000_000_000)
    monkeypatch.setattr(invitations, "commit_command", original)
    restarted = V1Service(service.play, service.ledger.path)
    await restarted.start()
    try:
        membership = await invitations.invitation(
            restarted, "new", cid, "/redeem", redeem, redeem=True
        )
        assert membership["role"] == "player"
        history = await service.play.store.history(cid)
        assert len(history) == 1 and history[0].recorded_at_us == 1_000_000_000
        assert (
            await invitations.invitation(restarted, "new", cid, "/redeem", redeem, redeem=True)
            == membership
        )
        assert await service.play.store.history(cid) == history
        with pytest.raises(Fault):
            await invitations.invitation(
                restarted,
                "bob",
                cid,
                "/redeem",
                {"command_id": "other", "token": invite["token"]},
                redeem=True,
            )
        # Re-evaluating the original expiry decision after the real deadline uses recorded time.
        assert invitations.claim_is_valid(
            {"claim": None, "expires": 1060.0},
            "claim",
            CommandInstant(history[0].recorded_at_us),
            retry=False,
        )
    finally:
        await restarted.close()
