"""Reducer seams run without committing and preserve their supplied checkpoints."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from test_combat import setup as combat_setup
from test_combat import start
from test_medical_service import setup as medical_setup
from test_spell_bindings import command as spell_command
from test_spell_bindings import setup as spell_setup
from test_spell_bindings import start_fight
from test_wave12 import ready, service

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat import GridPoint
from wayfarer.engine.simulation.setup import SetupCommand
from wayfarer.orchestration.combat import CombatContext, TakeCombatTurn, reduce_combat
from wayfarer.orchestration.physical import (
    PhysicalCommand,
    PhysicalContext,
    PhysicalRoute,
    reduce_physical,
)
from wayfarer.orchestration.setup import SetupContext, reduce_setup
from wayfarer.orchestration.spells import SpellExecutionContext, reduce_spell


async def test_physical_step_preserves_check_order_without_committing(tmp_path: Path) -> None:
    cid, play, _ = await medical_setup(tmp_path)
    campaign = await play.store.read(cid)
    before = play._load(campaign)
    encoded = before.model_dump_json()
    scene = next(e.location_id for e in before.world.entities if e.id == "a")
    assert scene is not None
    route = PhysicalRoute(id="swim", scene_id=scene, kind="swim", seconds=60)
    command = PhysicalCommand(
        id="swim", actor_id="a", expected_revision=0, kind="swim", route_id="swim"
    )
    dice = RecordedDice([1, 2, 3, 4, 3, 2])
    play.rng = dice
    context = PhysicalContext(play.rules_context, lambda *_: route, dice)
    with (
        patch.object(play, "checkpoint", side_effect=AssertionError("reducer checkpointed")),
        patch.object(play, "commit", side_effect=AssertionError("reducer committed")),
    ):
        updated, result = reduce_physical(before, command, context)
    assert before.model_dump_json() == encoded
    assert await play.store.read(cid) == campaign
    assert updated.revision == 1 and updated.resources.game_time == 60
    assert result.succeeded and result.fp_lost == 0
    assert [check.dice for check in result.checks] == [(1, 2, 3), (4, 3, 2)]
    assert dice.exhausted()


async def test_combat_steps_compose_without_a_transaction(tmp_path: Path) -> None:
    cid, play, _ = await combat_setup(tmp_path)
    campaign = await play.store.read(cid)
    before = play._load(campaign)
    encoded = before.model_dump_json()
    with (
        patch.object(play, "checkpoint", side_effect=AssertionError("reducer checkpointed")),
        patch.object(play, "commit", side_effect=AssertionError("reducer committed")),
    ):
        started, result = reduce_combat(before, start(), CombatContext(play, before))
        assert result.code == "combat.started"
        started_json = started.model_dump_json()
        command = TakeCombatTurn(
            id="move",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=1, y=0),
        )
        moved, result = reduce_combat(started, command, CombatContext(play, started))
    assert before.model_dump_json() == encoded
    assert started.model_dump_json() == started_json
    assert await play.store.read(cid) == campaign
    assert moved.revision == 2 and result.current_actor_id == "b"
    assert moved.encounters[0].participants[0].position == GridPoint(x=1, y=0)


async def test_setup_activation_returns_an_independent_campaign(tmp_path: Path) -> None:
    setup = service(tmp_path)
    cid = await ready(setup)
    campaign = await setup.play.store.read(cid)
    original = deepcopy(campaign)
    command = SetupCommand(id="activate", expected_revision=3, operation="activate")
    updated, result = reduce_setup(campaign, command, SetupContext(setup.play, "alice"))
    assert result.phase == "active" and updated["revision"] == 4
    assert "play_json" in updated and "play_json" not in campaign
    assert campaign == original == await setup.play.store.read(cid)
    updated["scenario"]["title"] = "Independent result"
    assert campaign == original


async def test_spell_step_returns_the_completed_maneuver_result(tmp_path: Path) -> None:
    cid, play = await spell_setup(tmp_path, combat=True, execution_version=2)
    await start_fight(cid, play)
    campaign = await play.store.read(cid)
    before = play._load(campaign)
    dice = RecordedDice([3, 3, 3])
    play.rng = dice
    updated, result = reduce_spell(
        before, spell_command(1), SpellExecutionContext(play.rules_context)
    )
    assert result.outcome == "active"
    assert updated.encounters[0].current_actor_id == "b"
    assert dice.exhausted()
    assert await play.store.read(cid) == campaign
