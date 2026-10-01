"""Campaigns fourth printing B366/B385/B394: Wait pauses a trajectory, not a turn."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_tactical import migration, setup

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.tactical import HighSpeedState
from wayfarer.engine.simulation.hex_geometry import Cell, Hex
from wayfarer.engine.world import Fact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def course(
    tmp_path: Path,
    *,
    high_speed: HighSpeedState | None = None,
    third_position: Hex | None = None,
    opaque_first: bool = False,
) -> tuple[str, PlayService]:
    cid, play = await setup(tmp_path, migrate=False)
    migrate = migration()
    battlefield = migrate.battlefield.model_copy(
        update={
            "cells": tuple(
                Cell(
                    position=Hex(q=q, r=r),
                    opaque_height=3 if opaque_first and (q, r) == (1, -1) else 0,
                )
                for q in range(-2, 6)
                for r in range(-8, 3)
            )
        }
    )
    migrate = migrate.model_copy(
        update={
            "battlefield": battlefield,
            "placements": tuple(
                p.model_copy(update={"pose": p.pose.model_copy(update={"facing": 4})})
                if p.actor_id == "b"
                else p.model_copy(
                    update={"pose": p.pose.model_copy(update={"position": third_position})}
                )
                if p.actor_id == "c" and third_position is not None
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
            update={
                "world": replace(
                    state.world, facts=state.world.facts + (Fact("seen-c", "c", "visible", "yes"),)
                )
                .learn("c", "seen-a")
                .learn("c", "seen-b")
                .learn("b", "seen-c")
            }
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


@pytest.mark.parametrize("checkpoint", [1, 5])
async def test_multiple_waiters_react_at_the_same_high_speed_checkpoint(
    tmp_path: Path, checkpoint: int
) -> None:
    cid, play = await course(tmp_path)
    declaration = {
        "actor_id": "b",
        "action": "move",
        "zone": ((1, -checkpoint),),
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
    assert paused.encounters[0].participants[1].position == Hex(q=1, r=-checkpoint)
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
    assert paused.encounters[0].participants[1].position == Hex(q=1, r=-checkpoint)
    assert paused.encounters[0].participants[1].high_speed is not None
    assert paused.encounters[0].participants[1].high_speed.remaining_yards == (
        5 - checkpoint or None
    )
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


async def test_waiters_resolve_in_path_order_before_turn_order(tmp_path: Path) -> None:
    """B385: the first encountered trigger interrupts before a later path entry."""
    cid, play = await course(tmp_path)
    declaration = {
        "actor_id": "b",
        "action": "move",
        "reaction": "attack",
        "reaction_target_id": "b",
        "mode_id": "swing",
    }
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    await turn(
        cid,
        play,
        "c",
        "wait",
        wait_trigger={**declaration, "item_id": "sword-c", "zone": ((1, -1),)},
    )
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={**declaration, "item_id": "sword-a", "zone": ((1, -3),)},
    )
    play.rng = RecordedDice(())
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
    assert paused.encounters[0].wait_interrupt.waiter_id == "c"
    assert paused.encounters[0].participants[1].position == Hex(q=1, r=-1)
    await turn(cid, play, "c", "do_nothing")
    state = play._load(await play.store.read(cid))
    resume = ResumeInterruptedTurn(
        id="resume-first", actor_id="b", expected_revision=state.revision, encounter_id="fight"
    )
    await CombatService(play).execute(cid, resume, principal_id="b")
    paused = play._load(await play.store.read(cid))
    assert paused.encounters[0].wait_interrupt is not None
    assert paused.encounters[0].wait_interrupt.waiter_id == "a"
    assert paused.encounters[0].participants[1].position == Hex(q=1, r=-3)
    assert paused.encounters[0].participants[1].high_speed == HighSpeedState(
        velocity=6, direction=4, remaining_yards=2, entry_turns=0
    )
    await turn(cid, play, "a", "do_nothing")
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        resume.model_copy(update={"id": "resume-last", "expected_revision": state.revision}),
        principal_id="b",
    )
    done = play._load(await play.store.read(cid))
    assert done.encounters[0].wait_interrupt is None
    assert done.encounters[0].participants[1].position == Hex(q=1, r=-5)
    assert done.encounters[0].participants[1].high_speed == HighSpeedState(velocity=6, direction=4)
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("entering", [False, True])
async def test_wait_reaction_knockdown_retires_unspent_trajectory(
    tmp_path: Path, entering: bool
) -> None:
    """B385 permits continuation only if the mover remains standing after the blow."""
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
    await turn(
        cid,
        play,
        "b",
        "move",
        enter_high_speed=entering,
        hex_path=tuple({"q": 1, "r": -n} for n in range(1, (5 if entering else 6) + 1)),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    # Ordinary hit, 1d+1 cutting = 7 raw, 10 injury; failed HT by 5 causes unconsciousness.
    play.rng = RecordedDice((3, 3, 3, 6, 5, 5, 5))
    await defend(cid, play, "b")
    injured = play._load(await play.store.read(cid))
    hp = next(pool for pool in injured.resources.pools if pool.id == "hp:b")
    assert hp.current == 0 and hp.injury and hp.injury.incapacitated
    assert injured.encounters[0].participants[1].posture == "prone"
    assert play.rng.exhausted()
    resume = ResumeInterruptedTurn(
        id="resume-injured", actor_id="b", expected_revision=injured.revision, encounter_id="fight"
    )
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    result = await CombatService(restarted).execute(cid, resume, principal_id="b")
    after = restarted._load(await restarted.store.read(cid))
    assert after.encounters[0].participants[1].position == Hex(q=1, r=-1)
    assert after.encounters[0].participants[1].high_speed is None
    assert after.encounters[0].wait_interrupt is None
    assert next(pool for pool in after.resources.pools if pool.id == "hp:b") == hp.model_copy(
        update={"injury": hp.injury.model_copy(update={"phase": "between"})}
    )
    assert await CombatService(restarted).execute(cid, resume, principal_id="b") == result
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("entering", [False, True])
@pytest.mark.parametrize("checkpoint", [1, 3])
async def test_wait_preserves_turning_before_and_after_checkpoint(
    tmp_path: Path, entering: bool, checkpoint: int
) -> None:
    """B394: one entry turn; later velocity 6 / Basic Move 5 has a one-yard radius."""
    cid, play = await course(
        tmp_path, high_speed=None if entering else HighSpeedState(velocity=6, direction=4)
    )
    path = (Hex(q=1, r=-1), Hex(q=1, r=-2)) + tuple(
        Hex(q=n - 1, r=-n) for n in range(3, (5 if entering else 6) + 1)
    )
    trigger = path[checkpoint - 1]
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "move",
            "zone": ((trigger.q, trigger.r),),
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    play.rng = RecordedDice(())
    await turn(
        cid,
        play,
        "b",
        "move",
        enter_high_speed=entering,
        hex_path=tuple(point.model_dump() for point in path),
    )
    paused = play._load(await play.store.read(cid))
    mover = paused.encounters[0].participants[1]
    assert mover.position == trigger
    assert mover.high_speed == HighSpeedState(
        velocity=6,
        direction=4 if checkpoint == 1 else 5,
        remaining_yards=len(path) - checkpoint,
        entry_turns=(0 if checkpoint == 1 else 1) if entering else None,
        straight_yards=0 if entering else 1,
    )
    await turn(cid, play, "a", "do_nothing")
    state = play._load(await play.store.read(cid))
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    command = ResumeInterruptedTurn(
        id="resume-curved", actor_id="b", expected_revision=state.revision, encounter_id="fight"
    )
    result = await CombatService(restarted).execute(cid, command, principal_id="b")
    done = restarted._load(await restarted.store.read(cid))
    assert done.encounters[0].participants[1].position == path[-1]
    assert done.encounters[0].participants[1].high_speed == HighSpeedState(
        velocity=6, direction=5, straight_yards=0 if entering else 4
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert restarted._load(await restarted.store.read(cid)) == done
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("entering", [False, True])
async def test_move_and_attack_waits_during_movement_before_attack(
    tmp_path: Path, entering: bool
) -> None:
    """B394 allows Move and Attack; B385 Wait interrupts its movement as well."""
    distance = 5 if entering else 6
    cid, play = await course(
        tmp_path,
        high_speed=None if entering else HighSpeedState(velocity=6, direction=4),
        third_position=Hex(q=1, r=-distance - 1),
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
    play.rng = RecordedDice(())
    await turn(
        cid,
        play,
        "b",
        "move_and_attack",
        enter_high_speed=entering,
        item_id="sword-b",
        target_id="c",
        mode_id="swing",
        hex_path=tuple({"q": 1, "r": -n} for n in range(1, distance + 1)),
    )
    paused = play._load(await play.store.read(cid))
    assert paused.encounters[0].wait_interrupt is not None
    assert paused.encounters[0].pending_defense is None
    assert paused.encounters[0].participants[1].position == Hex(q=1, r=-1)
    await turn(cid, play, "a", "do_nothing")
    state = play._load(await play.store.read(cid))
    resumed = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    resumed.rng = RecordedDice(())
    command = ResumeInterruptedTurn(
        id="resume-attack", actor_id="b", expected_revision=state.revision, encounter_id="fight"
    )
    result = await CombatService(resumed).execute(cid, command, principal_id="b")
    ready = resumed._load(await resumed.store.read(cid))
    assert ready.encounters[0].wait_interrupt is None
    assert ready.encounters[0].participants[1].position == Hex(q=1, r=-distance)
    assert ready.encounters[0].pending_defense is not None
    assert ready.encounters[0].pending_defense.defender_id == "c"
    resumed.rng = RecordedDice((5, 5, 5))
    await defend(cid, resumed, "c")
    after = resumed._load(await resumed.store.read(cid))
    assert after.encounters[0].pending_defense is None
    assert after.encounters[0].participants[1].high_speed == HighSpeedState(
        velocity=6, direction=4, straight_yards=0 if entering else 6
    )
    resumed.rng = RecordedDice(())
    assert await CombatService(resumed).execute(cid, command, principal_id="b") == result
    assert resumed._load(await resumed.store.read(cid)) == after
    assert await resumed.store.read(cid) == await resumed.store.replay(cid)


@pytest.mark.parametrize("revealed", [False, True])
async def test_movement_wait_uses_first_observable_zone_entry(
    tmp_path: Path, revealed: bool
) -> None:
    """A covered zone behind an opaque column cannot trigger until it is visible."""
    cid, play = await course(tmp_path, opaque_first=True)
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "move",
            "zone": ((1, -1), (1, -3)) if revealed else ((1, -1),),
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    play.rng = RecordedDice(())
    await turn(
        cid,
        play,
        "b",
        "move",
        enter_high_speed=True,
        hex_path=tuple({"q": 1, "r": -n} for n in range(1, 6)),
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[1].position == Hex(q=1, r=-3 if revealed else -5)
    assert (state.encounters[0].wait_interrupt is not None) is revealed
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)
