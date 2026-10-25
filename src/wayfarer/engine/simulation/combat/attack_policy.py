"""Producer-pinned voluntary attack rules, independent of later defender commands."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.generations import (
    melee_attack_postures_enabled,
    voluntary_attack_minimum_enabled,
)
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.models import Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext

PREFIX = "combat-attack-policy:"


class AttackPolicy(Record):
    minimum: bool = False
    postures: bool = False


def uses_basic_set_attacks(runtime: RulesContext) -> bool:
    combat = runtime.rules.combat
    return (
        combat is not None
        and combat.gurps_equipment is not None
        and combat.gurps_equipment.profile_id == "gurps-basic-set-4e-2004"
    )


def _key(encounter_id: str, actor_id: str, attack_id: str) -> str:
    return (
        PREFIX
        + hashlib.sha256(json.dumps([encounter_id, actor_id, attack_id]).encode()).hexdigest()
    )


def _read(
    state: PlayState, encounter_id: str, actor_id: str, attack_id: str
) -> AttackPolicy | None:
    event = next(
        (e for e in state.resources.events if e.id == _key(encounter_id, actor_id, attack_id)), None
    )
    return AttackPolicy.model_validate_json(event.kind) if event is not None else None


def _wait_key(state: PlayState, actor_id: str) -> str:
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    assert hp.injury is not None
    return "wait-turn:" + str(hp.injury.turn)


def initiating_policy(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str
) -> AttackPolicy:
    if not uses_basic_set_attacks(runtime):
        return AttackPolicy()
    if encounter.wait_interrupt is not None and encounter.wait_interrupt.waiter_id == actor_id:
        return _read(state, encounter.id, actor_id, _wait_key(state, actor_id)) or AttackPolicy()
    old = next((e for e in state.encounters if e.id == encounter.id), None)
    if old is not None:
        pending = old.pending_defense
        unarmed = old.pending_unarmed
        identifier = (
            pending.id
            if pending is not None and pending.attacker_id == actor_id
            else unarmed.id
            if unarmed is not None and unarmed.actor_id == actor_id
            else None
        )
        if identifier is not None:
            return _read(state, encounter.id, actor_id, identifier) or AttackPolicy()
    return AttackPolicy(
        minimum=voluntary_attack_minimum_enabled(), postures=melee_attack_postures_enabled()
    )


def policy(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    *,
    pending_id: str | None = None,
    declaring: bool = False,
) -> AttackPolicy:
    if not uses_basic_set_attacks(runtime):
        return AttackPolicy()
    captured = _read(state, encounter.id, actor_id, pending_id) if pending_id is not None else None
    if captured is not None:
        return captured
    return initiating_policy(runtime, state, encounter, actor_id) if declaring else AttackPolicy()


def posture_rules(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    *,
    pending_id: str | None = None,
    declaring: bool = False,
) -> bool:
    return policy(
        runtime, state, encounter, actor_id, pending_id=pending_id, declaring=declaring
    ).postures


def bind(
    state: PlayState, encounter: Encounter, actor_id: str, attack_id: str, selected: AttackPolicy
) -> PlayState:
    identifier = _key(encounter.id, actor_id, attack_id)
    if any(e.id == identifier for e in state.resources.events) or not (
        selected.minimum or selected.postures
    ):
        return state
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "events": state.resources.events
                    + (
                        ResourceEvent(
                            id=identifier,
                            at=state.resources.game_time,
                            target_id=actor_id,
                            kind=selected.model_dump_json(),
                        ),
                    )
                }
            )
        }
    )


def bind_pending(runtime: RulesContext, state: PlayState, encounter: Encounter) -> PlayState:
    if not uses_basic_set_attacks(runtime):
        return state
    pending = encounter.pending_defense
    unarmed = encounter.pending_unarmed
    if pending is not None and pending.composed_attack_id is None and pending.spell_cast_id is None:
        actor_id, identifier = pending.attacker_id, pending.id
    elif unarmed is not None:
        actor_id, identifier = unarmed.actor_id, unarmed.id
    else:
        return state
    return bind(
        state,
        encounter,
        actor_id,
        identifier,
        initiating_policy(runtime, state, encounter, actor_id),
    )


def bind_wait(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str
) -> PlayState:
    if not uses_basic_set_attacks(runtime):
        return state
    return bind(
        state,
        encounter,
        actor_id,
        _wait_key(state, actor_id),
        initiating_policy(runtime, state, encounter, actor_id),
    )
