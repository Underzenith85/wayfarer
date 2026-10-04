"""Authenticated single familiar, unconcealed critical Haste subject admission."""

import hashlib

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    AnalysisCast,
    AnalyzeSubject,
    SubjectObservation,
    observations,
    secrets,
)
from wayfarer.engine.simulation.magic.concentration import require_no_held_melee
from wayfarer.engine.simulation.magic.haste_state import environments
from wayfarer.engine.simulation.magic.spell_state import active_spells
from wayfarer.engine.simulation.resources import Item, is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def subject(runtime: RulesContext, state: PlayState, claim: AnalyzeSubject) -> SubjectObservation:
    actor = next((a for a in state.actors if a.actor_id == claim.caster_id), None)
    entity = next((e for e in state.world.entities if e.id == claim.caster_id), None)
    item = next((i for i in state.resources.items if i.id == claim.item_id), None)
    if actor is None or entity is None or entity.location_id is None or item is None:
        raise ValidationError("Analyze Magic requires a current caster and physical subject")
    if (
        item.owner_id != claim.caster_id
        or not is_carried(state.resources, item)
        or item.container_id is not None
    ):
        raise ValidationError(
            "Bounded analysis requires the caster's currently accessible carried item"
        )
    if (
        item.quantity != 1
        or (item.condition is not None and item.condition.disabled)
        or len(item.enchantments) != 1
    ):
        raise ValidationError("Analyze Magic supports one intact single-binding subject")
    binding = item.enchantments[0]
    if binding.spell_id != "haste" or binding.runtime_family != "spell":
        raise ValidationError("Bounded analysis supports a familiar Haste enchantment")
    project = next(
        (p for p in state.resources.enchantment_projects if p.id == binding.project_id), None
    )
    if (
        project is None
        or project.status != "completed"
        or project.magic_item_binding_id != binding.id
    ):
        raise ValidationError("Analyze Magic requires the real completed project provenance")
    if not any(
        e.id.startswith("enchantment-power:") and e.target_id == project.id
        for e in state.resources.events
    ):
        raise ValidationError("Bounded analysis requires actual unknown critical Power")
    entry = (
        next(
            (
                e
                for e in runtime.rules.combat.gurps_equipment.entries
                if e.definition_id == item.definition_id
            ),
            None,
        )
        if runtime.rules.combat and runtime.rules.combat.gurps_equipment
        else None
    )
    if entry is None:
        raise ValidationError("Analyze Magic requires a pinned physical equipment profile")
    return SubjectObservation(
        command_id="observation",
        subject=claim,
        binding=binding,
        item_digest=digest(item.model_dump_json()),
        definition_digest=digest(entry.model_dump_json()),
        location_id=entity.location_id,
    )


def observed(state: PlayState, identifier: str) -> SubjectObservation:
    found = tuple(o for o in observations(state.resources) if o.subject.id == identifier)
    if len(found) != 1:
        raise ValidationError("Analyze Magic requires one authenticated physical observation")
    return found[0]


def require_current(
    runtime: RulesContext, state: PlayState, value: SubjectObservation | AnalysisCast
) -> Item:
    claim = (
        value.subject
        if isinstance(value, SubjectObservation)
        else observed(state, value.subject_id).subject
    )
    current = subject(runtime, state, claim)
    if (current.binding, current.item_digest, current.definition_digest, current.location_id) != (
        value.binding,
        value.item_digest,
        value.definition_digest,
        value.location_id,
    ):
        raise ConflictError("Analyze Magic subject identity, touch or profile changed")
    return next(i for i in state.resources.items if i.id == claim.item_id)


def ready(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    if runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
        raise ValidationError("Analyze Magic requires the exact Basic Set profile")
    guard(state, actor_id, "analyze_magic_work")
    require_no_held_melee(state.resources, actor_id)
    synchronous(state, actor_id)
    if state.party.groups and len(state.party.groups) != 1:
        raise ConflictError("Bounded Analyze Magic requires one synchronous party")
    if any(e.status == "active" for e in state.encounters):
        raise ConflictError("Hour-long analysis is unavailable during an active encounter")
    if any(effect.actor_id == actor_id for effect in active_spells(state.resources)):
        raise ConflictError("Bounded Analyze Magic does not admit existing spells on")
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    if (
        actor.available_at > state.resources.game_time
        or actor.conditions
        or not fatigue_ready(state, actor_id)
    ):
        raise ConflictError("Caster is unavailable for uninterrupted analysis")
    compiled = build(runtime, state, actor_id)
    skill = next(
        (v.value for v in compiled.sheet.values if v.target == "spell:analyze-magic"), None
    )
    if skill is None or not 10 <= skill <= 14:
        raise ValidationError("Bounded Analyze Magic requires purchased skill 10 through 14")
    entity = next(e for e in state.world.entities if e.id == actor_id)
    if (
        entity.location_id is None
        or environments(state.resources).get(entity.location_id) != "normal"
    ):
        raise ValidationError("Bounded Analyze Magic requires authenticated normal mana")
    return int(skill)


def daily(state: PlayState, actor_id: str) -> None:
    if any(
        s.actor_id == actor_id and s.at // 86400 == state.resources.game_time // 86400
        for s in secrets(state.resources)
    ):
        raise ConflictError("Information spell attempt is already used today")


def energy(state: PlayState, actor_id: str) -> None:
    fp = next(p for p in state.resources.pools if p.id == "fp:" + actor_id)
    if fp.current < 8:
        raise ConflictError("Analyze Magic requires eight available FP")
