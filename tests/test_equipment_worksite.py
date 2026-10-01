"""World-grounded repair/salvage materials must be at the actual worksite."""

from pathlib import Path

import pytest
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import ObjectProfile, SalvageProfile
from wayfarer.engine.simulation.equipment.repair_transitions import repair
from wayfarer.engine.simulation.equipment.salvage import salvage
from wayfarer.engine.simulation.equipment.worksite import available_here
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, EndEncounter


@pytest.mark.parametrize(
    "operation,remote_item",
    [
        ("repair", "sword-b"),
        ("repair", "tool-b"),
        ("repair", "parts-b"),
        ("salvage", "sword-b"),
        ("salvage", "tool-b"),
    ],
)
async def test_world_ground_work_checks_objects_tools_and_parts(
    tmp_path: Path, operation: str, remote_item: str
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(
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
        ),
        object_hp=0,
    )
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end", actor_id="gm", expected_revision=1, encounter_id="fight", reason="worksite"
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    actor = next(e for e in state.world.entities if e.id == "b")
    # A workpiece may stay on local world ground; repair does not teleport it into inventory.
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"world_ground_location_id": actor.location_id})
                        if i.id == "sword-b"
                        else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    reduce = repair if operation == "repair" else salvage
    play.rng = RecordedDice((1,))
    bad = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"world_ground_location_id": "remote"})
                        if i.id == remote_item
                        else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    with pytest.raises(ValidationError):
        reduce(
            play.rules_context,
            bad,
            actor_id="b",
            item_id="sword-b",
            command_id="start",
            stage="start",
            task_id=None,
        )
    updated, task = reduce(
        play.rules_context,
        state,
        actor_id="b",
        item_id="sword-b",
        command_id="start",
        stage="start",
        task_id=None,
    )
    assert (
        next(i for i in updated.resources.items if i.id == "sword-b").world_ground_location_id
        == actor.location_id
    )
    # Object and pinned-tool location are checked again at completion. Cancellation is always possible.
    if remote_item != "parts-b":
        moved = updated.model_copy(
            update={
                "resources": updated.resources.model_copy(
                    update={
                        "game_time": task.due,
                        "items": tuple(
                            i.model_copy(update={"world_ground_location_id": "remote"})
                            if i.id == remote_item
                            else i
                            for i in updated.resources.items
                        ),
                    }
                )
            }
        )
        with pytest.raises((ValidationError, ConflictError)):
            reduce(
                play.rules_context,
                moved,
                actor_id="b",
                item_id="sword-b",
                command_id="finish",
                stage="finish",
                task_id="start",
            )
        _, cancelled = reduce(
            play.rules_context,
            moved,
            actor_id="b",
            item_id="sword-b",
            command_id="cancel",
            stage="cancel",
            task_id="start",
        )
        assert cancelled.status == "cancelled"
    # A null ground field on a contained tool must not disguise a remote grounded root.
    tool = next(i for i in state.resources.items if i.id == "tool-b")
    container = Item(
        id="container",
        owner_id="b",
        definition_id=tool.definition_id,
        world_ground_location_id="remote",
    )
    contained = tool.model_copy(update={"container_id": "container"})
    nested = state.model_copy(
        update={"resources": state.resources.model_copy(update={"items": (container, contained)})}
    )
    assert not available_here(nested, "b", contained)
