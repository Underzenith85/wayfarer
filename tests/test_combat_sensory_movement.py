"""Executed paths retire sensory proof; previews and unspent paths do not."""

import json
from pathlib import Path

import pytest
from test_combat_sensory_authority import declaration, prepare

from wayfarer.engine.rules.types.tactical import HighSpeedState
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.combat.maneuvers import WaitTrigger
from wayfarer.engine.simulation.combat.sensory_state import (
    evidence,
    history,
    invalidate_movement,
    invalidations,
)
from wayfarer.engine.simulation.combat.tactical_transitions import finish_defense_with_movement
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.combat_senses import CombatSensesService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_closed_path_retires_proof_and_exact_retry_does_not_repeat_it(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play = await prepare(tmp_path, backend, "hex")
    await CombatSensesService(play).execute(cid, declaration(), principal_id="gm")
    initial = play._load(await play.store.read(cid))
    command = TakeCombatTurn(
        id="loop",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="move",
        hex_path=(Hex(q=0, r=1), Hex(q=0, r=0)),
    )
    engine = play.engine.combat
    assert engine is not None
    _, preview_resources, _ = engine.take_turn(
        initial.encounters[0],
        actor_id="a",
        maneuver="move",
        command_id="preview",
        resources=initial.resources,
        hex_path=command.hex_path,
        spatial_revision=3,
    )
    assert len(invalidations(preview_resources)) == 1
    assert not invalidations(initial.resources)
    assert await play.store.read(cid) == await play.store.replay(cid)
    result = await CombatService(play).execute(cid, command, principal_id="a")
    final = play._load(await play.store.read(cid))
    assert final.encounters[0].participants[0].position == Hex(q=0, r=0)
    assert evidence(final, final.encounters[0], "b", "a") is None
    assert len(invalidations(final.resources)) == 1
    assert await CombatService(play).execute(cid, command, principal_id="a") == result
    assert play._load(await play.store.read(cid)) == final


@pytest.mark.parametrize("returning_prefix", [False, True])
async def test_wait_records_only_accepted_prefix_and_consumes_a_closed_prefix(
    tmp_path: Path,
    returning_prefix: bool,
) -> None:
    cid, play = await prepare(tmp_path, "sqlite", "hex")
    await CombatSensesService(play).execute(cid, declaration(), principal_id="gm")
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    engine = play.engine.combat
    assert engine is not None
    waiter = encounter.participants[1]
    trigger = WaitTrigger(
        actor_id="a",
        action="move",
        zone=((0, 0) if returning_prefix else (0, 1),),
        reaction="attack",
        item_id="sword-b",
        reaction_target_id="a",
        mode_id="swing",
    )
    encounter = engine._replace(
        encounter,
        waiter.model_copy(
            update={
                "maneuver_state": waiter.maneuver_state.model_copy(update={"wait": trigger}),
            }
        ),
    )
    command = TakeCombatTurn(
        id="wait-loop",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="move",
        hex_path=(Hex(q=0, r=1), Hex(q=0, r=0)),
    )
    paused, resources, result = engine.take_turn(
        encounter,
        actor_id="a",
        maneuver="move",
        resources=state.resources,
        command_id=command.id,
        command_json=command.model_dump_json(),
        hex_path=command.hex_path,
        spatial_revision=3,
    )
    assert result.code == "combat.wait_triggered"
    assert paused.participants[0].position == Hex(q=0, r=0 if returning_prefix else 1)
    assert len(invalidations(resources)) == 1
    assert not invalidations(state.resources)
    assert paused.wait_interrupt is not None
    saved = TakeCombatTurn.model_validate_json(paused.wait_interrupt.command_json)
    assert saved.hex_path == (() if returning_prefix else (Hex(q=0, r=0),))
    assert saved.maneuver == ("do_nothing" if returning_prefix else "move")


async def test_wait_at_existing_high_speed_checkpoint_does_not_record_unspent_movement(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path, "sqlite", "hex")
    await CombatSensesService(play).execute(cid, declaration(), principal_id="gm")
    state = play._load(await play.store.read(cid))
    engine = play.engine.combat
    assert engine is not None
    encounter = state.encounters[0]
    actor, waiter = encounter.participants
    actor = actor.model_copy(
        update={
            "high_speed": HighSpeedState(
                velocity=6,
                direction=1,
                remaining_yards=2,
            )
        }
    )
    waiter = waiter.model_copy(
        update={
            "maneuver_state": waiter.maneuver_state.model_copy(
                update={
                    "wait": WaitTrigger(
                        actor_id="a",
                        action="move",
                        zone=((0, 0),),
                        reaction="attack",
                        item_id="sword-b",
                        reaction_target_id="a",
                        mode_id="swing",
                    ),
                }
            )
        }
    )
    encounter = engine._replace(engine._replace(encounter, actor), waiter)
    command = TakeCombatTurn(
        id="zero-prefix",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="move",
        hex_path=(Hex(q=0, r=1), Hex(q=0, r=2)),
    )
    paused, resources, result = engine.take_turn(
        encounter,
        actor_id="a",
        maneuver="move",
        resources=state.resources,
        command_id=command.id,
        command_json=command.model_dump_json(),
        hex_path=command.hex_path,
        spatial_revision=3,
    )
    assert result.code == "combat.wait_triggered"
    assert paused.participants[0].position == Hex(q=0, r=0)
    assert resources == state.resources
    assert paused.wait_interrupt is not None
    assert json.loads(paused.wait_interrupt.command_json)["hex_path"] == [
        {"q": 0, "r": 1},
        {"q": 0, "r": 2},
    ]


@pytest.mark.parametrize("path", [(), (Hex(q=0, r=1), Hex(q=0, r=0))])
async def test_deferred_path_reports_motion_only_after_successful_execution(
    tmp_path: Path,
    path: tuple[Hex, ...],
) -> None:
    cid, play = await prepare(tmp_path, "sqlite", "hex")
    await CombatSensesService(play).execute(cid, declaration(), principal_id="gm")
    state = play._load(await play.store.read(cid))
    engine = play.engine.combat
    assert engine is not None
    encounter = state.encounters[0]
    attacker = encounter.participants[0].model_copy(update={"movement_allowance": 20})
    encounter = engine._replace(encounter, attacker)
    declared, resources, _ = engine.take_turn(
        encounter,
        actor_id="a",
        maneuver="attack",
        resources=state.resources,
        command_id="deferred",
        item_id="sword-a",
        target_id="b",
        step_timing="after",
        hex_path=path,
        hex_facing=1,
        spatial_revision=3,
    )
    assert not invalidations(resources)
    assert declared.participants[0].position == attacker.position
    accepted, _ = engine.choose_defense(declared, actor_id="b", selected="none")
    final, moved = finish_defense_with_movement(
        play.rules_context,
        state,
        accepted,
        ChooseDefense(
            id="defense",
            actor_id="b",
            expected_revision=3,
            encounter_id="fight",
            defense="none",
        ),
    )
    assert final.participants[0].position == attacker.position
    assert moved == (frozenset({"a"}) if path else frozenset())
    resources = invalidate_movement(resources, final.id, moved, revision=4)
    assert len(invalidations(resources)) == int(bool(path))


async def test_invalid_and_facing_only_paths_do_not_invalidate(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, "sqlite", "hex")
    await CombatSensesService(play).execute(cid, declaration(), principal_id="gm")
    state = play._load(await play.store.read(cid))
    engine = play.engine.combat
    assert engine is not None
    with pytest.raises(ValidationError, match="adjacent"):
        engine.take_turn(
            state.encounters[0],
            actor_id="a",
            maneuver="move",
            command_id="invalid",
            resources=state.resources,
            hex_path=(Hex(q=0, r=2),),
            spatial_revision=3,
        )
    _, resources, _ = engine.take_turn(
        state.encounters[0],
        actor_id="a",
        maneuver="move",
        command_id="face",
        resources=state.resources,
        hex_facing=1,
        spatial_revision=3,
    )
    assert resources == state.resources


async def test_movement_helper_leaves_unrelated_pairs_encounters_and_events_unchanged(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path, "sqlite", "hex")
    result = await CombatSensesService(play).execute(cid, declaration(), principal_id="gm")
    assert result is not None
    state = play._load(await play.store.read(cid))
    unrelated = result.model_copy(
        update={"id": "combat-sense:unrelated", "observer_id": "c", "target_id": "d"}
    )
    elsewhere = result.model_copy(
        update={"id": "combat-sense:elsewhere", "encounter_id": "other-fight"}
    )
    spectator = ResourceEvent(id="spectator-note", at=0, target_id="spectator", kind="private note")
    resources = state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=unrelated.id, at=0, target_id="c", kind=unrelated.model_dump_json()
                ),
                ResourceEvent(
                    id=elsewhere.id, at=0, target_id="b", kind=elsewhere.model_dump_json()
                ),
                spectator,
            )
        }
    )
    updated = invalidate_movement(resources, "fight", frozenset({"a"}), revision=3)
    assert tuple(e for e in updated.events if e in resources.events) == resources.events
    assert history(updated) == history(resources)
    assert [i.evidence_id for i in invalidations(updated)] == [result.id]
    assert invalidate_movement(updated, "fight", frozenset({"a"}), revision=4) == updated
