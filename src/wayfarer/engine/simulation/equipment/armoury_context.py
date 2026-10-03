"""Private B169/B178 director observations, captured by the next repair task."""

import hashlib
from typing import Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.skills.mundane.arts import OBJECT_REPAIR_SKILLS
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.equipment.repair_default_resolver import current as default_current
from wayfarer.engine.simulation.equipment.worksite import available_here
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "armoury-familiarity:"
Basis = Literal["unfamiliar-model", "known-model", "similar-model"]


class DeclareArmouryFamiliarity(Command):
    kind: Literal["declare-armoury-familiarity"] = "declare-armoury-familiarity"
    performer_id: Id
    item_id: Id
    basis: Basis
    repair_start_command_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)


ADAPTER: TypeAdapter[DeclareArmouryFamiliarity] = TypeAdapter(DeclareArmouryFamiliarity)


class ArmouryFamiliarity(Record):
    command_id: Id
    performer_id: Id
    equipment_definition_id: Id
    skill_id: Id
    basis: Basis
    observed_at: int


def observations(resources: ResourceState) -> tuple[ArmouryFamiliarity, ...]:
    return tuple(
        ArmouryFamiliarity.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def familiarity_modifier(state: PlayState, actor_id: str, definition_id: str, skill_id: str) -> int:
    """Unobserved historical tasks retain their target; no similarity is inferred."""
    current = next(
        (
            entry
            for entry in reversed(observations(state.resources))
            if (entry.performer_id, entry.equipment_definition_id, entry.skill_id)
            == (actor_id, definition_id, skill_id)
        ),
        None,
    )
    return -2 if current is not None and current.basis == "unfamiliar-model" else 0


def repair_familiarity_modifier(
    state: PlayState, actor_id: str, definition_id: str, skill_id: str, procedure_id: str | None
) -> int:
    """Only bound restoration procedures consume observed model familiarity."""
    return familiarity_modifier(state, actor_id, definition_id, skill_id) if procedure_id else 0


def declare(
    runtime: RulesContext, state: PlayState, command: DeclareArmouryFamiliarity
) -> tuple[PlayState, ArmouryFamiliarity]:
    if runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
        raise ValidationError("Armoury familiarity requires the exact Basic Set profile")
    item = next((item for item in state.resources.items if item.id == command.item_id), None)
    if (
        item is None
        or item.owner_id != command.performer_id
        or not available_here(state, command.performer_id, item)
    ):
        raise ValidationError("Armoury familiarity requires the performer's current equipment")
    spec = runtime.resources.specs[item.definition_id]
    profile = spec.durability
    default_plan = None
    if command.repair_start_command_id is not None:
        entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
        default_plan = default_current(
            runtime,
            state,
            actor_id=command.performer_id,
            item=item,
            entry=entry,
            start_command_id=command.repair_start_command_id,
            required=True,
        )
    if (
        profile is None
        or profile.repair_skill_id is None
        or (profile.repair_skill_id not in OBJECT_REPAIR_SKILLS and default_plan is None)
    ):
        raise ValidationError("Armoury familiarity requires a supported restoration specialty")
    compiled = runtime.approved_build(state, command.performer_id)
    purchase = next(
        (
            purchase
            for purchase in compiled.purchases
            if purchase.definition_id == profile.repair_skill_id
        ),
        None,
    )
    if default_plan is None and (purchase is None or purchase.technology_level is None):
        raise ValidationError("Armoury familiarity requires an approved specialty and TL")
    observation = ArmouryFamiliarity(
        command_id=command.id,
        performer_id=command.performer_id,
        equipment_definition_id=item.definition_id,
        skill_id=profile.repair_skill_id,
        basis=command.basis,
        observed_at=state.resources.game_time,
    )
    resources = state.resources.model_copy(
        update={
            "revision": state.revision + 1,
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(command.id.encode()).hexdigest(),
                    at=state.resources.game_time,
                    target_id=command.performer_id,
                    kind=observation.model_dump_json(),
                ),
            ),
        }
    )
    return state.model_copy(
        update={"revision": state.revision + 1, "resources": resources}
    ), observation
