"""Private operating reservations and native interruption arithmetic, not live-host certification."""

from types import SimpleNamespace
from typing import cast

import pytest
from test_armoury_alternative_tools import alternative
from test_artifacts import configured as artifact_engine
from test_artifacts import known_state
from test_objects import fixture as object_fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectResult
from wayfarer.engine.simulation.equipment.artifacts import OperateArtifact
from wayfarer.engine.simulation.equipment.general import (
    UseGeneralEquipment,
    apply_general_equipment,
)
from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
from wayfarer.engine.simulation.equipment.repair_parts import digest
from wayfarer.engine.simulation.equipment.repair_tool_operation import (
    PREFIX,
    current,
    prepare,
    record,
    require_selection,
    reserved,
    settle,
)
from wayfarer.engine.simulation.equipment.repairs import RepairTask
from wayfarer.engine.simulation.equipment.repairs import record as record_task
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import EquipmentSpec, Item, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def operation_fixture() -> tuple[ResourceState, RepairTask]:
    _, resources = object_fixture()
    resources = resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"charges": 3600}) if item.id == "sword" else item
                for item in resources.items
            )
        }
    )
    task = RepairTask(
        id="repair",
        actor_id="a",
        item_id="bag",
        tool_id="sword",
        start=0,
        due=1800,
        skill=12,
        condition=ObjectCondition(hp=0),
    )
    entry = alternative("skill:armoury-body-armor", duration_seconds=3600).model_copy(
        update={"definition_id": "sword", "weight_millipounds": 5}
    )
    tool = next(item for item in resources.items if item.id == "sword")
    value = prepare(entry, tool, "a", "repair", "bag", 0, 1800, "skill:armoury-body-armor")
    assert value
    return record_task(record(resources, value, False), task, "repair"), task


@pytest.mark.parametrize(
    "problem",
    ["duplicate", "target", "time", "boolean-charge", "float-charge", "boolean-duration", "digest"],
)
def test_operation_reader_rejects_forged_or_duplicate_identity(problem: str) -> None:
    resources, task = operation_fixture()
    event = next(event for event in resources.events if event.id.startswith(PREFIX))
    if problem == "duplicate":
        altered = resources.model_copy(update={"events": resources.events + (event,)})
    else:
        if problem in {"boolean-charge", "float-charge", "boolean-duration"}:
            old = '"duration_seconds":1800' if problem == "boolean-duration" else '"charges":3600'
            new = (
                '"duration_seconds":true'
                if problem == "boolean-duration"
                else '"charges":3600.0'
                if problem == "float-charge"
                else '"charges":true'
            )
            value = event.kind.replace(old, new)
            event = event.model_copy(update={"kind": value})
        elif problem == "digest":
            event = event.model_copy(update={"id": PREFIX + "wrong"})
        else:
            event = event.model_copy(
                update={"target_id": "b"} if problem == "target" else {"at": 1}
            )
        altered = resources.model_copy(
            update={
                "events": tuple(
                    event if old.id.startswith(PREFIX) else old for old in resources.events
                )
            }
        )
    with pytest.raises(ValidationError, match="operation"):
        current(altered, task)


def test_native_destroyed_kit_stops_active_time_and_cancel_can_release_reservation() -> None:
    engine, _ = object_fixture()
    resources, task = operation_fixture()
    original = engine.specs["sword"]
    entry = alternative("skill:armoury-body-armor", duration_seconds=3600).model_copy(
        update={
            "definition_id": "sword",
            "weight_millipounds": 5,
            "durability": original.durability,
        }
    )
    # This unit fixture explicitly admits the durable, internally charged kit.
    operation = current(resources, task)
    assert operation
    resources = resources.model_copy(
        update={
            "events": tuple(
                event.model_copy(
                    update={
                        "kind": operation.model_copy(
                            update={"equipment_digest": digest(entry)}
                        ).model_dump_json()
                    }
                )
                if event.id.startswith(PREFIX)
                else event
                for event in resources.events
            ),
            "game_time": 100,
        }
    )
    engine.specs["sword"] = entry.inventory_spec()
    assert reserved(resources, "sword")
    damaged, outcome = apply_object(
        engine,
        resources,
        DamageObject(
            id="destroy-kit",
            actor_id="a",
            item_id="sword",
            expected_revision=resources.revision,
            basic_damage=100,
            damage_type="cr",
        ),
        system=True,
        rng=RecordedDice(()),
    )
    assert outcome.condition.destroyed and outcome.condition.hp == -88  # HP10 - (100 - DR2).
    later = damaged.model_copy(update={"game_time": 200})
    settled = settle(later, task, (entry,))
    assert next(item.charges for item in settled.items if item.id == "sword") == 3500
    released = record_task(settled, task.model_copy(update={"status": "cancelled"}), "cancel")
    assert not reserved(released, "sword")


def test_reserved_kit_cannot_be_spent_as_another_equipment_features_fuel() -> None:
    resources, _ = operation_fixture()
    engine = cast(
        ResourceEngine,
        SimpleNamespace(
            specs={
                "bag": EquipmentSpec(
                    definition_id="bag",
                    unit_weight=1,
                    general=(
                        GeneralEquipmentFeature(
                            kind="light",
                            duration_seconds=3600,
                            consumable_definition_id="sword",
                            consumable_units=1,
                        ),
                    ),
                )
            },
            validate=lambda state: None,
        ),
    )
    with pytest.raises(ConflictError, match="pending timed repair"):
        apply_general_equipment(
            engine,
            resources,
            UseGeneralEquipment(
                id="consume-reserved",
                actor_id="a",
                item_id="bag",
                consumable_item_id="sword",
                duration_seconds=1,
                expected_revision=resources.revision,
            ),
            actor_technology_level=4,
        )


def test_artifact_operation_cannot_draw_or_spend_a_reserved_tool_reservoir() -> None:
    reducer = artifact_engine()
    entry = alternative("skill:armoury-body-armor", duration_seconds=3600).model_copy(
        update={"definition_id": "tool", "weight_millipounds": 1}
    )
    reducer.resources.specs["tool"] = entry.inventory_spec()
    state = known_state(reducer)
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"charges": 3600}) if item.id == "tool" else item
                for item in state.resources.items
            )
        }
    )
    tool = next(item for item in resources.items if item.id == "tool")
    operation = prepare(entry, tool, "a", "repair", "sword", 0, 1800, "skill:armoury-body-armor")
    assert operation
    task = RepairTask(
        id="repair",
        actor_id="a",
        item_id="sword",
        tool_id="tool",
        start=0,
        due=1800,
        skill=12,
        condition=ObjectCondition(hp=0),
    )
    state = state.model_copy(
        update={"resources": record_task(record(resources, operation, False), task, "repair")}
    )
    before = state.model_dump_json()
    rng = RecordedDice(())
    with pytest.raises(ValidationError, match="owned, retrieved, and accessible"):
        reducer.campaign.apply(
            state,
            OperateArtifact(
                id="other-use",
                actor_id="a",
                expected_revision=0,
                item_id="tool",
                capability_id="beam",
            ),
            rng=rng,
        )
    assert state.model_dump_json() == before and rng.exhausted()


def test_prior_same_tick_disable_receipt_does_not_stop_a_new_operation() -> None:
    resources, task = operation_fixture()
    old = ObjectResult(
        command_id="prior-disable", item_id="sword", condition=ObjectCondition(hp=0, disabled=True)
    )
    resources = resources.model_copy(
        update={
            "game_time": 1800,
            "object_results": (old,),
            "events": (
                ResourceEvent(
                    id="object:prior-disable", at=0, target_id="sword", kind="stress_object"
                ),
            )
            + resources.events,
        }
    )
    entry = alternative("skill:armoury-body-armor", duration_seconds=3600).model_copy(
        update={"definition_id": "sword", "weight_millipounds": 5}
    )
    settled = settle(resources, task, (entry,))
    assert next(item.charges for item in settled.items if item.id == "sword") == 1800


@pytest.mark.parametrize(
    "shape",
    [
        "good",
        "unselected",
        "wrong-skill",
        "count",
        "range",
        "target",
        "modifier",
        "extra-feature",
        "no-reservoir",
        "stack",
        "external",
        "capacity-only",
        "non-tool-capacity",
    ],
)
def test_fresh_operating_contract_accepts_only_one_plain_exact_skill_seconds_tool(
    shape: str,
) -> None:
    skill = "skill:armoury-body-armor"
    entry = alternative(skill, duration_seconds=1800)
    tool = Item(id="kit", definition_id=entry.definition_id, owner_id="a", charges=1800)
    selected = shape != "unselected"
    if shape == "wrong-skill":
        skill = "skill:armoury-melee-weapons"
    elif shape in {
        "count",
        "range",
        "target",
        "modifier",
        "external",
        "capacity-only",
        "non-tool-capacity",
    }:
        feature = entry.general[0]
        changes_by_shape: dict[str, dict[str, object]] = {
            "count": {"capacity": 1},
            "range": {"range_yards": 1},
            "target": {"target": "weapon"},
            "modifier": {"modifier": 1},
            "external": {"consumable_definition_id": "fuel", "consumable_units": 1},
            "capacity-only": {"duration_seconds": None, "capacity": 1},
            "non-tool-capacity": {
                "kind": "container",
                "skill_id": None,
                "duration_seconds": None,
                "capacity": 1,
            },
        }
        changes = changes_by_shape[shape]
        entry = entry.model_copy(
            update={
                "general": (GeneralEquipmentFeature.model_validate(feature.model_dump() | changes),)
            }
        )
    elif shape == "extra-feature":
        entry = entry.model_copy(
            update={"general": entry.general + (GeneralEquipmentFeature(kind="accessory"),)}
        )
    elif shape == "no-reservoir":
        tool = tool.model_copy(update={"charges": None})
    elif shape == "stack":
        tool = tool.model_copy(update={"quantity": 2})
    if shape == "good":
        require_selection(entry, tool, selected, source_policy=True, skill_id=skill)
    else:
        with pytest.raises(ValidationError, match="operating limits"):
            require_selection(entry, tool, selected, source_policy=True, skill_id=skill)
