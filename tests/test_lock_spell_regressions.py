"""Persisted regression oracles from independent B236/B383 lock review.
Fixture injuries and locations represent prior authoritative facts.
"""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.lock_state import (
    LockFixture,
    LockState,
    latest,
    passage_blocked,
    save,
    validate_fixture,
)
from wayfarer.engine.simulation.resources import ResourceState


@pytest.mark.parametrize("hand", [None, "right-hand"])
async def test_one_hand_ready(tmp_path: Path, hand: Literal["right-hand"] | None) -> None:
    from test_lock_spell_combat import cast_in_combat, prepare_fight

    from wayfarer.engine.rules.types.location import HumanBody, LastingInjury
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn

    cid, play = await prepare_fight(tmp_path, "sqlite")
    await cast_in_combat(cid, play, spell="lockmaster", turns=10, dice=(3, 3, 3))
    before = play._load(await play.store.read(cid))
    hp = next(p for p in before.resources.pools if p.id == "hp:a")
    assert hp.injury is not None
    injured = hp.model_copy(
        update={
            "injury": hp.injury.model_copy(
                update={
                    "anatomy": "human",
                    "lasting_injuries": (
                        LastingInjury(
                            id="old-hand",
                            location="left-hand",
                            kind="crippled",
                            duration="permanent",
                            inflicted_at=0,
                            injury=6,
                        ),
                    ),
                }
            )
        }
    )

    def seed_injury(campaign: Campaign) -> CommandReceipt:
        updated = before.model_copy(
            update={
                "revision": before.revision + 1,
                "actors": tuple(
                    a.model_copy(update={"body": HumanBody(anatomy="human")})
                    if a.actor_id == "a"
                    else a
                    for a in before.actors
                ),
                "resources": before.resources.model_copy(
                    update={
                        "revision": before.revision + 1,
                        "pools": tuple(
                            injured if p.id == hp.id else p for p in before.resources.pools
                        ),
                    }
                ),
            }
        )
        play.commit(campaign, updated)
        return CommandReceipt(action="resource", outcome="fixture.injury")

    await play.store.commit_turn(
        cid, "injury", before.revision, "injury", seed_injury, actor_id="gm"
    )
    result = await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="open",
            actor_id="a",
            expected_revision=before.revision + 1,
            encounter_id="fight",
            maneuver="ready",
            target_id="chest",
            ready_hand=hand,
        ),
        principal_id="a",
    )
    assert result.code == "combat.ready"
    after = await play.store.read(cid)
    assert not latest(play._load(after).resources)["chest"].closed
    assert after == await play.store.replay(cid)


def test_retching_legacy_light_interrupts_without_unbound_check() -> None:
    from test_spells import command, context, state

    from wayfarer.engine.rules.fright import FrightEffect
    from wayfarer.engine.simulation.health.fright import apply_effect
    from wayfarer.engine.simulation.magic.spells import apply_spell

    resources, _ = apply_spell(state(), command(), context(), rng=RecordedDice(()), system=True)
    resources = resources.model_copy(update={"game_time": 1})
    resources = apply_effect(
        resources,
        FrightEffect(table_total=12, condition="retching", duration_seconds=15),
        actor_id="a",
        trigger_id="fright",
        command_id="retch",
        ht=10,
        will=12,
        modified_will=12,
        rng=RecordedDice(()),
    )
    _, result = apply_spell(
        resources,
        command(resources.revision, kind="complete"),
        context(),
        rng=RecordedDice(()),
        system=True,
    )
    assert result.outcome == "interrupted" and result.energy_spent == 0


async def test_cancel_old_noncombat_ward_after_new_encounter(tmp_path: Path) -> None:
    from test_lock_spell_persistence import cast, declare, prepare, revision

    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.orchestration.combat import CombatService, StartEncounter
    from wayfarer.orchestration.locks import LockSpellService

    cid, play = await prepare(tmp_path, combat=True)
    await declare(play, cid)
    _, completion = await cast(play, cid, "magelock", "ward")
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=3, y=1)),
            ),
        ),
        principal_id="gm",
    )
    result = await LockSpellService(play).execute(
        cid,
        completion.model_copy(
            update={
                "id": "cancel",
                "kind": "cancel",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="gm",
    )
    assert result.outcome == "cancelled" and result.energy_spent == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_destroy_retrieve_consume_restore_keeps_passage_open() -> None:
    from test_objects import fixture

    from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
    from wayfarer.engine.simulation.equipment.world_ground import (
        WorldGroundCommand,
        apply_world_ground,
    )
    from wayfarer.engine.simulation.resources import Consume
    from wayfarer.engine.world import Connection, Entity, EntityKind, World
    from wayfarer.errors import ConflictError, ValidationError

    engine, resources = fixture()
    world = World(
        entities=(
            Entity("a", EntityKind.ACTOR, "A", "hall"),
            Entity("b", EntityKind.ACTOR, "B", "room"),
            Entity("hall", EntityKind.LOCATION, "Hall"),
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("door", EntityKind.OBJECT, "Door", "hall"),
            Entity("other-door", EntityKind.OBJECT, "Other", "hall"),
        ),
        connections=(Connection("hall", "room", "doorway"),),
    )
    engine = engine.for_world(world)
    resources = resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(
                    update={"world_ground_location_id": "hall", "ready": False, "equipped": False}
                )
                if i.id == "sword"
                else i
                for i in resources.items
            )
        }
    )
    door = LockFixture(
        object_id="door", location_id="hall", kind="door", item_id="sword", passage=("hall", "room")
    )
    validate_fixture(world, resources, door, declaring=True)
    resources = save(resources, LockState(fixture=door, locked=True, closed=True), "bind")
    with pytest.raises(ValidationError, match="two distinct"):
        validate_fixture(
            world, resources, door.model_copy(update={"object_id": "other-door"}), declaring=True
        )
    retrieval = WorldGroundCommand(
        id="retrieve",
        actor_id="a",
        expected_revision=resources.revision,
        kind="retrieve",
        item_id="sword",
    )
    with pytest.raises(ConflictError, match="intact fixed door"):
        apply_world_ground(
            resources, world, engine, retrieval, authorized_actor_id="a", system=True
        )
    resources, result = apply_object(
        engine,
        resources,
        DamageObject(
            id="destroy",
            actor_id="a",
            expected_revision=resources.revision,
            item_id="sword",
            basic_damage=100,
            damage_type="cr",
        ),
        system=True,
        rng=RecordedDice(()),
    )
    assert result.condition.destroyed
    resources, _ = apply_world_ground(
        resources,
        world,
        engine,
        retrieval.model_copy(update={"expected_revision": resources.revision}),
        authorized_actor_id="a",
        system=True,
    )
    resources = engine.apply(
        resources,
        Consume(
            id="dispose",
            actor_id="a",
            expected_revision=resources.revision,
            item_id="sword",
            quantity=1,
        ),
    )
    restored = ResourceState.model_validate_json(resources.model_dump_json())
    engine.validate(restored)
    validate_fixture(world, restored, door)
    assert not passage_blocked(restored, "hall", "room")
    assert not any(i.id == "sword" for i in restored.items)
    with pytest.raises(ValidationError):
        validate_fixture(world, restored, door, declaring=True)


async def test_failed_salvage_is_terminal_and_remote_actor_is_rejected(tmp_path: Path) -> None:
    from test_gurps_melee import setup

    from wayfarer.engine.rules.types.object import ObjectProfile, SalvageProfile
    from wayfarer.engine.simulation.equipment.salvage import salvage
    from wayfarer.errors import ValidationError

    profile = ObjectProfile(
        construction="homogenous",
        hp=12,
        dr=6,
        ht=12,
        repair_skill_id="skill:armoury",
        repair_tools_definition="equipment:armoury-tools",
        repair_parts_definition="equipment:spare-parts",
        salvage=SalvageProfile(
            skill_id="skill:armoury",
            tools_definition_id="equipment:armoury-tools",
            recovered_definition_id="equipment:spare-parts",
            seconds=600,
            disabled_quantity=3,
            destroyed_quantity=1,
        ),
    )
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", durability=profile, object_hp=1, start_encounter=False
    )
    state = play._load(await play.store.read(cid))
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"world_ground_location_id": "dock"})
                if i.id == "sword-b"
                else i
                for i in state.resources.items
            )
        }
    )
    door = LockFixture(
        object_id="chest",
        location_id="dock",
        kind="door",
        item_id="sword-b",
        passage=("dock", "alley"),
    )
    validate_fixture(state.world, resources, door, declaring=True)
    state = state.model_copy(
        update={
            "resources": save(resources, LockState(fixture=door, locked=True, closed=True), "bind")
        }
    )

    def away(s: PlayState) -> PlayState:
        return s.model_copy(
            update={
                "world": replace(
                    s.world,
                    entities=tuple(
                        replace(e, location_id="alley") if e.id == "b" else e
                        for e in s.world.entities
                    ),
                )
            }
        )

    args = dict(actor_id="b", item_id="sword-b")
    with pytest.raises(ValidationError):
        salvage(
            play.rules_context, away(state), **args, command_id="start", stage="start", task_id=None
        )
    started, task = salvage(
        play.rules_context, state, **args, command_id="start", stage="start", task_id=None
    )
    due = started.model_copy(
        update={"resources": started.resources.model_copy(update={"game_time": task.due})}
    )
    with pytest.raises(ValidationError):
        salvage(
            play.rules_context,
            away(due),
            **args,
            command_id="finish",
            stage="finish",
            task_id="start",
        )
    _, cancelled = salvage(
        play.rules_context, away(due), **args, command_id="cancel", stage="cancel", task_id="start"
    )
    assert cancelled.status == "cancelled"
    play.rng = RecordedDice((6, 6, 6))
    after, task = salvage(
        play.rules_context, due, **args, command_id="finish", stage="finish", task_id="start"
    )
    assert (
        task.status == "completed" and task.recovered_quantity == 0 and not task.condition.destroyed
    )
    restored = ResourceState.model_validate_json(after.resources.model_dump_json())
    validate_fixture(state.world, restored, door)
    assert not passage_blocked(restored, "dock", "alley")
    assert not any(i.id == "sword-b" for i in restored.items)
