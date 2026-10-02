"""Private clock consumers see durable instants within their transaction only."""

import asyncio
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign, engine

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.errors import ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.entropy import commit_command, current_command_instant
from wayfarer.orchestration.replay_inputs import replay_inputs


def forbidden_instant() -> CommandInstant:
    raise AssertionError("Replay must use the recorded instant")


def forbidden_seed() -> str:
    raise AssertionError("Replay must use the recorded seed")


async def test_captured_scope_matches_record_and_seed_replay_ignores_other_clocks(
    tmp_path: Path,
) -> None:
    captures: list[int] = []

    def capture() -> CommandInstant:
        captures.append(9_007_199_254_740_993)
        return CommandInstant(captures[-1])

    play = build_play(tmp_path, engine(), instants=capture, filename="captured.sqlite")
    initial = campaign(play.engine)
    await seed_campaign(play.store, initial)
    observed: list[CommandInstant] = []

    def reduce(state: Campaign) -> CommandReceipt:
        instant = current_command_instant()
        observed.append(instant)
        assert current_command_instant() is instant
        state["revision"] += 1
        return CommandReceipt(action="legacy", outcome=str(instant.unix_microseconds))

    result = await commit_command(play, initial["id"], "observe", 0, "observe", reduce)
    record = (await played(play.store, initial["id"]))[0]
    assert captures == [9_007_199_254_740_993]
    assert record.recorded_at_us == observed[0].unix_microseconds
    assert record.event["outcome"] == str(record.recorded_at_us)
    with pytest.raises(ValidationError, match="command scope"):
        current_command_instant()

    replay = build_play(
        tmp_path,
        engine(),
        instants=forbidden_instant,
        seeds=forbidden_seed,
        filename="replayed.sqlite",
    )
    await seed_campaign(replay.store, initial)
    with replay_inputs(record):
        replayed = await commit_command(
            replay,
            initial["id"],
            "observe",
            0,
            "observe",
            reduce,
            instant=CommandInstant(1),
        )
    assert replayed == result and observed[1] == observed[0]
    copied = (await played(replay.store, initial["id"]))[0]
    assert copied.recorded_at_us == record.recorded_at_us
    assert copied.entropy_seed == record.entropy_seed
    with pytest.raises(ValidationError, match="command scope"):
        current_command_instant()


async def test_failure_resets_scope_and_successful_retry_keeps_its_recorded_instant(
    tmp_path: Path,
) -> None:
    play = build_play(tmp_path, engine(), filename="rollback-time.sqlite")
    initial = campaign(play.engine)
    await seed_campaign(play.store, initial)

    def fail(state: Campaign) -> CommandReceipt:
        assert current_command_instant() == CommandInstant(100)
        state["revision"] += 1
        raise ValidationError("consequence rejected")

    with pytest.raises(ValidationError, match="consequence rejected"):
        await commit_command(
            play, initial["id"], "observe", 0, "observe", fail, instant=CommandInstant(100)
        )
    assert await play.store.read(initial["id"]) == initial
    assert await played(play.store, initial["id"]) == []
    with pytest.raises(ValidationError, match="command scope"):
        current_command_instant()
    calls: list[int] = []

    def succeed(state: Campaign) -> CommandReceipt:
        calls.append(current_command_instant().unix_microseconds)
        state["revision"] += 1
        return CommandReceipt(action="legacy", outcome=str(calls[-1]))

    committed = await commit_command(
        play, initial["id"], "observe", 0, "observe", succeed, instant=CommandInstant(200)
    )
    repeated = await commit_command(
        play, initial["id"], "observe", 0, "observe", succeed, instant=CommandInstant(999)
    )
    assert calls == [200]
    assert committed["state"] == repeated["state"]
    assert (await played(play.store, initial["id"]))[0].recorded_at_us == 200


async def test_concurrent_campaigns_observe_only_their_own_captured_instant(tmp_path: Path) -> None:
    play = build_play(tmp_path, engine(), filename="concurrent-time.sqlite")
    first, second = campaign(play.engine), campaign(play.engine)
    await seed_campaign(play.store, first)
    await seed_campaign(play.store, second)
    expected = {first["id"]: 101, second["id"]: 202}

    def reduce(state: Campaign) -> CommandReceipt:
        assert current_command_instant().unix_microseconds == expected[state["id"]]
        state["revision"] += 1
        return CommandReceipt(action="legacy", outcome="done")

    await asyncio.gather(
        *(
            commit_command(play, cid, "observe", 0, "observe", reduce, instant=CommandInstant(at))
            for cid, at in expected.items()
        )
    )
    for cid, at in expected.items():
        assert (await played(play.store, cid))[0].recorded_at_us == at
    with pytest.raises(ValidationError, match="command scope"):
        current_command_instant()
