"""Current complete living-human Aura facts and canonical magic/control witnesses."""

import hashlib
import json
from dataclasses import asdict

from wayfarer.engine.rules.catalog import DefinitionKind
from wayfarer.engine.rules.magic.gurps_magic import magery_level
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.aura_state import (
    AuraSubjectFacts,
    AuraSubjectObservation,
    observations,
    secrets,
)
from wayfarer.engine.simulation.magic.backfires import forgotten, require_settled
from wayfarer.engine.simulation.magic.haste_state import environments
from wayfarer.engine.simulation.magic.rituals import require_ordinary_ritual
from wayfarer.engine.simulation.magic.spell_state import active_spells
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.mental_control import control_grants
from wayfarer.errors import ConflictError, ValidationError


def observation(
    runtime: RulesContext, state: PlayState, claim: AuraSubjectFacts, command_id: str
) -> AuraSubjectObservation:
    caster = next((e for e in state.world.entities if e.id == claim.caster_id), None)
    target = next((e for e in state.world.entities if e.id == claim.subject_id), None)
    actor = next((a for a in state.actors if a.actor_id == claim.subject_id), None)
    if caster is None or target is None or actor is None or actor.body is None:
        raise ValidationError("Aura requires actual actor subjects")
    if caster.location_id is None or caster.location_id != target.location_id:
        raise ConflictError("Aura touching subject is not co-located")
    if actor.body.tolerance is not None and actor.body.tolerance.structure != "living":
        raise ConflictError("Aura living-human facts contradict canonical anatomy")
    hp = next((p for p in state.resources.pools if p.id == "hp:" + claim.subject_id), None)
    if hp is None or hp.injury is None or hp.injury.dead:
        raise ConflictError("Aura living-human facts require current canonical living state")
    if hp.injury.machine or (
        hp.injury.tolerance is not None and hp.injury.tolerance.structure != "living"
    ):
        raise ConflictError("Aura living-human facts contradict canonical physiology")
    compiled = build(runtime, state, claim.subject_id)
    purchases = {p.definition_id: p.amount for p in compiled.purchases}
    level = magery_level(purchases)
    if (level >= 0) != (claim.mage_power is not None):
        raise ValidationError("Approximate mage wording must agree with purchased Magery")
    if len({s.id for s in claim.secret_traits}) != len(claim.secret_traits):
        raise ValidationError("Duplicate secret Aura fact")
    for fact in claim.secret_traits:
        if fact.definition_id is not None:
            definition = runtime.reviewer.compiler.definitions.get(fact.definition_id)
            if (
                purchases.get(fact.definition_id, 0) <= 0
                or definition is None
                or definition.kind != DefinitionKind.TRAIT
            ):
                raise ValidationError(
                    "Secret trait reference is not an actual approved trait purchase"
                )
    if any(
        g.kind == "possession" and g.controller_id == claim.subject_id
        for g in control_grants(state.resources)
    ):
        raise ConflictError("Aura does not admit displaced possessing consciousness")
    controls = tuple(
        sorted(
            g.model_dump_json()
            for g in control_grants(state.resources)
            if g.target_id == claim.subject_id
        )
    )
    return AuraSubjectObservation(
        command_id=command_id,
        subject=claim,
        location_id=caster.location_id,
        entity_json=json.dumps(asdict(target), sort_keys=True),
        body_json=actor.body.model_dump_json(),
        build_digest=hashlib.sha256(
            json.dumps(asdict(compiled), sort_keys=True, default=str).encode()
        ).hexdigest(),
        magery=level,
        control_json=controls,
        conditions=actor.conditions,
        world_facts_json=json.dumps(
            [asdict(f) for f in state.world.facts if f.subject_id == claim.subject_id],
            sort_keys=True,
        ),
    )


def current_subject(
    runtime: RulesContext, state: PlayState, subject_id: str, actor_id: str
) -> AuraSubjectObservation:
    found = next((o for o in observations(state.resources) if o.subject.id == subject_id), None)
    if found is None or found.subject.caster_id != actor_id:
        raise ConflictError("Aura subject observation is unavailable to caster")
    latest = next(
        o
        for o in reversed(observations(state.resources))
        if o.subject.subject_id == found.subject.subject_id
    )
    if latest != found:
        raise ConflictError("Aura subject facts have been superseded")
    if observation(runtime, state, found.subject, found.command_id) != found:
        raise ConflictError("Aura subject physical/build/control witness changed")
    return found


def ready(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    if runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
        raise ValidationError("Aura requires the Basic Set profile")
    guard(state, actor_id, "aura_cast")
    synchronous(state, actor_id)
    require_settled(state.resources, actor_id)
    if forgotten(state.resources, actor_id, "aura"):
        raise ConflictError("Aura is currently forgotten")
    if len(state.party.groups) > 1 or any(e.status == "active" for e in state.encounters):
        raise ConflictError("Bounded Aura requires a synchronous noncombat party")
    if any(e.actor_id == actor_id for e in active_spells(state.resources)):
        raise ConflictError("Bounded Aura does not admit existing spells on")
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    if (
        actor.available_at > state.resources.game_time
        or actor.conditions
        or not fatigue_ready(state, actor_id)
    ):
        raise ConflictError("Aura caster is unavailable")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    if hp.current <= 1 or hp.injury is None or hp.injury.incapacitated or hp.injury.stunned:
        raise ConflictError("Bounded Aura requires a healthy conscious caster")
    if hp.injury.shock:
        raise ConflictError("Bounded Aura does not admit temporal injury shock")
    fp = next(p for p in state.resources.pools if p.id == "fp:" + actor_id)
    if fp.current < 3:
        raise ConflictError("Aura requires its full three FP before dice")
    compiled = build(runtime, state, actor_id)
    skill = next((v.value for v in compiled.sheet.values if v.target == "spell:aura"), None)
    purchased = any(p.definition_id == "spell:aura" and p.amount > 0 for p in compiled.purchases)
    if not purchased or skill is None or not 10 <= skill <= 14:
        raise ValidationError("Bounded Aura requires purchased skill 10 through 14")
    caster = next(e for e in state.world.entities if e.id == actor_id)
    if environments(state.resources).get(caster.location_id or "") != "normal":
        raise ValidationError("Aura requires authenticated normal mana")
    if any(
        s.actor_id == actor_id and s.at // 86400 == state.resources.game_time // 86400
        for s in secrets(state.resources)
    ):
        raise ConflictError("Caster already attempted Aura today")
    require_ordinary_ritual(runtime, state, actor_id, compiled, int(skill))
    return int(skill)
