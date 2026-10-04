"""Approved B244 learning and current supported living target witnesses."""

import hashlib
import json
from dataclasses import asdict

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.backfires import forgotten, require_settled
from wayfarer.engine.simulation.magic.concentration import (
    require_idle_concentration,
    require_no_held_melee,
)
from wayfarer.engine.simulation.magic.haste_state import environments
from wayfarer.engine.simulation.magic.rituals import require_ordinary_ritual
from wayfarer.engine.simulation.magic.rooted_feet_state import (
    RootedFeetObservation,
    RootedFeetSubject,
    active_effect,
    observations,
)
from wayfarer.engine.simulation.magic.spell_state import active_spells
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.size_forms import require_native_size
from wayfarer.errors import ConflictError, ValidationError


def target_strength(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    require_native_size(state.resources, actor_id)
    if (
        actor.body is None
        or hp.injury is None
        or hp.injury.anatomy != "human"
        or hp.injury.incapacitated
        or hp.injury.stunned
        or hp.injury.prone
        or hp.injury.machine
    ):
        raise ConflictError("Rooted Feet requires current living human anatomy")
    if actor.body.tolerance is not None or hp.injury.tolerance is not None:
        raise ConflictError("Rooted Feet does not admit transformed anatomy")
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    if any(p.definition_id == "trait:size-modifier" and p.amount != 0 for p in compiled.purchases):
        raise ConflictError("Rooted Feet first carrier requires size modifier zero")
    if any(
        p.amount > 0 and p.definition_id in ("trait:magic-resistance", "advantage:magic-resistance")
        for p in compiled.purchases
    ):
        raise ConflictError("Rooted Feet does not yet admit Magic Resistance")
    if any(
        e.actor_id == actor_id or e.target_id == actor_id for e in active_spells(state.resources)
    ):
        raise ConflictError("Rooted Feet does not admit other active spell carriers")
    fp = next(p for p in state.resources.pools if p.id == "fp:" + actor_id)
    return fatigue_value(fp, compiled.statistics.st)


def observation(
    runtime: RulesContext, state: PlayState, claim: RootedFeetSubject, command_id: str
) -> RootedFeetObservation:
    caster = next((e for e in state.world.entities if e.id == claim.caster_id), None)
    target = next((e for e in state.world.entities if e.id == claim.target_id), None)
    if (
        caster is None
        or target is None
        or caster.location_id is None
        or caster.location_id != target.location_id
    ):
        raise ConflictError("Rooted Feet requires current co-located touch")
    target_strength(runtime, state, claim.target_id)
    actor = next(a for a in state.actors if a.actor_id == claim.target_id)
    if actor.conditions or any(e.status == "active" for e in state.encounters):
        raise ConflictError("Rooted Feet casting requires ordinary noncombat standing target")
    witness = json.dumps(
        {
            "entity": asdict(target),
            "caster": asdict(caster),
            "body": actor.body.model_dump(mode="json") if actor.body else None,
            "proposal": actor.proposal.model_dump(mode="json"),
            "approval": actor.approval.model_dump(mode="json") if actor.approval else None,
            "facts": [asdict(f) for f in state.world.facts if f.subject_id == claim.target_id],
        },
        sort_keys=True,
    )
    return RootedFeetObservation(
        command_id=command_id, subject=claim, witness=hashlib.sha256(witness.encode()).hexdigest()
    )


def current_subject(
    runtime: RulesContext, state: PlayState, subject_id: str, caster_id: str
) -> RootedFeetObservation:
    found = next((o for o in observations(state.resources) if o.subject.id == subject_id), None)
    if found is None or found.subject.caster_id != caster_id:
        raise ConflictError("Rooted Feet observation is unavailable to caster")
    latest = next(
        o
        for o in reversed(observations(state.resources))
        if o.subject.target_id == found.subject.target_id
    )
    if latest != found or observation(runtime, state, found.subject, found.command_id) != found:
        raise ConflictError("Rooted Feet subject witness changed")
    if active_effect(state.resources, found.subject.target_id) is not None:
        raise ConflictError("Rooted Feet does not admit overlapping roots")
    return found


def ready(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    guard(state, actor_id, "rooted_feet_cast")
    synchronous(state, actor_id)
    require_settled(state.resources, actor_id)
    require_idle_concentration(state.resources, actor_id)
    require_no_held_melee(state.resources, actor_id)
    if forgotten(state.resources, actor_id, "rooted-feet"):
        raise ConflictError("Rooted Feet is currently forgotten")
    if len(state.party.groups) > 1 or any(e.status == "active" for e in state.encounters):
        raise ConflictError("Rooted Feet casting requires synchronous noncombat party")
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    fp = next(p for p in state.resources.pools if p.id == "fp:" + actor_id)
    if (
        actor.available_at > state.resources.game_time
        or actor.conditions
        or not fatigue_ready(state, actor_id)
        or hp.injury is None
        or hp.injury.incapacitated
        or hp.injury.stunned
        or hp.injury.shock
        or hp.current <= 1
        or fp.current < 3
    ):
        raise ConflictError("Rooted Feet requires healthy available caster and three FP")
    r = state.resources
    if (
        r.scheduled
        or r.hazards
        or r.cyclic_attacks
        or r.cyclic_exposures
        or r.toxins
        or r.dependencies
        or r.survival_tasks
        or r.illnesses
        or r.recovery_tasks
        or runtime.rules.npcs is not None
    ):
        raise ConflictError("Rooted Feet does not admit timed hazard carriers")
    if any(e.actor_id == actor_id for e in active_spells(r)):
        raise ConflictError("Rooted Feet does not admit existing caster spells on")
    compiled = build(runtime, state, actor_id)
    skill = next((v.value for v in compiled.sheet.values if v.target == "spell:rooted-feet"), None)
    if (
        runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004"
        or not any(
            p.definition_id == "spell:rooted-feet" and p.amount > 0 for p in compiled.purchases
        )
        or skill is None
        or not 10 <= skill <= 14
    ):
        raise ValidationError("Rooted Feet requires purchased Basic Set skill ten through fourteen")
    location = next(e.location_id for e in state.world.entities if e.id == actor_id)
    if environments(r).get(location or "") != "normal":
        raise ConflictError("Rooted Feet requires authenticated normal mana")
    require_ordinary_ritual(runtime, state, actor_id, compiled, int(skill))
    return int(skill)
