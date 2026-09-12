"""Exactly-once enchanting project transitions and magic-item compilation."""

from __future__ import annotations

import hashlib
from math import ceil
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.magic.protocols import MagicItemInstance
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.magic.enchanting import (
    EnchantingRules,
    EnchantmentInterruption,
    EnchantmentMaterial,
    EnchantmentProject,
    EnchantmentRecipe,
    EnchantmentWork,
    EnergyContribution,
    busy_actor_ids,
)
from wayfarer.engine.simulation.resources import Item, Receipt, ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

MAGE_DAY = 8 * 60 * 60


class EnchantmentCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class CreateEnchantment(EnchantmentCommand):
    kind: Literal["create"] = "create"
    project_id: Id
    recipe_id: Id
    target_item_id: Id
    enchanter_ids: tuple[Id, ...] = Field(min_length=1)


class BeginEnchanting(EnchantmentCommand):
    kind: Literal["begin"] = "begin"
    project_id: Id
    contributions: tuple[EnergyContribution, ...] = ()


class InterruptEnchanting(EnchantmentCommand):
    kind: Literal["interrupt"] = "interrupt"
    project_id: Id


class SettleEnchanting(EnchantmentCommand):
    kind: Literal["settle"] = "settle"
    project_id: Id
    work_id: Id


class AbandonEnchantment(EnchantmentCommand):
    kind: Literal["abandon"] = "abandon"
    project_id: Id


TypedEnchantmentCommand = Annotated[
    CreateEnchantment
    | BeginEnchanting
    | InterruptEnchanting
    | SettleEnchanting
    | AbandonEnchantment,
    Field(discriminator="kind"),
]
COMMAND_ADAPTER: TypeAdapter[TypedEnchantmentCommand] = TypeAdapter(TypedEnchantmentCommand)


class EnchantmentOutcome(Record):
    command_id: Id
    project_id: Id
    status: str
    energy_completed: int = Field(ge=0)
    check: CheckTrace | None = None
    binding_id: Id | None = None


def _digest(command: EnchantmentCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _prior(resources: ResourceState, command: EnchantmentCommand) -> EnchantmentOutcome | None:
    receipt = next((r for r in resources.receipts if r.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Enchanting command ID reused with a different payload")
    event = next(e for e in resources.events if e.id == "enchantment:" + command.id)
    return EnchantmentOutcome.model_validate_json(event.kind)


def _recipe(rules: EnchantingRules, identifier: str) -> EnchantmentRecipe:
    recipe = next((r for r in rules.recipes if r.id == identifier), None)
    if recipe is None:
        raise ValidationError("Unknown authored enchantment recipe")
    return recipe


def _project(resources: ResourceState, identifier: str, actor_id: str) -> EnchantmentProject:
    project = next((p for p in resources.enchantment_projects if p.id == identifier), None)
    if project is None or project.owner_id != actor_id:
        raise ValidationError("Enchantment project is not owned by this actor")
    return project


def _replace(resources: ResourceState, project: EnchantmentProject) -> ResourceState:
    return resources.model_copy(
        update={
            "enchantment_projects": tuple(
                p for p in resources.enchantment_projects if p.id != project.id
            )
            + (project,)
        }
    )


def _record(
    state: PlayState,
    command: EnchantmentCommand,
    project: EnchantmentProject,
    outcome: EnchantmentOutcome,
) -> PlayState:
    revision = state.revision + 1
    resources = _replace(state.resources, project).model_copy(
        update={
            "revision": revision,
            "receipts": state.resources.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": state.resources.events
            + (
                ResourceEvent(
                    id="enchantment:" + command.id,
                    at=state.resources.game_time,
                    target_id=project.id,
                    kind=outcome.model_dump_json(),
                ),
            ),
        }
    )
    return state.model_copy(update={"revision": revision, "resources": resources})


def _levels(
    runtime: RulesContext, state: PlayState, recipe: EnchantmentRecipe, actor_id: str
) -> int:
    actor = next((a for a in state.actors if a.actor_id == actor_id), None)
    if actor is None or actor.approval is None:
        raise ValidationError("Enchanter requires an approved build")
    build, _ = runtime.reviewer.activate(
        actor.proposal,
        actor.approval,
        campaign_id=state.campaign_id,
        actor_id=actor_id,
    )
    values = {v.target: int(v.value) for v in build.sheet.values}
    enchant = values.get("spell:enchant")
    effect = values.get(recipe.spell_id)
    if enchant is None or effect is None:
        raise ValidationError("Enchanter lacks the cataloged Enchant or effect spell")
    return min(enchant, effect)


def _validate_bindings(
    runtime: RulesContext,
    state: PlayState,
    recipe: EnchantmentRecipe,
    target_item_id: str,
    enchanter_ids: tuple[str, ...],
) -> int:
    if len(set(enchanter_ids)) != len(enchanter_ids):
        raise ValidationError("Duplicate project enchanter")
    entities = {entity.id: entity for entity in state.world.entities}
    leader = entities.get(enchanter_ids[0])
    if leader is None or any(
        entities.get(actor) is None or entities[actor].location_id != leader.location_id
        for actor in enchanter_ids
    ):
        raise ValidationError("All enchanters must share the enchanting workspace location")
    target = next((i for i in state.resources.items if i.id == target_item_id), None)
    if (
        target is None
        or target.owner_id != enchanter_ids[0]
        or target.quantity != 1
        or target.definition_id not in recipe.target_definition_ids
        or target.definition_id not in runtime.resources.specs
        or target.ground is not None
        or (target.condition is not None and target.condition.disabled)
    ):
        raise ValidationError("Target item is not suitable for this enchantment recipe")
    if not any(
        i.owner_id == enchanter_ids[0]
        and i.definition_id == recipe.workspace_definition_id
        and i.ground is None
        and (i.condition is None or not i.condition.disabled)
        for i in state.resources.items
    ):
        raise ValidationError("Required enchanting workspace is unavailable")
    minimum = 20 if recipe.mana == "low" else 15
    levels = tuple(_levels(runtime, state, recipe, actor) for actor in enchanter_ids)
    if any(level < minimum for level in levels):
        raise ValidationError(f"Every enchanter requires both spells at {minimum}+")
    power = min(levels)
    effective_power = power - (len(enchanter_ids) - 1 if recipe.method == "quick-and-dirty" else 0)
    if effective_power < 15:
        raise ValidationError("Assistant penalty leaves magic-item Power below 15")
    return power


def _consume_materials(
    resources: ResourceState, owner_id: str, materials: tuple[EnchantmentMaterial, ...]
) -> ResourceState:
    available: dict[str, int] = {}
    for item in resources.items:
        if item.owner_id == owner_id and item.ground is None and not item.equipped:
            available[item.definition_id] = available.get(item.definition_id, 0) + item.quantity
    if any(available.get(m.definition_id, 0) < m.quantity for m in materials):
        raise ValidationError("Enchanting material shortfall")
    remaining = {m.definition_id: m.quantity for m in materials}
    kept: list[Item] = []
    for item in resources.items:
        wanted = remaining.get(item.definition_id, 0)
        if wanted and item.owner_id == owner_id and item.ground is None and not item.equipped:
            used = min(wanted, item.quantity)
            remaining[item.definition_id] -= used
            if used < item.quantity:
                kept.append(item.model_copy(update={"quantity": item.quantity - used}))
        else:
            kept.append(item)
    return resources.model_copy(update={"items": tuple(kept)})


def _energy_preflight(
    resources: ResourceState,
    recipe: EnchantmentRecipe,
    project: EnchantmentProject,
    contributions: tuple[EnergyContribution, ...],
) -> None:
    if recipe.method == "slow-and-sure":
        if contributions:
            raise ValidationError("Slow and Sure enchanting does not spend FP or HP")
        return
    if len({c.actor_id for c in contributions}) != len(contributions):
        raise ValidationError("Duplicate enchanting energy contribution")
    if {c.actor_id for c in contributions} != set(project.enchanter_ids):
        raise ValidationError("Quick and Dirty requires an explicit contribution per enchanter")
    if sum(c.energy for c in contributions) != recipe.energy_required:
        raise ValidationError("Quick and Dirty contributions must equal required energy")
    pools = {p.id: p for p in resources.pools}
    for contribution in contributions:
        fp, hp = pools.get("fp:" + contribution.actor_id), pools.get("hp:" + contribution.actor_id)
        if fp is None or fp.fatigue is None or hp is None or hp.injury is None:
            raise ValidationError("Enchanter energy pools are unavailable")
        if fp.current < contribution.fp or hp.current - contribution.hp < -hp.maximum:
            raise ValidationError("Enchanter cannot supply promised energy")


def _spend_energy(
    resources: ResourceState, contributions: tuple[EnergyContribution, ...]
) -> ResourceState:
    fp = {c.actor_id: c.fp for c in contributions}
    hp = {c.actor_id: c.hp for c in contributions}
    return resources.model_copy(
        update={
            "pools": tuple(
                pool.model_copy(update={"current": pool.current - fp[pool.id[3:]]})
                if pool.id.startswith("fp:") and pool.id[3:] in fp
                else pool.model_copy(update={"current": pool.current - hp[pool.id[3:]]})
                if pool.id.startswith("hp:") and pool.id[3:] in hp
                else pool
                for pool in resources.pools
            )
        }
    )


def _begin(
    runtime: RulesContext,
    state: PlayState,
    command: BeginEnchanting,
    project: EnchantmentProject,
    recipe: EnchantmentRecipe,
) -> tuple[PlayState, EnchantmentProject, EnchantmentOutcome]:
    if project.status not in ("active", "interrupted") or project.active_work is not None:
        raise ConflictError("Enchantment project is not available for work")
    for actor_id in project.enchanter_ids:
        synchronous(state, actor_id)
    if busy_actor_ids(state.resources.enchantment_projects) & set(project.enchanter_ids):
        raise ConflictError("An enchanter already has active project work")
    _validate_bindings(runtime, state, recipe, project.target_item_id, project.enchanter_ids)
    _energy_preflight(state.resources, recipe, project, command.contributions)
    remaining = recipe.energy_required - project.energy_completed
    duration = (
        ceil(recipe.energy_required / 100) * 3600
        if recipe.method == "quick-and-dirty"
        else ceil(remaining / len(project.enchanter_ids)) * MAGE_DAY + project.delay_seconds
    )
    work = EnchantmentWork(
        id=command.id,
        start=state.resources.game_time,
        due=state.resources.game_time + duration,
        enchanter_ids=project.enchanter_ids,
        contributions=command.contributions,
    )
    project = project.model_copy(
        update={"status": "active", "active_work": work, "delay_seconds": 0}
    )
    return (
        state,
        project,
        EnchantmentOutcome(
            command_id=command.id,
            project_id=project.id,
            status="work-started",
            energy_completed=project.energy_completed,
        ),
    )


def _interrupt(
    state: PlayState,
    command: InterruptEnchanting,
    project: EnchantmentProject,
    recipe: EnchantmentRecipe,
) -> tuple[EnchantmentProject, EnchantmentOutcome]:
    work = project.active_work
    if project.status != "active" or work is None:
        raise ConflictError("Enchantment project has no active work to interrupt")
    if state.resources.game_time >= work.due:
        raise ConflictError("Completed enchanting work must be settled, not interrupted")
    credited = 0
    delay = 0
    if recipe.method == "slow-and-sure":
        full_days = max(0, state.resources.game_time - work.start) // MAGE_DAY
        credited = min(
            recipe.energy_required - project.energy_completed,
            full_days * len(project.enchanter_ids),
        )
        delay = 2 * MAGE_DAY
    interruption = EnchantmentInterruption(
        command_id=command.id,
        at=state.resources.game_time,
        credited_energy=credited,
        lost_seconds=delay,
    )
    project = project.model_copy(
        update={
            "status": "interrupted",
            "energy_completed": project.energy_completed + credited,
            "delay_seconds": project.delay_seconds + delay,
            "active_work": None,
            "interruptions": project.interruptions + (interruption,),
        }
    )
    return project, EnchantmentOutcome(
        command_id=command.id,
        project_id=project.id,
        status="interrupted",
        energy_completed=project.energy_completed,
    )


def _settle(
    runtime: RulesContext,
    state: PlayState,
    command: SettleEnchanting,
    project: EnchantmentProject,
    recipe: EnchantmentRecipe,
    power: int,
) -> tuple[PlayState, EnchantmentProject, EnchantmentOutcome]:
    work = project.active_work
    if work is None or work.id != command.work_id:
        raise ConflictError("Enchanting work receipt is not active")
    if state.resources.game_time < work.due:
        raise ConflictError("Enchanting work has not reached its shared-clock deadline")
    _energy_preflight(state.resources, recipe, project, work.contributions)
    resources = (
        _spend_energy(state.resources, work.contributions)
        if recipe.method == "quick-and-dirty"
        else state.resources
    )
    modifiers = (
        (
            Modifier(
                -(len(project.enchanter_ids) - 1),
                "Quick and Dirty assistants",
                "campaigns:b481:quick-and-dirty",
                "gurps-basic-set-4e-2004",
                ModifierKind.SITUATIONAL,
            ),
        )
        if len(project.enchanter_ids) > 1 and recipe.method == "quick-and-dirty"
        else ()
    )
    check = success_roll("gurps-basic-set-4e-2004", power, modifiers, rng=runtime.rng)
    completed = recipe.energy_required
    binding_id = None
    status = "failed"
    if check.outcome.succeeded:
        target = next(i for i in resources.items if i.id == project.target_item_id)
        binding_id = "magic-item:" + project.id
        item_power = power - (
            len(project.enchanter_ids) - 1 if recipe.method == "quick-and-dirty" else 0
        )
        instance = MagicItemInstance(
            id=binding_id,
            item_id=target.id,
            spell_id=recipe.runtime_spell_id or recipe.spell_id.removeprefix("spell:"),
            power=item_power,
            power_reduction=recipe.power_reduction,
            requires_magery=recipe.requires_magery,
            always_on=recipe.activation == "always-on",
            project_id=project.id,
            recipe_id=recipe.id,
            effect_id=recipe.effect_id,
            activation=recipe.activation,
            runtime_family=recipe.runtime_family,
            owner_id=target.owner_id,
            created_at=resources.game_time,
            method=recipe.method,
            maximum_charges=recipe.maximum_charges,
            charges=recipe.maximum_charges,
            maintenance_energy=recipe.maintenance_energy,
        )
        target = target.model_copy(update={"enchantments": target.enchantments + (instance,)})
        resources = resources.model_copy(
            update={"items": tuple(target if i.id == target.id else i for i in resources.items)}
        )
        status = "completed"
    elif check.outcome is Outcome.CRITICAL_FAILURE:
        destroyed = next(i for i in resources.items if i.id == project.target_item_id)
        resources = resources.model_copy(
            update={
                "items": tuple(i for i in resources.items if i.id != destroyed.id),
                "expended_items": resources.expended_items + (destroyed,),
            }
        )
        status = "critical-failure"
    project = project.model_copy(
        update={
            "status": "completed" if check.outcome.succeeded else "failed",
            "energy_completed": completed,
            "active_work": None,
            "check": check,
            "magic_item_binding_id": binding_id,
        }
    )
    state = state.model_copy(update={"resources": resources})
    return (
        state,
        project,
        EnchantmentOutcome(
            command_id=command.id,
            project_id=project.id,
            status=status,
            energy_completed=completed,
            check=check,
            binding_id=binding_id,
        ),
    )


def apply_enchantment(
    runtime: RulesContext,
    state: PlayState,
    command: TypedEnchantmentCommand,
    *,
    system: bool = False,
) -> tuple[PlayState, EnchantmentOutcome]:
    """Apply one project command; persisted receipts suppress repeated costs and rolls."""
    if not system:
        raise ValidationError("Enchantment projects require engine authority")
    rules = runtime.rules.enchanting
    if rules is None:
        raise ValidationError("Campaign has no authored enchanting rules")
    prior = _prior(state.resources, command)
    if prior is not None:
        return state, prior
    if command.expected_revision != state.revision or state.revision != state.resources.revision:
        raise ConflictError("Enchantment project revision changed")
    if isinstance(command, CreateEnchantment):
        if any(p.id == command.project_id for p in state.resources.enchantment_projects):
            raise ConflictError("Enchantment project ID already exists")
        if command.actor_id != command.enchanter_ids[0]:
            raise ValidationError("Project owner must be the lead enchanter")
        if any(
            p.target_item_id == command.target_item_id and p.status in ("active", "interrupted")
            for p in state.resources.enchantment_projects
        ):
            raise ConflictError("Target item already has an unfinished enchantment")
        recipe = _recipe(rules, command.recipe_id)
        _validate_bindings(runtime, state, recipe, command.target_item_id, command.enchanter_ids)
        resources = _consume_materials(state.resources, command.actor_id, recipe.materials)
        state = state.model_copy(update={"resources": resources})
        project = EnchantmentProject(
            id=command.project_id,
            owner_id=command.actor_id,
            recipe_id=recipe.id,
            target_item_id=command.target_item_id,
            enchanter_ids=command.enchanter_ids,
            materials_spent=recipe.materials,
        )
        outcome = EnchantmentOutcome(
            command_id=command.id,
            project_id=project.id,
            status="created",
            energy_completed=0,
        )
    else:
        project = _project(state.resources, command.project_id, command.actor_id)
        recipe = _recipe(rules, project.recipe_id)
        if isinstance(command, BeginEnchanting):
            state, project, outcome = _begin(runtime, state, command, project, recipe)
        elif isinstance(command, InterruptEnchanting):
            project, outcome = _interrupt(state, command, project, recipe)
        elif isinstance(command, AbandonEnchantment):
            if project.status not in ("active", "interrupted"):
                raise ConflictError("Only an unfinished enchantment can be abandoned")
            project = project.model_copy(update={"status": "abandoned", "active_work": None})
            outcome = EnchantmentOutcome(
                command_id=command.id,
                project_id=project.id,
                status="abandoned",
                energy_completed=project.energy_completed,
            )
        else:
            assert isinstance(command, SettleEnchanting)
            power = _validate_bindings(
                runtime, state, recipe, project.target_item_id, project.enchanter_ids
            )
            state, project, outcome = _settle(runtime, state, command, project, recipe, power)
    state = _record(state, command, project, outcome)
    return state, outcome
