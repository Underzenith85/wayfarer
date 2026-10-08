"""Current source-approved personal Deathtouch and actual combat contact admission."""

import hashlib
import json

from wayfarer.engine.rules.types.location import Hand
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.backfires import forgotten, require_settled
from wayfarer.engine.simulation.magic.concentration import (
    require_idle_concentration,
    require_no_held_melee,
)
from wayfarer.engine.simulation.magic.deathtouch_learning import require_learning
from wayfarer.engine.simulation.magic.hand_melee_spell_state import (
    CastHandDeathtouch,
    casts,
    held_actor_ids,
)
from wayfarer.engine.simulation.magic.melee_hand_carrier import require_productive_hand
from wayfarer.engine.simulation.magic.melee_spell_state import (
    held_actor_ids as staff_held_actor_ids,
)
from wayfarer.engine.simulation.magic.rituals import require_ordinary_ritual
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def ready(
    runtime: RulesContext, state: PlayState, command: CastHandDeathtouch
) -> tuple[int, str, str]:
    if runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
        raise ValidationError("Deathtouch requires the Basic Set profile")
    guard(state, command.actor_id, "melee_spell_cast")
    synchronous(state, command.actor_id)
    if len(state.party.groups) > 1 or any(e.status == "active" for e in state.encounters):
        raise ConflictError("Bounded Deathtouch casting requires a sole noncombat party")
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    if (
        actor.available_at > state.resources.game_time
        or actor.conditions
        or not fatigue_ready(state, command.actor_id)
    ):
        raise ConflictError("Deathtouch caster is unavailable")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    if hp.injury is None or hp.injury.incapacitated or hp.injury.stunned or hp.injury.shock:
        raise ConflictError("Deathtouch requires a healthy conscious caster")
    require_settled(state.resources, command.actor_id)
    if forgotten(state.resources, command.actor_id, "deathtouch"):
        raise ConflictError("Deathtouch is currently forgotten")
    compiled = build(runtime, state, command.actor_id)
    purchase = {p.definition_id: p.amount for p in compiled.purchases}
    require_learning(runtime, purchase)
    skill = next(
        (int(v.value) for v in compiled.sheet.values if v.target == "spell:deathtouch"), None
    )
    if skill is None or not 10 <= skill <= 24:
        raise ValidationError("Bounded Deathtouch requires purchased skill10 through24")
    if command.operation == "start":
        if command.actor_id in tuple(
            c.actor_id for c in casts(state.resources).values() if c.status == "casting"
        ):
            raise ConflictError("Already concentrating on a hand Melee spell")
        require_idle_concentration(state.resources, command.actor_id)
        require_no_held_melee(state.resources, command.actor_id)
        if (
            command.actor_id in held_actor_ids(state.resources)
            or command.actor_id in staff_held_actor_ids(state.resources)
            or any(
                e.actor_id == command.actor_id and e.spell_id == "fireball" and e.phase == "active"
                for e in latest(state.resources).values()
            )
        ):
            raise ConflictError("Cannot cast while holding a Melee or Missile spell")
    if any(
        e.phase == "active" and command.actor_id in (e.actor_id, e.target_id)
        for e in latest(state.resources).values()
    ):
        raise ConflictError("Bounded Deathtouch does not admit other active spells")
    require_ordinary_ritual(runtime, state, command.actor_id, compiled, skill)
    require_productive_hand(runtime, state, actor_id=command.actor_id, hand=command.carrier.hand)
    return (
        skill,
        actor.approval.build_revision if actor.approval else "",
        hand_digest(state, command.actor_id, command.carrier.hand, compiled.revision),
    )


def hand_digest(state: PlayState, actor_id: str, hand: Hand, build_revision: str) -> str:
    """Pin source configuration, not mutable HP, FP or physical injury state."""
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    if actor.body is None:
        raise ConflictError("Hand Melee source requires explicit current body configuration")
    witness = mana_witness(state, actor_id)
    payload = (
        actor_id,
        hand,
        build_revision,
        actor.body.model_dump(mode="json"),
        witness.id,
        witness.kind,
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def mana_witness(state: PlayState, actor_id: str) -> ResourceEvent:
    location = next(e.location_id for e in state.world.entities if e.id == actor_id)
    return next(
        e
        for e in state.resources.events
        if e.id.startswith("melee-spell:mana:") and e.target_id == location
    )
