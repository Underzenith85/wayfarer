"""B551 policy is pinned by the launched attack, never a fresh defender command."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError as SchemaError
from pydantic import field_validator

from wayfarer.engine.rules.tables.combat import posture_ranged_target_modifier
from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.thrown.live_grenades import armed_cause
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ValidationError
from wayfarer.models import Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext

PROFILE = "gurps-basic-set-4e-2004"
PREFIX = "combat-ranged-target-policy:"


class RangedTargetPolicy(Record):
    generation: Literal[1] = 1

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Ranged target policy requires exact integer generation 1")
        return value


def _key(encounter: Encounter, actor_id: str, identifier: str) -> str:
    return (
        PREFIX
        + hashlib.sha256(json.dumps([encounter.id, actor_id, identifier]).encode()).hexdigest()
    )


def _recorded(state: PlayState, encounter: Encounter, actor_id: str, identifier: str) -> bool:
    events = tuple(
        event
        for event in state.resources.events
        if event.id == _key(encounter, actor_id, identifier)
    )
    if not events:
        return False
    if len(events) != 1 or events[0].target_id != actor_id:
        raise ValidationError("Recorded ranged target policy has duplicate identity or wrong owner")
    try:
        RangedTargetPolicy.model_validate_json(events[0].kind)
    except SchemaError as exc:
        raise ValidationError("Invalid recorded ranged target policy generation") from exc
    return True


def _wait_key(state: PlayState, actor_id: str) -> str:
    hp = next(pool for pool in state.resources.pools if pool.id == "hp:" + actor_id)
    assert hp.injury is not None
    return "wait-turn:" + str(hp.injury.turn)


def initiating(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str
) -> bool:
    interrupt = encounter.wait_interrupt
    if interrupt is not None and interrupt.waiter_id == actor_id:
        return _recorded(state, encounter, actor_id, _wait_key(state, actor_id))
    old = next((item for item in state.encounters if item.id == encounter.id), None)
    pending = old.pending_defense if old is not None else None
    if pending is not None and pending.attacker_id == actor_id:
        return _recorded(state, encounter, actor_id, pending.id)
    return runtime.correct_ranged_target_postures


def enabled(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    *,
    pending_id: str | None = None,
    declaring: bool = False,
) -> bool:
    if pending_id is not None and _recorded(state, encounter, actor_id, pending_id):
        return True
    return initiating(runtime, state, encounter, actor_id) if declaring else False


def bind(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    identifier: str,
    *,
    profile_id: str,
    initiating_state: PlayState | None = None,
) -> PlayState:
    if profile_id != PROFILE or not initiating(
        runtime, initiating_state or state, encounter, actor_id
    ):
        return state
    key = _key(encounter, actor_id, identifier)
    if any(event.id == key for event in state.resources.events):
        return state
    event = ResourceEvent(
        id=key,
        at=state.resources.game_time,
        target_id=actor_id,
        kind=RangedTargetPolicy().model_dump_json(),
    )
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"events": state.resources.events + (event,)}
            )
        }
    )


def bind_pending(runtime: RulesContext, state: PlayState, encounter: Encounter) -> PlayState:
    pending = encounter.pending_defense
    if (
        pending is None
        or pending.composed_attack_id is not None
        or pending.spell_cast_id is not None
        or pending.suppression_zone_id is not None
    ):
        return state
    rules = runtime.rules.combat
    if rules is None or rules.gurps_equipment is None or pending.shield_rush:
        return state
    if not isinstance(
        mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id), RangedMode
    ):
        return state
    return bind(
        runtime,
        state,
        encounter,
        pending.attacker_id,
        pending.id,
        profile_id=rules.gurps_equipment.profile_id,
    )


def bind_wait(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str
) -> PlayState:
    rules = runtime.rules.combat
    if (
        rules is None
        or rules.gurps_equipment is None
        or rules.gurps_equipment.profile_id != PROFILE
    ):
        return state
    return bind(
        runtime,
        state,
        encounter,
        actor_id,
        _wait_key(state, actor_id),
        profile_id=rules.gurps_equipment.profile_id,
    )


def target_modifier(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    target_id: str,
    *,
    profile_id: str,
    location: HitLocation | None = None,
    pending_id: str | None = None,
    declaring: bool = False,
    object_target: bool = False,
    area_target: bool = False,
    captured: bool = False,
) -> int:
    response = runtime.response_attack
    if captured or (response is not None and response[0] == pending_id):
        return 0
    if (
        profile_id != PROFILE
        or object_target
        or area_target
        or not enabled(
            runtime, state, encounter, actor_id, pending_id=pending_id, declaring=declaring
        )
    ):
        return 0
    target = next(
        participant for participant in encounter.participants if participant.actor_id == target_id
    )
    return posture_ranged_target_modifier(target.posture, location)


def _nonpausing_grenade_delivery(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: RangedMode,
    response: ChooseDefense | None,
) -> bool:
    pending = encounter.pending_defense
    assert pending is not None
    if (
        response is None
        or response.actor_id != pending.defender_id
        or response.encounter_id != encounter.id
        or response.defense != "none"
        or "none" not in pending.allowed
        or response.item_id is not None
        or response.second_defense is not None
        or response.second_item_id is not None
        or response.catch_thrown
        or response.sacrificial_for is not None
        or response.acrobatic_dodge
        or response.dodge_and_drop
        or response.retreat is not None
        or response.basic_retreat
        or response.parry_mode_id is not None
        or response.second_parry_mode_id is not None
        or selected.firearm is None
        or selected.firearm.action != "grenade"
    ):
        return False
    source = next(item for item in state.resources.items if item.id == pending.weapon_id)
    entry = next(
        entry for entry in catalog(runtime).entries if entry.definition_id == source.definition_id
    )
    if entry.warhead is None:
        return False
    # The canonical payload reducer relocates this actual armed cause. With no
    # defense there are no shield impacts, so it never opens a damage pause.
    return armed_cause(state.resources, source.id, encounter.id) is not None


def require_owner_damage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    response: ChooseDefense | None = None,
) -> None:
    pending = encounter.pending_defense
    if (
        pending is None
        or pending.spell_cast_id is not None
        or pending.composed_attack_id is not None
    ):
        return
    if not enabled(runtime, state, encounter, pending.attacker_id, pending_id=pending.id):
        return
    selected = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if (
        isinstance(selected, RangedMode)
        and selected.thrown
        and not _nonpausing_grenade_delivery(runtime, state, encounter, selected, response)
    ):
        raise ValidationError("Thrown attacks do not support a separate owner-damage choice")
