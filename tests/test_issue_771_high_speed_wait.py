"""Campaigns fourth printing B366/B385/B394: Wait pauses a trajectory, not a turn."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_tactical import migration, setup

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.tactical import HighSpeedState
from wayfarer.engine.simulation.hex_geometry import Cell, Hex
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def course(
    tmp_path: Path, *, high_speed: HighSpeedState | None = None
) -> tuple[str, PlayService]:
    cid, play = await setup(tmp_path, migrate=False)
    migrate = migration()
    battlefield = migrate.battlefield.model_copy(
        update={
            "cells": tuple(Cell(position=Hex(q=q, r=r)) for q in range(-2, 6) for r in range(-8, 3))
        }
    )
    migrate = migrate.model_copy(
        update={
            "battlefield": battlefield,
            "placements": tuple(
                p.model_copy(update={"pose": p.pose.model_copy(update={"facing": 4})})
                if p.actor_id == "b"
                else p
                for p in migrate.placements
            ),
        }
    )
    await CombatService(play).execute(cid, migrate, principal_id="gm")
    play = play.for_campaign(await play.store.read(cid))

    def visible(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        state = state.model_copy(
            update={"world": state.world.learn("c", "seen-a").learn("c", "seen-b")}
        )
        state = state.model_copy(
            update={
                "encounters": tuple(
                    e.model_copy(
                        update={
                            "participants": tuple(
                                p.model_copy(update={"high_speed": high_speed})
                                if p.actor_id == "b"
                                else p
                                for p in e.participants
                            )
                        }
                    )
                    for e in state.encounters
                )
            }
        )
        campaign["play_json"] = state.model_dump_json()
        return CommandReceipt(action="combat", outcome="visible-course")

    current = await play.store.read(cid)
    await play.store.commit_turn(
        cid, "visible-course", current["revision"], "visible-course", visible
    )
    return cid, play.for_campaign(await play.store.read(cid))


@pytest.mark.parametrize("triggering", [False, True])
@pytest.mark.parametrize("entering", [False, True])
async def test_wait_allows_or_pauses_high_speed_entry(
    tmp_path: Path, triggering: bool, entering: bool
) -> None:
    cid, play = await course(
        tmp_path, high_speed=None if entering else HighSpeedState(velocity=6, direction=4)
    )
    distance = 5 if entering else 6
    completed_speed = HighSpeedState(velocity=6, direction=4, straight_yards=0 if entering else 6)
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "move",
            "zone": ((1, -1) if triggering else (4, -1),),
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    state = play._load(await play.store.read(cid))
    request = TakeCombatTurn(
        id="run",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="move",
        enter_high_speed=entering,
        hex_path=tuple(Hex(q=1, r=-n) for n in range(1, distance + 1)),
    )
    service = CombatService(play)
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, request, principal_id="a")
    assert play._load(await play.store.read(cid)) == state
    result = await service.execute(cid, request, principal_id="b")
    paused = play._load(await play.store.read(cid))
    runner = paused.encounters[0].participants[1]
    if not triggering:
        assert runner.position == Hex(q=1, r=-distance)
        assert runner.high_speed == completed_speed
        assert paused.encounters[0].wait_interrupt is None
        return
    assert result.code == "combat.wait_triggered"
    assert runner.position == Hex(q=1, r=-1)
    assert runner.high_speed == HighSpeedState(
        velocity=6,
        direction=4,
        remaining_yards=distance - 1,
        entry_turns=0 if entering else None,
        straight_yards=0 if entering else 1,
    )
    play.rng = RecordedDice(())
    assert await service.execute(cid, request, principal_id="b") == result
    assert play._load(await play.store.read(cid)) == paused
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    resume = ResumeInterruptedTurn(
        id="resume", actor_id="b", expected_revision=state.revision, encounter_id="fight"
    )
    with pytest.raises(ValidationError, match="braking"):
        await service.execute(cid, resume.model_copy(update={"cancel": True}), principal_id="b")
    assert play._load(await play.store.read(cid)) == state
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    service = CombatService(restarted)
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            resume.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id="b",
        )
    done = await service.execute(cid, resume, principal_id="b")
    resumed = play._load(await play.store.read(cid))
    runner = resumed.encounters[0].participants[1]
    assert runner.position == Hex(q=1, r=-distance)
    assert runner.high_speed == completed_speed
    assert resumed.encounters[0].wait_interrupt is None
    play.rng = RecordedDice(())
    assert await service.execute(cid, resume, principal_id="b") == done
    assert play._load(await play.store.read(cid)) == resumed
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("entering", [False, True])
async def test_wait_does_not_legalize_an_invalid_full_trajectory(
    tmp_path: Path, entering: bool
) -> None:
    cid, play = await course(
        tmp_path, high_speed=None if entering else HighSpeedState(velocity=6, direction=4)
    )
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "move",
            "zone": ((1, -1),),
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    state = play._load(await play.store.read(cid))
    distance = 5 if entering else 6
    service = CombatService(play)
    play.rng = RecordedDice(())
    for path in (
        tuple(Hex(q=1, r=-n) for n in range(1, distance)),
        (Hex(q=1, r=-1), Hex(q=1, r=-3)) + tuple(Hex(q=1, r=-n) for n in range(4, distance + 2)),
    ):
        with pytest.raises(ValidationError):
            await service.execute(
                cid,
                TakeCombatTurn(
                    id="invalid-path",
                    actor_id="b",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    maneuver="move",
                    enter_high_speed=entering,
                    hex_path=path,
                ),
                principal_id="b",
            )
        assert play._load(await play.store.read(cid)) == state
    assert play.rng.exhausted()


async def test_multiple_waiters_react_at_the_same_high_speed_checkpoint(tmp_path: Path) -> None:
    cid, play = await course(tmp_path)
    declaration = {
        "actor_id": "b",
        "action": "move",
        "zone": ((1, -1),),
        "reaction": "attack",
        "reaction_target_id": "b",
        "mode_id": "swing",
    }
    await turn(cid, play, "a", "wait", wait_trigger={**declaration, "item_id": "sword-a"})
    await turn(cid, play, "b", "do_nothing")
    await turn(
        cid,
        play,
        "c",
        "wait",
        wait_trigger={**declaration, "item_id": "sword-c", "reaction_target_id": "a"},
    )
    await turn(cid, play, "a", "wait", wait_trigger={**declaration, "item_id": "sword-a"})
    await turn(
        cid,
        play,
        "b",
        "move",
        enter_high_speed=True,
        hex_path=tuple({"q": 1, "r": -n} for n in range(1, 6)),
    )
    paused = play._load(await play.store.read(cid))
    assert paused.encounters[0].wait_interrupt is not None
    assert paused.encounters[0].wait_interrupt.waiter_id == "a"
    assert paused.encounters[0].participants[1].position == Hex(q=1, r=-1)
    await turn(cid, play, "a", "do_nothing")
    state = play._load(await play.store.read(cid))
    resume = ResumeInterruptedTurn(
        id="resume-a", actor_id="b", expected_revision=state.revision, encounter_id="fight"
    )
    service = CombatService(play)
    result = await service.execute(cid, resume, principal_id="b")
    paused = play._load(await play.store.read(cid))
    assert result.code == "combat.wait_triggered"
    assert paused.encounters[0].wait_interrupt is not None
    assert paused.encounters[0].wait_interrupt.waiter_id == "c"
    assert paused.encounters[0].participants[1].position == Hex(q=1, r=-1)
    assert paused.encounters[0].participants[1].high_speed is not None
    assert paused.encounters[0].participants[1].high_speed.remaining_yards == 4
    play.rng = RecordedDice(())
    assert await service.execute(cid, resume, principal_id="b") == result
    await turn(cid, play, "c", "do_nothing")
    state = play._load(await play.store.read(cid))
    await service.execute(
        cid,
        resume.model_copy(update={"id": "resume-c", "expected_revision": state.revision}),
        principal_id="b",
    )
    done = play._load(await play.store.read(cid))
    assert done.encounters[0].wait_interrupt is None
    assert done.encounters[0].participants[1].position == Hex(q=1, r=-5)
    assert done.encounters[0].participants[1].high_speed == HighSpeedState(velocity=6, direction=4)
    assert await play.store.read(cid) == await play.store.replay(cid)
