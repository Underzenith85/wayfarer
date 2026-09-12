"""B407 malfunction evidence in the same resource checkpoint as combat."""

import hashlib
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.firearm_types import FirearmFailure
from wayfarer.engine.rules.readiness_types import ProjectileProgress
from wayfarer.engine.simulation.combat import Combatant, InjuryTrace, RangedSituation
from wayfarer.engine.simulation.gurps_equipment import EquipmentCatalog, RangedMode
from wayfarer.engine.simulation.resources import (
    AmmunitionLoad,
    Item,
    Pool,
    ResourceEvent,
    ResourceState,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class MalfunctionRecord(Record):
    kind: Literal["firearm-malfunction-v1"] = "firearm-malfunction-v1"
    id: str
    encounter_id: str
    attacker: Combatant
    defender: Combatant
    attacker_build_revision: str
    defender_build_revision: str
    catalog: EquipmentCatalog
    weapon: RangedMode
    scene: RangedSituation
    original_attack: CheckTrace
    ammunition_load: AmmunitionLoad | None
    items: tuple[Item, ...]
    pools: tuple[Pool, ...]
    failure: FirearmFailure
    trace: InjuryTrace


def save_malfunction(state: ResourceState, record: MalfunctionRecord) -> ResourceState:
    event_id = "firearm-malfunction:" + hashlib.sha256(record.id.encode()).hexdigest()
    previous = next((e for e in state.events if e.id == event_id), None)
    if previous is not None:
        if MalfunctionRecord.model_validate_json(previous.kind) != record:
            raise ConflictError("Recorded firearm malfunction cannot be replaced")
        return state
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


def spend_rounds(state: ResourceState, weapon_id: str, count: int) -> ResourceState:
    if not count:
        return state
    load = next((v for v in state.ammunition_loads if v.weapon_id == weapon_id), None)
    if load is None or load.rounds < count:
        raise ValidationError("Insufficient loaded ammunition")
    loads = tuple(
        v.model_copy(
            update={
                "rounds": v.rounds - count,
                "reload_progress": 0,
                "readiness": ProjectileProgress(stage="loaded") if v.readiness else None,
            }
        )
        if v.weapon_id == weapon_id
        else v
        for v in state.ammunition_loads
    )
    items = tuple(
        i.model_copy(
            update={"charges": i.charges - count}
            if i.charges is not None
            else {"quantity": i.quantity - count}
        )
        if i.id == load.ammunition_item_id
        else i
        for i in state.items
        if i.id != load.ammunition_item_id or i.charges is not None or i.quantity > count
    )
    return state.model_copy(
        update={
            "items": items,
            "ammunition_loads": tuple(v for v in loads if v.rounds or v.reload_progress),
        }
    )


def validate_failures(state: ResourceState, equipment: EquipmentCatalog | None) -> None:
    for item in state.items:
        failure = item.firearm_failure
        if failure is None:
            continue
        if equipment is None or equipment.profile_id != "gurps-basic-set-4e-2004":
            raise ValidationError("Firearm failure requires the exact Basic Set catalog")
        entry = next((e for e in equipment.entries if e.definition_id == item.definition_id), None)
        weapon = next((m for m in entry.modes if m.id == failure.mode_id), None) if entry else None
        if item.quantity != 1 or not isinstance(weapon, RangedMode) or weapon.firearm is None:
            raise ValidationError("Firearm failure must reference an individual firearm mode")
        if failure.blocked_round and not any(
            v.weapon_id == item.id and v.mode_id == failure.mode_id and v.rounds > 0
            for v in state.ammunition_loads
        ):
            raise ValidationError("Misfired round must retain its ammunition reservation")
        cause = "firearm-malfunction:" + hashlib.sha256(failure.cause_id.encode()).hexdigest()
        if not any(e.id == cause for e in state.events):
            raise ValidationError("Firearm failure requires its recorded causal event")
