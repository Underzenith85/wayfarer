"""Recorded time, invitation deadline boundaries and recovery after a saved claim."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_campaign
from test_actions import campaign, engine
from test_v1_api import api as api

from wayfarer.contracts import Campaign, CommandReceipt, TurnResult
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.transport.v1 import invitations
from wayfarer.transport.v1.common import Fault, Obj, obj
from wayfarer.transport.v1.ledger import Ledger
from wayfarer.transport.v1.service import V1Service


class CommitCrash(AsyncSQLiteStore):
    """A second handle on the same database that dies where the claim is saved."""

    async def commit_turn(self, *args: object, **kwargs: object) -> TurnResult:
        raise RuntimeError("after claim commit")


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


async def test_clock_captured_before_callback_and_retry_keeps_original(tmp_path: Path) -> None:
    instants = iter((CommandInstant(10), CommandInstant(20)))
    play = build_play(tmp_path, engine(), instants=lambda: next(instants), filename="time.sqlite")
    initial = campaign(play.engine)
    await seed_campaign(play.store, initial)
    calls = []

    def reduce(state: Campaign) -> CommandReceipt:
        calls.append(state["revision"])
        state["revision"] += 1
        return CommandReceipt(action="legacy", outcome="done")

    await commit_command(play, initial["id"], "clock", 0, "clock", reduce)
    await commit_command(play, initial["id"], "clock", 0, "clock", reduce)
    assert calls == [0]
    row = (await played(play.store, initial["id"]))[0]
    assert row.recorded_at_us == 10
    # An explicit replay instant never consults the clock (the iterator is exhausted).
    await commit_command(play, initial["id"], "next", 1, "next", reduce, instant=CommandInstant(30))
    assert (await played(play.store, initial["id"]))[-1].recorded_at_us == 30


async def test_ledger_uses_one_captured_or_supplied_instant(tmp_path: Path) -> None:
    captures: list[int] = []

    def capture() -> CommandInstant:
        captures.append(77)
        return CommandInstant(77)

    store = Ledger(tmp_path / "boundary.sqlite", instants=capture)
    async with store.transaction() as tx:
        assert captures == [77]
        await asyncio.sleep(0)
        assert tx.instant.unix_microseconds == 77
        await tx.put("saved", {"at": tx.instant.unix_microseconds})
    async with store.transaction(instant=CommandInstant(88)) as tx:
        assert captures == [77]
        assert await tx.get("saved") == {"at": 77}


def scripted(now: list[CommandInstant]) -> Callable[[], CommandInstant]:
    return lambda: now[0]


async def test_saved_invitation_claim_resumes_after_expiry(
    api: tuple[str, str, V1Service],
) -> None:
    _, cid, hosted = api
    now = [CommandInstant(1_000_000_000)]
    service = V1Service(
        hosted.runtime, hosted.ledger.path, processes=hosted.processes, instants=scripted(now)
    )
    await service.start()
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

    # The claim is saved by a worker whose store dies before the domain commit.
    assert isinstance(hosted.play.store, AsyncSQLiteStore)
    crashing = PlayService(
        CommitCrash(hosted.play.store.path),
        hosted.play.engine,
        rng=hosted.play.rng,
        sessions=hosted.play.sessions,
        instants=scripted(now),
        seeds=hosted.play.seeds,
    )
    broken = V1Service(
        build_runtime(crashing),
        hosted.ledger.path,
        processes=hosted.processes,
        instants=scripted(now),
    )
    await broken.start()
    try:
        with pytest.raises(RuntimeError, match="after claim"):
            await invitations.invitation(broken, "new", cid, "/redeem", redeem, redeem=True)
    finally:
        await broken.close()
    now[0] = CommandInstant(2_000_000_000)
    restarted = V1Service(
        hosted.runtime, hosted.ledger.path, processes=hosted.processes, instants=scripted(now)
    )
    await restarted.start()
    try:
        membership = await invitations.invitation(
            restarted, "new", cid, "/redeem", redeem, redeem=True
        )
        assert membership["role"] == "player"
        history = await played(service.play.store, cid)
        assert len(history) == 1 and history[0].recorded_at_us == 1_000_000_000
        assert (
            await invitations.invitation(restarted, "new", cid, "/redeem", redeem, redeem=True)
            == membership
        )
        assert await played(service.play.store, cid) == history
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
        await service.close()
