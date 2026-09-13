"""Independent Campaigns B385-B391 tactical-combat expectations for #507."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_hex_geometry import board, h
from test_tactical import migration
from test_tactical import setup as tactical_setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.tactical import attack_approach
from wayfarer.engine.simulation.hex_geometry import Occupant, Pose, movement, pop_up_movement
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn


def test_b387_allied_hex_is_an_obstruction_but_not_a_barrier() -> None:
    ally = Occupant(actor_id="ally", position=h(1, 0), relation="ally")
    result = movement(
        board(),
        Pose(position=h(0, 0), facing=0),
        (h(1, 0), h(2, 0)),
        move=3,
        occupants=(ally,),
    )
    assert result.cost == 3
    assert result.destination.position == h(2, 0)

    enemy = ally.model_copy(update={"relation": "enemy"})
    with pytest.raises(ValidationError, match="close-combat"):
        movement(
            board(),
            Pose(position=h(0, 0), facing=0),
            (h(1, 0), h(2, 0)),
            move=5,
            occupants=(enemy,),
        )


def test_b390_pop_up_is_one_atomic_exposure_and_return() -> None:
    origin = Pose(position=h(0, 0), facing=0)
    result = pop_up_movement(
        board(), origin, (h(1, 0), h(0, 0)), move=5, exposure_facing=5
    )
    assert result.exposure == Pose(position=h(1, 0), facing=5)
    assert result.destination == Pose(position=h(0, 0), facing=5)
    with pytest.raises(ValidationError, match="exposed hex"):
        pop_up_movement(board(), origin, (h(1, 0), h(2, 0)), move=5)


def test_b391_runaround_preserves_awareness_from_the_start_of_the_path() -> None:
    defender = Pose(position=h(0, 0), facing=0)
    assert attack_approach(defender, Pose(position=h(-1, 0), facing=0)) == "rear"
    assert (
        attack_approach(
            defender,
            Pose(position=h(-1, 0), facing=0),
            origin=Pose(position=h(1, 0), facing=3),
        )
        == "runaround"
    )
    assert attack_approach(defender, Pose(position=h(-1, 1), facing=0)) == "side"


async def test_wait_interrupt_stops_on_the_first_matching_hex(tmp_path: Path) -> None:
    cid, play = await tactical_setup(tmp_path)
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
    result = await turn(
        cid,
        play,
        "b",
        "move",
        hex_path=({"q": 1, "r": -1}, {"q": 2, "r": -1}),
    )
    assert result.code == "combat.wait_triggered"
    encounter = play._load(await play.store.read(cid)).encounters[0]
    assert encounter.participants[1].position == h(1, -1)
    assert encounter.wait_interrupt is not None
    assert '"hex_path":[{"q":2,"r":-1}]' in encounter.wait_interrupt.command_json
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
    )
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        ResumeInterruptedTurn(
            id="resume-movement",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
        ),
        authenticated_actor_id="b",
    )
    resumed = play._load(await play.store.read(cid)).encounters[0]
    assert resumed.participants[1].position == h(2, -1)


async def test_pop_up_uses_exposure_geometry_returns_to_cover_and_applies_penalty(
    tmp_path: Path,
) -> None:
    cid, play = await tactical_setup(tmp_path, migrate=False)
    command = migration()
    battlefield = command.battlefield.model_copy(
        update={
            "cells": tuple(
                cell.model_copy(update={"opaque_height": 2})
                if cell.position == h(1, -1)
                else cell
                for cell in command.battlefield.cells
            )
        }
    )
    placements = tuple(
        placement.model_copy(
            update={"pose": placement.pose.model_copy(update={"position": h(2, -1)})}
        )
        if placement.actor_id == "b"
        else placement
        for placement in command.placements
    )
    await CombatService(play).execute(
        cid,
        command.model_copy(update={"battlefield": battlefield, "placements": placements}),
        authenticated_actor_id="gm",
    )
    play = play.for_campaign(await play.store.read(cid))
    state = play._load(await play.store.read(cid))
    attack = TakeCombatTurn(
        id="pop-up",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        mode_id="throw-fixture",
        target_id="b",
        pop_up=True,
        hex_path=(h(1, 0), h(0, 0)),
    )
    result = await CombatService(play).execute(cid, attack, authenticated_actor_id="a")
    assert result.code == "combat.defense_required"
    pending = play._load(await play.store.read(cid)).encounters[0]
    assert pending.participants[0].position == h(0, 0)
    assert pending.pending_defense is not None
    assert pending.pending_defense.tactical_approach == "pop-up"
    assert pending.pending_defense.tactical_attack_pose == Pose(position=h(1, 0), facing=0)

    play.rng = RecordedDice((3, 4, 4, 2))
    resolved = await defend(cid, play, "b")
    assert resolved.injury is not None and resolved.injury.attack is not None
    assert resolved.injury.attack.effective_target == 11
    assert play._load(await play.store.read(cid)).encounters[0].participants[0].position == h(0, 0)
