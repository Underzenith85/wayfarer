"""Source-qualified charged-hand ordinary punch capture and once-only discharge."""

import hashlib

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.generations import hand_melee_spell_contact_enabled
from wayfarer.engine.simulation.combat.unarmed.records import PendingUnarmed
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.magic.deathtouch_effects import apply_deathtouch
from wayfarer.engine.simulation.magic.hand_melee_contact_state import (
    HandContact,
    HandContactResult,
    attach_contact,
    contact_digest,
    contact_results,
    read_contact,
)
from wayfarer.engine.simulation.magic.hand_melee_spell_admission import hand_digest, mana_witness
from wayfarer.engine.simulation.magic.hand_melee_spell_state import (
    HandMeleeCast,
    append,
    cast_event,
    casts,
)
from wayfarer.engine.simulation.magic.melee_hand_carrier import require_productive_hand
from wayfarer.engine.simulation.magic.melee_spell_admission import admit_target
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError


def _held(state: PlayState, actor_id: str) -> HandMeleeCast | None:
    found = tuple(
        c for c in casts(state.resources).values() if c.actor_id == actor_id and c.status == "held"
    )
    if len(found) > 1:
        raise ConflictError("One actor cannot carry multiple hand Melee charges")
    return found[0] if found else None


def _source(runtime: RulesContext, state: PlayState, charge: HandMeleeCast) -> None:
    compiled = require_productive_hand(
        runtime,
        state,
        actor_id=charge.actor_id,
        hand=charge.carrier.hand,
        build_revision=charge.build_revision,
    )
    witness = mana_witness(state, charge.actor_id)
    if (
        witness.id != charge.mana_event_id
        or hashlib.sha256(witness.kind.encode()).hexdigest() != charge.mana_event_digest
    ):
        raise ConflictError("Hand contact current mana source changed")
    expected = hand_digest(state, charge.actor_id, charge.carrier.hand, compiled.revision)
    if expected != charge.carrier_digest:
        raise ConflictError("Hand contact accepted carrier identity changed")


def guard_unarmed_declaration(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> None:
    del encounter
    charge = _held(state, command.actor_id)
    if charge is None:
        return
    if (
        command.action != "punch"
        or command.maneuver != "attack"
        or command.attack_option is not None
        or command.second_attack is not None
        or command.skill not in ("attribute:dx", "skill:brawling")
        or len(command.hands) != 1
    ):
        raise ConflictError("Charged hand requires one explicit ordinary DX or Brawling punch")
    if command.hands[0] != charge.carrier.hand:
        return
    if not hand_melee_spell_contact_enabled():
        raise ConflictError("Charged hand requires captured hand-contact delivery generation")
    _source(runtime, state, charge)
    admit_target(runtime, state, command.target_id)


def _pending_digest(pending: PendingUnarmed) -> str:
    return hashlib.sha256(pending.model_dump_json(exclude={"allowed"}).encode()).hexdigest()


def _body_digest(state: PlayState, actor_id: str) -> str:
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    if actor.body is None:
        raise ConflictError("Hand contact body is unavailable")
    return hashlib.sha256(actor.body.model_dump_json().encode()).hexdigest()


def prepare_contact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    pending: PendingUnarmed,
    *,
    command_id: str,
) -> HandContact | None:
    charge = _held(state, pending.actor_id)
    if charge is None or pending.hands != (charge.carrier.hand,):
        return None
    if not hand_melee_spell_contact_enabled():
        raise ConflictError("Charged hand requires captured hand-contact delivery generation")
    if (
        pending.action != "punch"
        or pending.skill not in ("attribute:dx", "skill:brawling")
        or pending.grip_id is not None
        or pending.choke_hold
    ):
        raise ConflictError("Hand contact requires actual ordinary punch metadata")
    if (
        pending.id != "unarmed:" + hashlib.sha256(command_id.encode()).hexdigest()
        or encounter.pending_unarmed != pending
    ):
        raise ConflictError("Hand contact actual pending source identity changed")
    _source(runtime, state, charge)
    admit_target(runtime, state, pending.target_id)
    event = cast_event(state.resources, charge.cast_id)
    return HandContact(
        pending_id=pending.id,
        pending_digest=_pending_digest(pending),
        command_id=command_id,
        encounter_id=encounter.id,
        attacker_id=pending.actor_id,
        defender_id=pending.target_id,
        hand=charge.carrier.hand,
        cast_id=charge.cast_id,
        charge_event_id=event.id,
        charge_digest=hashlib.sha256(event.kind.encode()).hexdigest(),
        build_revision=charge.build_revision,
        body_digest=_body_digest(state, charge.actor_id),
        mana_event_id=charge.mana_event_id,
        mana_event_digest=charge.mana_event_digest,
        energy=charge.energy,
    )


def _identity(state: PlayState, encounter: Encounter, contact: HandContact) -> HandMeleeCast:
    if read_contact(state.resources, contact.pending_id) != contact:
        raise ConflictError("Hand contact must match its recorded actual association")
    charge = casts(state.resources).get(contact.cast_id)
    if charge is None or charge.status != "held" or charge.actor_id != contact.attacker_id:
        raise ConflictError("Hand contact charge is no longer held")
    event = cast_event(state.resources, contact.cast_id)
    if (
        encounter.id != contact.encounter_id
        or event.target_id != contact.attacker_id
        or event.id != contact.charge_event_id
        or hashlib.sha256(event.kind.encode()).hexdigest() != contact.charge_digest
        or charge.carrier.hand != contact.hand
        or charge.energy != contact.energy
        or charge.build_revision != contact.build_revision
        or charge.mana_event_id != contact.mana_event_id
        or charge.mana_event_digest != contact.mana_event_digest
    ):
        raise ConflictError("Hand contact immutable charge source changed")
    return charge


def validate_contact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    pending: PendingUnarmed,
    contact: HandContact,
) -> None:
    if (
        pending.id != contact.pending_id
        or pending.actor_id != contact.attacker_id
        or pending.target_id != contact.defender_id
        or pending.hands != (contact.hand,)
        or _pending_digest(pending) != contact.pending_digest
    ):
        raise ConflictError("Hand contact actual pending metadata changed")
    charge = _identity(state, encounter, contact)
    _source(runtime, state, charge)
    if _body_digest(state, contact.attacker_id) != contact.body_digest:
        raise ConflictError("Hand contact current body identity changed")
    admit_target(runtime, state, contact.defender_id)


def finish_contact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    contact: HandContact,
    *,
    ordinary_hit: bool,
    actual_defense: Defense,
    defense_check: CheckTrace | None,
    defense_implement_id: str | None,
    resolved_location: HumanLocation | None,
    critical_row: int | None,
    prevalidated: bool = False,
) -> tuple[PlayState, Encounter, HandContactResult]:
    del critical_row
    previous = next(
        (r for r in contact_results(state.resources) if r.pending_id == contact.pending_id), None
    )
    if previous is not None:
        if (
            previous.contact_digest != contact_digest(contact)
            or previous.ordinary_hit != ordinary_hit
            or previous.actual_defense != actual_defense
            or previous.defense_check != defense_check
            or previous.defense_implement_id != defense_implement_id
            or previous.location != resolved_location
        ):
            raise ConflictError("Hand contact completed delivery facts changed")
        return state, encounter, previous
    charge = _identity(state, encounter, contact)
    if not prevalidated:
        pending = encounter.pending_unarmed
        if pending is None:
            raise ConflictError("Deferred hand contact requires actual pending source")
        validate_contact(runtime, state, encounter, pending, contact)
    defended = defense_check is not None and defense_check.outcome.succeeded
    if defended and actual_defense == "parry" and defense_implement_id is None:
        raise ConflictError("Hand contact Parry requires its actual classified implement")
    arc = (
        defended
        and actual_defense == "parry"
        and defense_implement_id in ("left-hand", "right-hand")
    )
    if actual_defense == "block":
        raise ConflictError("Charged hand Block delivery has no canonical unarmed route")
    trigger = ordinary_hit or arc
    hp = next(p for p in state.resources.pools if p.id == "hp:" + contact.defender_id)
    hp_before = hp.current
    dice: tuple[int, ...] = ()
    injury = 0
    checks: tuple[CheckTrace, ...] = ()
    reasons: tuple[str, ...] = ()
    if trigger:
        if hp.injury is None:
            raise ConflictError("Hand contact victim health is unavailable")
        if not hp.injury.dead:
            state, dice, injury, checks, reasons = apply_deathtouch(
                runtime,
                state,
                command_id=contact.pending_id + ":hand-deathtouch",
                defender_id=contact.defender_id,
                energy=contact.energy,
            )
        state = state.model_copy(
            update={
                "resources": append(
                    state.resources,
                    "cast",
                    contact.pending_id + ":spent",
                    charge.actor_id,
                    charge.model_copy(update={"status": "spent"}),
                )
            }
        )
    result = HandContactResult(
        pending_id=contact.pending_id,
        contact_digest=contact_digest(contact),
        cast_id=contact.cast_id,
        attacker_id=contact.attacker_id,
        defender_id=contact.defender_id,
        hand=contact.hand,
        status="spent" if trigger else "held",
        outcome="discharged" if trigger and dice else "no-effect" if trigger else "held",
        triggered=trigger,
        ordinary_hit=ordinary_hit,
        actual_defense=actual_defense,
        defense_check=defense_check,
        defense_implement_id=defense_implement_id,
        location=resolved_location,
        hp_before=hp_before,
        hp_after=next(
            p.current for p in state.resources.pools if p.id == "hp:" + contact.defender_id
        ),
        dice=dice,
        injury=injury,
        injury_checks=checks,
        injury_check_reasons=reasons,
    )
    state = state.model_copy(
        update={
            "resources": append(
                state.resources, "result", contact.pending_id, contact.attacker_id, result
            )
        }
    )
    return state, encounter, result


# Explicit root/unarmed import surface; no existing dispatcher registration here.
__all__ = [
    "HandContact",
    "HandContactResult",
    "attach_contact",
    "prepare_contact",
    "read_contact",
    "guard_unarmed_declaration",
    "validate_contact",
    "finish_contact",
]
