"""Trusted roll visibility survives ending an optional Luck decision."""

import hashlib
import json

from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

SOURCE_PREFIX = "opponent-secret-source:"
RESULT_PREFIX = "opponent-secret-result:"


class SecretAttackSource(Record):
    encounter_id: Id
    attack_id: Id
    attacker_id: Id
    target_id: Id


class SecretAttackResult(Record):
    command_id: Id
    source: SecretAttackSource


def _identity(prefix: str, *values: str) -> str:
    key = json.dumps(values, separators=(",", ":"))
    return prefix + hashlib.sha256(key.encode()).hexdigest()


def secret_source(
    state: ResourceState, encounter_id: str, attack_id: str
) -> SecretAttackSource | None:
    identifier = _identity(SOURCE_PREFIX, encounter_id, attack_id)
    event = next((e for e in state.events if e.id == identifier), None)
    if event is None:
        return None
    source = SecretAttackSource.model_validate_json(event.kind)
    if (source.encounter_id, source.attack_id, source.target_id) != (
        encounter_id,
        attack_id,
        event.target_id,
    ):
        raise ValidationError("Secret attack visibility differs from its recorded source")
    return source


def pending_secret_source(state: ResourceState, encounter: Encounter) -> SecretAttackSource | None:
    pending = encounter.pending_defense
    unarmed = encounter.pending_unarmed
    if pending is not None:
        identity = pending.id, pending.attacker_id, pending.defender_id
    elif unarmed is not None:
        identity = unarmed.id, unarmed.actor_id, unarmed.target_id
    else:
        return None
    source = secret_source(state, encounter.id, identity[0])
    if source is not None and (source.attacker_id, source.target_id) != identity[1:]:
        raise ValidationError("Secret attack visibility differs from its pending declaration")
    return source


def conceal_attack_source(
    state: ResourceState, source: SecretAttackSource, *, system: bool = False
) -> ResourceState:
    if not system:
        raise ValidationError("Attack visibility requires trusted source authority")
    prior = secret_source(state, source.encounter_id, source.attack_id)
    if prior is not None:
        if prior != source:
            raise ValidationError("Secret attack source cannot change identity")
        return state
    event = ResourceEvent(
        id=_identity(SOURCE_PREFIX, source.encounter_id, source.attack_id),
        at=state.game_time,
        target_id=source.target_id,
        kind=source.model_dump_json(),
    )
    return state.model_copy(update={"events": state.events + (event,)})


def conceal_attack_result(
    state: ResourceState, source: SecretAttackSource, command_id: str
) -> ResourceState:
    if secret_source(state, source.encounter_id, source.attack_id) != source:
        raise ValidationError("Secret attack result requires its exact source visibility")
    binding = SecretAttackResult(command_id=command_id, source=source)
    event = ResourceEvent(
        id=_identity(RESULT_PREFIX, command_id),
        at=state.game_time,
        target_id=source.target_id,
        kind=binding.model_dump_json(),
    )
    return state.model_copy(update={"events": state.events + (event,)})


def private_attack_result(state: ResourceState, command_id: str) -> bool:
    identifier = _identity(RESULT_PREFIX, command_id)
    event = next((e for e in state.events if e.id == identifier), None)
    if event is None:
        return False
    binding = SecretAttackResult.model_validate_json(event.kind)
    source = binding.source
    if (
        binding.command_id != command_id
        or event.target_id != source.target_id
        or secret_source(state, source.encounter_id, source.attack_id) != source
    ):
        raise ValidationError("Secret attack result has no matching source visibility")
    return True
