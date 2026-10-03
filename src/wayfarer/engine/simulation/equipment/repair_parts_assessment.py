"""Commit the B485 requirement before buying or consuming repair supplies."""

from fractions import Fraction
from math import ceil

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.repair_parts import (
    AssessRepairParts,
    RepairPartsAssessment,
    digest,
    latest,
    record,
    require_current,
)
from wayfarer.engine.simulation.equipment.repair_transitions import repair
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def assess(
    runtime: RulesContext, state: PlayState, command: AssessRepairParts
) -> tuple[PlayState, RepairPartsAssessment]:
    repair(
        runtime,
        state,
        actor_id=command.actor_id,
        item_id=command.item_id,
        command_id=command.id,
        stage="start",
        task_id=None,
        preview=True,
        assessment_only=True,
        preview_context_id=command.repair_start_command_id,
    )
    item = next(i for i in state.resources.items if i.id == command.item_id)
    assert item.condition is not None
    if item.condition.hp > 0:
        raise ValidationError("Parts assessment requires a major repair")
    entry = runtime.resources.specs[item.definition_id]
    # The canonical equipment catalog owns both prices and the repair binding.
    equipment = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    assert entry.durability is not None
    part_entry: EquipmentProfile = next(
        e
        for e in catalog(runtime).entries
        if e.definition_id == entry.durability.repair_parts_definition
    )
    prior = latest(state.resources, command.item_id)
    if prior is not None:
        # Eligibility above proves current custody and the approved performer.
        # The item's immutable requirement survives a lawful owner handoff.
        prior = prior.model_copy(update={"actor_id": command.actor_id})
    if prior is not None and prior.condition != item.condition:
        settled = next(
            (t for t in reversed(tasks(state.resources)) if t.item_id == item.id),
            None,
        )
        if (
            settled is not None
            and settled.status == "completed"
            and settled.restored_hp > 0
            and settled.condition == prior.condition
        ):
            expected = settled.condition.model_copy(
                update={
                    "hp": settled.condition.hp + settled.restored_hp,
                    "disabled": False,
                    "residual_roll": None,
                    "shock": 0,
                    "shock_until": None,
                    "reduced_definition_id": None
                    if (settled.condition.hp + settled.restored_hp) * 3 >= entry.durability.hp
                    else settled.condition.reduced_definition_id,
                }
            )
            if item.condition == expected:
                require_current(
                    prior.model_copy(update={"condition": expected}),
                    actor_id=command.actor_id,
                    definition_id=item.definition_id,
                    condition=item.condition,
                    entry=equipment,
                    part_entry=part_entry,
                )
                prior = None
    if prior is not None:
        require_current(
            prior,
            actor_id=command.actor_id,
            definition_id=item.definition_id,
            condition=item.condition,
            entry=equipment,
            part_entry=part_entry,
        )
        result = prior
    else:
        die = draw_dice(runtime.rng, 1)[0]
        result = RepairPartsAssessment(
            command_id=command.id,
            actor_id=command.actor_id,
            assessor_id=command.actor_id,
            item_id=item.id,
            definition_id=item.definition_id,
            condition=item.condition,
            profile_digest=digest(equipment),
            parts_definition_id=part_entry.definition_id,
            parts_price_digest=digest(part_entry),
            die=die,
            quantity=ceil(Fraction(equipment.price) * die / (Fraction(part_entry.price) * 10)),
        )
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "resources": record(state.resources, result, command.id).model_copy(
                update={"revision": state.revision + 1}
            ),
        }
    ), result
