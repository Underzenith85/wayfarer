"""Immutable causal record for ranged critical results inside the combat CAS."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.simulation.combat import Combatant, InjuryTrace, RangedSituation
from wayfarer.simulation.gurps_equipment import EquipmentCatalog, RangedMode
from wayfarer.simulation.resources import (
    AmmunitionLoad,
    Id,
    Item,
    Pool,
    Record,
    ResourceEvent,
    ResourceState,
)


class RangedCritical(Record):
    kind: Literal["ranged-critical-v1"] = "ranged-critical-v1"
    id: Id
    encounter_id: Id
    created_at: int = Field(ge=0)
    attacker: Combatant
    defender: Combatant
    attacker_build_revision: Id
    defender_build_revision: Id
    catalog: EquipmentCatalog
    weapon: RangedMode
    scene: RangedSituation
    ammunition_load: AmmunitionLoad | None = None
    items: tuple[Item, ...]
    pools: tuple[Pool, ...]
    trace: InjuryTrace


def save_ranged_critical(state: ResourceState, record: RangedCritical) -> ResourceState:
    event_id = "ranged-critical:" + hashlib.sha256(record.id.encode()).hexdigest()
    previous = next((event for event in state.events if event.id == event_id), None)
    if previous is not None:
        if RangedCritical.model_validate_json(previous.kind) != record:
            raise ConflictError("Recorded ranged critical context cannot be replaced")
        return state
    if not record.trace.critical_table or record.created_at != state.game_time:
        raise ValidationError("Ranged critical context requires a current recorded table result")
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=state.game_time,
                    target_id=record.attacker.actor_id,
                    kind=record.model_dump_json(),
                ),
            )
        }
    )
