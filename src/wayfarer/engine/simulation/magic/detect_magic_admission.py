"""Current physical subjects and magic truth derived from canonical producers."""

import hashlib

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.backfires import forgotten, require_settled
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.detect_magic_state import (
    Detection,
    DetectObservation,
    DetectSubject,
)
from wayfarer.engine.simulation.magic.haste_state import environments
from wayfarer.engine.simulation.magic.item_state import item_magic_lost
from wayfarer.engine.simulation.magic.lock_state import latest as locks
from wayfarer.engine.simulation.magic.lock_state import validate_fixture
from wayfarer.engine.simulation.magic.rituals import require_ordinary_ritual
from wayfarer.engine.simulation.magic.spell_state import active_spells
from wayfarer.engine.simulation.resources import is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def subject(runtime: RulesContext, state: PlayState, claim: DetectSubject) -> DetectObservation:
    caster = next((e for e in state.world.entities if e.id == claim.caster_id), None)
    if caster is None or caster.location_id is None:
        raise ValidationError("Detect Magic requires a current placed caster")
    if claim.carrier == "inventory":
        item = next((i for i in state.resources.items if i.id == claim.target_id), None)
        if (
            item is None
            or item.owner_id != claim.caster_id
            or not is_carried(state.resources, item)
            or item.container_id is not None
            or item.quantity != 1
            or item.condition is not None
            and item.condition.disabled
        ):
            raise ValidationError("Detect Magic requires one intact accessible owned physical item")
        entry = (
            next(
                (
                    e
                    for e in runtime.rules.combat.gurps_equipment.entries
                    if e.definition_id == item.definition_id
                ),
                None,
            )
            if (runtime.rules.combat and runtime.rules.combat.gurps_equipment)
            else None
        )
        if entry is None:
            raise ValidationError("Detect Magic requires its pinned physical equipment profile")
        if runtime.rules.spells and any(
            binding.item_id == item.id for binding in runtime.rules.spells.magic_items
        ):
            raise ValidationError("Detect Magic does not admit configured magic item carriers")
        if any(lock.fixture.item_id == item.id for lock in locks(state.resources).values()):
            raise ValidationError("Lock-backed items require the explicit physical lock carrier")
        if any(e.target_id == item.id for e in active_spells(state.resources)):
            raise ValidationError("Unknown temporary inventory magic carrier")
        if any(
            p.target_item_id == item.id and p.status != "completed"
            for p in state.resources.enchantment_projects
        ):
            raise ValidationError(
                "Detect Magic does not admit unfinished or failed enchantment carriers"
            )
        physical = digest(item.model_dump_json() + entry.model_dump_json())
    else:
        lock = locks(state.resources).get(claim.target_id)
        if lock is None:
            raise ValidationError("Detect Magic requires a real admitted physical lock")
        if lock.fixture.item_id is not None:
            raise ValidationError(
                "Detect Magic does not yet join lock-backed inventory magic carriers"
            )
        validate_fixture(state.world, state.resources, lock.fixture)
        if lock.fixture.location_id != caster.location_id:
            raise ValidationError("The physical lock is not currently touched here")
        physical = digest(lock.model_dump_json())
    # Validate the complete supported magic carrier during admission, never a GM truth assertion.
    truth(state, claim, physical)
    return DetectObservation(
        command_id="observation",
        subject=claim,
        physical_digest=physical,
        location_id=caster.location_id,
    )


def truth(state: PlayState, claim: DetectSubject, physical: str) -> Detection:
    if claim.carrier == "inventory":
        item = next(i for i in state.resources.items if i.id == claim.target_id)
        if any(item_magic_lost(state.resources, item.id, b.id) for b in item.enchantments):
            raise ValidationError("Detect Magic does not admit lost enchantment carriers")
        if not item.enchantments:
            return Detection(
                actor_id=claim.caster_id, target_id=item.id, identity=physical, magical=False
            )
        if len(item.enchantments) != 1:
            raise ValidationError("Detect Magic does not admit multiple or concealed enchantments")
        binding = item.enchantments[0]
        project = next(
            (p for p in state.resources.enchantment_projects if p.id == binding.project_id), None
        )
        if (
            binding.spell_id != "haste"
            or binding.runtime_family != "spell"
            or project is None
            or project.status != "completed"
            or project.magic_item_binding_id != binding.id
        ):
            raise ValidationError("Detect Magic requires real completed familiar Haste provenance")
        return Detection(
            actor_id=claim.caster_id,
            target_id=item.id,
            identity=digest(physical + binding.model_dump_json()),
            magical=True,
            permanence="permanent",
            spell_id="haste",
            power=binding.power,
            binding_id=binding.id,
            project_id=binding.project_id,
        )
    effects = tuple(e for e in active_spells(state.resources) if e.target_id == claim.target_id)
    if not effects:
        return Detection(
            actor_id=claim.caster_id, target_id=claim.target_id, identity=physical, magical=False
        )
    if len(effects) != 1 or effects[0].spell_id != "magelock" or not effects[0].execute_effects:
        raise ValidationError("Detect Magic supports only one real familiar temporary Magelock")
    effect = effects[0]
    return Detection(
        actor_id=claim.caster_id,
        target_id=claim.target_id,
        identity=digest(physical + effect.model_dump_json()),
        magical=True,
        permanence="temporary",
        spell_id="magelock",
    )


def require_current(runtime: RulesContext, state: PlayState, value: DetectObservation) -> None:
    now = subject(runtime, state, value.subject)
    if (now.physical_digest, now.location_id) != (value.physical_digest, value.location_id):
        raise ConflictError("Detect Magic physical identity, touch or profile changed")


def ready(runtime: RulesContext, state: PlayState, actor_id: str, *, starting: bool = False) -> int:
    if runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
        raise ValidationError("Detect Magic requires the exact Basic Set profile")
    guard(state, actor_id, "detect_magic_work")
    resources = state.resources
    if (
        resources.scheduled
        or resources.hazards
        or resources.cyclic_attacks
        or resources.cyclic_exposures
        or resources.toxins
        or resources.dependencies
        or resources.survival_tasks
        or resources.illnesses
        or resources.recovery_tasks
        or runtime.rules.npcs is not None
    ):
        raise ConflictError("Bounded Detect Magic does not admit timed hazard carriers")
    synchronous(state, actor_id)
    if state.party.groups and len(state.party.groups) != 1:
        raise ConflictError("Detect Magic requires one synchronous party")
    if any(e.status == "active" for e in state.encounters):
        raise ConflictError("Bounded Detect Magic is unavailable during combat")
    require_settled(state.resources, actor_id)
    if forgotten(state.resources, actor_id, "detect-magic"):
        raise ConflictError("Detect Magic is currently forgotten")
    if starting:
        require_idle_concentration(state.resources, actor_id)
    if any(effect.actor_id == actor_id for effect in active_spells(state.resources)):
        raise ConflictError("Bounded Detect Magic does not admit existing caster spells on")
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    fp = next(p for p in state.resources.pools if p.id == "fp:" + actor_id)
    if starting and hp.injury is not None and hp.injury.shock:
        raise ConflictError("Bounded Detect Magic cannot start during active shock")
    if (
        actor.available_at > state.resources.game_time
        or actor.conditions
        or not fatigue_ready(state, actor_id)
        or hp.current <= 1
        or hp.injury is None
        or hp.injury.incapacitated
        or hp.injury.stunned
        or fp.current < 2
    ):
        raise ConflictError("Detect Magic requires healthy available caster and reserved energy")
    compiled = build(runtime, state, actor_id)
    skill = next((v.value for v in compiled.sheet.values if v.target == "spell:detect-magic"), None)
    purchased = any(
        p.definition_id == "spell:detect-magic" and p.amount > 0 for p in compiled.purchases
    )
    if not purchased or skill is None or not 10 <= skill <= 14:
        raise ValidationError("Detect Magic requires purchased skill ten through fourteen")
    location = next(e.location_id for e in state.world.entities if e.id == actor_id)
    if environments(state.resources).get(location or "") != "normal":
        raise ValidationError("Detect Magic requires authenticated normal mana")
    require_ordinary_ritual(runtime, state, actor_id, compiled, int(skill))
    return int(skill)
