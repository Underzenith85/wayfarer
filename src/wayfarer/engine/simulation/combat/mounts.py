"""Assigning a crew to a mounted weapon inside the encounter transaction (#357).

Serving a mount is a Ready maneuver by the gunner. The assignment is durable
inventory state, so a restart or a retried command finds the same crew rather
than an empty mount.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def assign_crew(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeCombatTurn
) -> ResourceState:
    from wayfarer.engine.simulation.actors import catalog

    if command.item_id is None:
        raise ValidationError("Serving a mount requires the mounted weapon")
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id:
        raise ValidationError("Serving a mount requires the gunner's own weapon")
    entry = next(
        (e for e in catalog(runtime).entries if e.definition_id == item.definition_id), None
    )
    mount = next(
        (
            m.mount
            for m in (entry.modes if entry else ())
            if isinstance(m, RangedMode) and m.mount is not None
        ),
        None,
    )
    if mount is None:
        raise ValidationError("This weapon has no mount to serve")
    crew = command.mount_crew
    if len(set(crew)) != len(crew):
        raise ValidationError("A crew member serves a mount once")
    if command.actor_id not in crew:
        raise ValidationError("The gunner serves its own mount")
    if len(crew) != mount.crew:
        raise ValidationError("A mounted weapon needs its full crew")
    if any(member not in encounter.turn_order for member in crew):
        raise ValidationError("Every crew member is in the encounter")
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"mount_crew": crew}) if i.id == item.id else i
                for i in state.resources.items
            )
        }
    )
    runtime.resources.validate(resources)
    return resources
