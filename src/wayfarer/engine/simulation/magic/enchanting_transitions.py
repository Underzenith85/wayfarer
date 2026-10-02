"""Exactly-once enchanting project transitions and magic-item compilation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, Outcome, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.magic.ceremonial import replay_ceremonial_check
from wayfarer.engine.rules.magic.protocols import MagicItemInstance, ceremonial_skill_bonus
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
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
from wayfarer.engine.simulation.magic.enchanting_calendar import (
    CALENDAR_DAY as CALENDAR_DAY,
)
from wayfarer.engine.simulation.magic.enchanting_calendar import (
    next_shift_at,
    record_rest,
)
from wayfarer.engine.simulation.magic.item_state import has_item_magic, require_power_installation
from wayfarer.engine.simulation.magic.staff_state import require_staff_construction
from wayfarer.engine.simulation.resources import (
    Advance,
    Item,
    Receipt,
    ResourceEvent,
    ResourceState,
)
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

MAGE_DAY = 8 * 60 * 60


def replay_enchantment_check(check: CheckTrace) -> CheckTrace:
    """Score recorded B481 ceremonial dice while preserving the real skill target."""
    return replace(replay_ceremonial_check(check), rule_id="gurps.magic.enchanting")


class EnchantmentSchedule(Record):
    """Private daily-work facts; the public project schema remains unchanged."""

    first_shift_at: int = Field(ge=0)
    makeup_shifts: int = Field(default=0, ge=0)


def _schedule(resources: ResourceState, work: EnchantmentWork) -> EnchantmentSchedule:
    event = next((e for e in resources.events if e.id == "enchantment-schedule:" + work.id), None)
    return (
        EnchantmentSchedule.model_validate_json(event.kind)
        if event
        else EnchantmentSchedule(first_shift_at=work.start)
    )


def enchanting_work_active(resources: ResourceState, project: EnchantmentProject) -> bool:
    """Whether a mage is concentrating now, excluding nightly rest and completed work."""
    work = project.active_work
    if work is None or project.status != "active" or resources.game_time >= work.due:
        return False
    event = next((e for e in resources.events if e.id == "enchantment-schedule:" + work.id), None)
    if event is None:
        return True
    schedule = EnchantmentSchedule.model_validate_json(event.kind)
    elapsed = resources.game_time - schedule.first_shift_at
    return elapsed >= 0 and elapsed % CALENDAR_DAY < MAGE_DAY


def _end_daily_work(
    resources: ResourceState,
    project: EnchantmentProject,
    recipe: EnchantmentRecipe,
    command_id: str,
) -> ResourceState:
    work = project.active_work
    if recipe.method != "slow-and-sure" or work is None:
        return resources
    return record_rest(
        resources, work, command_id, first_shift_at=_schedule(resources, work).first_shift_at
    )


def unresolved_enchantment(resources: ResourceState, item_id: str) -> bool:
    """An unknown Quick and Dirty perversion needs a director-authored resolution."""
    return any(
        e.id.startswith("enchantment-perversion:") and e.target_id == item_id
        for e in resources.events
    )


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


class AdvanceEnchanting(EnchantmentCommand):
    kind: Literal["advance"] = "advance"
    project_id: Id
    work_id: Id
    to: int = Field(ge=0)


class AbandonEnchantment(EnchantmentCommand):
    kind: Literal["abandon"] = "abandon"
    project_id: Id


TypedEnchantmentCommand = Annotated[
    CreateEnchantment
    | BeginEnchanting
    | InterruptEnchanting
    | SettleEnchanting
    | AdvanceEnchanting
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
    # model_copy is deliberately nonvalidating; authoritative commands recheck recipes.
    EnchantmentRecipe.model_validate(recipe.model_dump())
    if recipe.mana == "none":
        raise ValidationError("Enchanting requires mana")
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
        or (target.ground is not None or target.world_ground_location_id is not None)
        or (target.condition is not None and target.condition.disabled)
    ):
        raise ValidationError("Target item is not suitable for this enchantment recipe")
    if recipe.spell_id == "spell:staff":
        require_staff_construction(state.resources, target_item_id)
    if recipe.spell_id == "spell:power":
        configured_magic = runtime.rules.spells.magic_items if runtime.rules.spells else ()
        if not has_item_magic(state.resources, target_item_id, configured_magic):
            raise ValidationError("Power requires an already enchanted magic item")
        require_power_installation(state.resources, target_item_id, configured_magic)
    if unresolved_enchantment(state.resources, target_item_id):
        raise ValidationError("Unresolved enchanting perversion requires director adjudication")
    if not any(
        i.owner_id == enchanter_ids[0]
        and i.definition_id == recipe.workspace_definition_id
        and (i.ground is None and i.world_ground_location_id is None)
        and (i.condition is None or not i.condition.disabled)
        for i in state.resources.items
    ):
        raise ValidationError("Required enchanting workspace is unavailable")
    minimum = 20 if recipe.mana == "low" else 15
    levels = tuple(_levels(runtime, state, recipe, actor) for actor in enchanter_ids)
    if any(level < minimum for level in levels):
        raise ValidationError(f"Every enchanter requires both spells at {minimum}+")
    power = levels[0]
    effective_power = (
        power
        - (5 if recipe.mana == "low" else 0)
        - (len(enchanter_ids) - 1 if recipe.method == "quick-and-dirty" else 0)
    )
    if effective_power < 15:
        raise ValidationError("Assistant penalty leaves magic-item Power below 15")
    return power


def _consume_materials(
    resources: ResourceState, owner_id: str, materials: tuple[EnchantmentMaterial, ...]
) -> ResourceState:
    available: dict[str, int] = {}
    for item in resources.items:
        if (
            item.owner_id == owner_id
            and (item.ground is None and item.world_ground_location_id is None)
            and not item.equipped
        ):
            available[item.definition_id] = available.get(item.definition_id, 0) + item.quantity
    if any(available.get(m.definition_id, 0) < m.quantity for m in materials):
        raise ValidationError("Enchanting material shortfall")
    remaining = {m.definition_id: m.quantity for m in materials}
    kept: list[Item] = []
    for item in resources.items:
        wanted = remaining.get(item.definition_id, 0)
        if (
            wanted
            and item.owner_id == owner_id
            and (item.ground is None and item.world_ground_location_id is None)
            and not item.equipped
        ):
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
    if sum(c.energy for c in contributions) < recipe.energy_required:
        raise ValidationError("Quick and Dirty contributions must cover required energy")
    pools = {p.id: p for p in resources.pools}
    for contribution in contributions:
        fp, hp = pools.get("fp:" + contribution.actor_id), pools.get("hp:" + contribution.actor_id)
        if fp is None or fp.fatigue is None or hp is None or hp.injury is None:
            raise ValidationError("Enchanter energy pools are unavailable")
        if fp.current < contribution.fp or hp.current - contribution.hp < -hp.maximum:
            raise ValidationError("Enchanter cannot supply promised energy")


def _spend_energy(
    runtime: RulesContext,
    state: PlayState,
    contributions: tuple[EnergyContribution, ...],
    command_id: str,
) -> ResourceState:
    resources = state.resources
    for contribution in contributions:
        compiled = build(runtime, state, contribution.actor_id)
        assert compiled.statistics is not None
        prefix = "enchantment-energy:" + command_id + ":" + contribution.actor_id
        if contribution.hp:
            resources, result = apply_injury(
                resources,
                Wound(
                    id=prefix + ":hp",
                    actor_id=contribution.actor_id,
                    expected_revision=resources.revision,
                    basic_damage=contribution.hp,
                    resistance=0,
                    damage_type="cr",
                ),
                ht=compiled.statistics.ht,
                rng=runtime.rng,
                system=True,
                burning_hp=True,
            )
            if result.injury != contribution.hp:
                raise ConflictError("Enchanter cannot supply promised HP")
        if contribution.fp:
            resources, fatigue = apply_fatigue(
                resources,
                FatigueCost(
                    id=prefix + ":fp",
                    actor_id=contribution.actor_id,
                    expected_revision=resources.revision,
                    amount=contribution.fp,
                    power=True,
                ),
                ht=compiled.statistics.ht,
                rng=runtime.rng,
                system=True,
            )
            if fatigue.fp_lost != contribution.fp or fatigue.hp_lost:
                raise ConflictError("Enchanter cannot supply promised FP")
    return resources


def _begin(
    runtime: RulesContext,
    state: PlayState,
    command: BeginEnchanting,
    project: EnchantmentProject,
    recipe: EnchantmentRecipe,
) -> tuple[PlayState, EnchantmentProject, EnchantmentOutcome]:
    if project.status not in ("active", "interrupted") or project.active_work is not None:
        raise ConflictError("Enchantment project is not available for work")
    if any(
        other.id != project.id
        and other.status in ("active", "interrupted")
        and (other.active_work is not None or other.interruptions)
        and set(other.enchanter_ids) & set(project.enchanter_ids)
        for other in state.resources.enchantment_projects
    ):
        raise ConflictError("An enchanter is committed to another unfinished enchantment")
    for actor_id in project.enchanter_ids:
        synchronous(state, actor_id)
    if busy_actor_ids(state.resources.enchantment_projects) & set(project.enchanter_ids):
        raise ConflictError("An enchanter already has active project work")
    _validate_bindings(runtime, state, recipe, project.target_item_id, project.enchanter_ids)
    _energy_preflight(state.resources, recipe, project, command.contributions)
    remaining = recipe.energy_required - project.energy_completed
    if recipe.method == "quick-and-dirty":
        duration = ((recipe.energy_required + 99) // 100) * 3600
    else:
        first_shift = next_shift_at(state.resources, project.enchanter_ids)
        makeup = 0
        if project.interruptions:
            paused = next(
                (
                    e
                    for e in state.resources.events
                    if e.id == "enchantment-pause:" + project.interruptions[-1].command_id
                ),
                None,
            )
            if paused:
                previous = EnchantmentSchedule.model_validate_json(paused.kind)
                makeup = previous.makeup_shifts + max(
                    0, (first_shift - previous.first_shift_at) // CALENDAR_DAY
                )
                first_shift = max(first_shift, previous.first_shift_at)
            else:
                # Old checkpoints stored makeup in eight-hour workdays.
                makeup = (project.delay_seconds + MAGE_DAY - 1) // MAGE_DAY
        schedule = EnchantmentSchedule(first_shift_at=first_shift, makeup_shifts=makeup)
        days = (remaining + len(project.enchanter_ids) - 1) // len(project.enchanter_ids) + makeup
        duration = first_shift - state.resources.game_time + (days - 1) * CALENDAR_DAY + MAGE_DAY
        resources = state.resources.model_copy(
            update={
                "events": state.resources.events
                + (
                    ResourceEvent(
                        id="enchantment-schedule:" + command.id,
                        at=state.resources.game_time,
                        target_id=project.id,
                        kind=schedule.model_dump_json(),
                    ),
                )
            }
        )
        state = state.model_copy(update={"resources": resources})
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
    runtime: RulesContext,
    state: PlayState,
    command: InterruptEnchanting,
    project: EnchantmentProject,
    recipe: EnchantmentRecipe,
) -> tuple[PlayState, EnchantmentProject, EnchantmentOutcome]:
    work = project.active_work
    if project.status != "active" or work is None:
        raise ConflictError("Enchantment project has no active work to interrupt")
    if state.resources.game_time >= work.due:
        raise ConflictError("Completed enchanting work must be settled, not interrupted")
    credited = 0
    delay = 0
    if recipe.method == "slow-and-sure":
        schedule = _schedule(state.resources, work)
        elapsed = state.resources.game_time - schedule.first_shift_at
        full_days = (
            elapsed // CALENDAR_DAY + int(elapsed % CALENDAR_DAY >= MAGE_DAY) if elapsed >= 0 else 0
        )
        active = enchanting_work_active(state.resources, project)
        credited = min(
            recipe.energy_required - project.energy_completed,
            max(0, full_days - schedule.makeup_shifts) * len(project.enchanter_ids),
        )
        makeup = max(0, schedule.makeup_shifts - full_days) + int(active)
        next_shift = (
            schedule.first_shift_at + (elapsed // CALENDAR_DAY + 1) * CALENDAR_DAY
            if elapsed >= 0
            else schedule.first_shift_at
        )
        delay = makeup * MAGE_DAY
        resources = state.resources
        if active:
            # B481 describes missing 1d FP, not another 1d lost on top of prior fatigue.
            contributions = []
            for actor_id in project.enchanter_ids:
                missing = draw_dice(runtime.rng, 1)[0]
                fp = next(p for p in resources.pools if p.id == "fp:" + actor_id)
                contributions.append(
                    EnergyContribution(
                        actor_id=actor_id, fp=max(0, fp.current - fp.maximum + missing)
                    )
                )
            resources = _spend_energy(runtime, state, tuple(contributions), command.id)
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id="enchantment-pause:" + command.id,
                        at=resources.game_time,
                        target_id=project.id,
                        kind=EnchantmentSchedule(
                            first_shift_at=next_shift, makeup_shifts=makeup
                        ).model_dump_json(),
                    ),
                )
            }
        )
        state = state.model_copy(update={"resources": resources})
    interruption = EnchantmentInterruption(
        command_id=command.id,
        at=state.resources.game_time,
        credited_energy=credited,
        lost_seconds=delay,
    )
    state = state.model_copy(
        update={"resources": _end_daily_work(state.resources, project, recipe, command.id)}
    )
    project = project.model_copy(
        update={
            "status": "interrupted",
            "energy_completed": project.energy_completed + credited,
            "delay_seconds": delay,
            "active_work": None,
            "interruptions": project.interruptions + (interruption,),
        }
    )
    return (
        state,
        project,
        EnchantmentOutcome(
            command_id=command.id,
            project_id=project.id,
            status="interrupted",
            energy_completed=project.energy_completed,
        ),
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
    modifiers: tuple[Modifier, ...] = (
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
    if recipe.mana == "low":
        modifiers += (
            Modifier(-5, "Low mana", "campaigns:b481:enchanting", "gurps-basic-set-4e-2004"),
        )
    lead_hp = next((c.hp for c in work.contributions if c.actor_id == project.enchanter_ids[0]), 0)
    if lead_hp:
        modifiers += (
            Modifier(
                -lead_hp,
                "Lead enchanter burns HP",
                "characters:b237:burning-hp",
                "gurps-basic-set-4e-2004",
            ),
        )
    bonus = (
        ceremonial_skill_bonus(recipe.energy_required, sum(c.energy for c in work.contributions))
        if recipe.method == "quick-and-dirty"
        else 0
    )
    if bonus:
        modifiers += (
            Modifier(
                bonus,
                "Extra ceremonial energy",
                "campaigns:b481:enchanting",
                "gurps-basic-set-4e-2004",
            ),
        )
    check = replay_enchantment_check(
        success_roll("gurps-basic-set-4e-2004", power, modifiers, rng=runtime.rng)
    )
    critical_bonus = draw_dice(runtime.rng, 2) if check.outcome is Outcome.CRITICAL_SUCCESS else ()
    resources = (
        _spend_energy(runtime, state, work.contributions, command.id)
        if recipe.method == "quick-and-dirty"
        else state.resources
    )
    resources = _end_daily_work(resources, project, recipe, command.id)
    completed = recipe.energy_required
    binding_id = None
    status = "failed"
    if check.outcome.succeeded:
        target = next(i for i in resources.items if i.id == project.target_item_id)
        binding_id = "magic-item:" + project.id
        # B481 explicitly makes extra ceremonial energy affect permanent Power;
        # low mana is expressly temporary. Whether the B237 lead-HP roll penalty
        # also lowers permanent Power remains a source-interpretation boundary.
        item_power = (
            power
            - (len(project.enchanter_ids) - 1 if recipe.method == "quick-and-dirty" else 0)
            + bonus
            + sum(critical_bonus)
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
        if critical_bonus:
            resources = resources.model_copy(
                update={
                    "events": resources.events
                    + (
                        ResourceEvent(
                            id="enchantment-power:" + command.id,
                            at=resources.game_time,
                            target_id=project.id,
                            kind=",".join(str(d) for d in critical_bonus),
                        ),
                    )
                }
            )
    elif check.outcome is Outcome.CRITICAL_FAILURE or (
        recipe.method == "slow-and-sure"
        and not next(i for i in resources.items if i.id == project.target_item_id).enchantments
    ):
        destroyed = next(i for i in resources.items if i.id == project.target_item_id)
        resources = resources.model_copy(
            update={
                "items": tuple(i for i in resources.items if i.id != destroyed.id),
                "expended_items": resources.expended_items + (destroyed,),
            }
        )
        status = "critical-failure" if check.outcome is Outcome.CRITICAL_FAILURE else "failed"
    elif recipe.method == "quick-and-dirty":
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id="enchantment-perversion:" + command.id,
                        at=resources.game_time,
                        target_id=project.target_item_id,
                        kind=project.id,
                    ),
                )
            }
        )
        status = "perverted"
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


def _advance_work(
    runtime: RulesContext,
    state: PlayState,
    command: AdvanceEnchanting,
    project: EnchantmentProject,
) -> tuple[PlayState, EnchantmentOutcome]:
    work = project.active_work
    if project.status != "active" or work is None or work.id != command.work_id:
        raise ConflictError("Enchanting work receipt is not active")
    if not state.resources.game_time < command.to <= work.due:
        raise ValidationError("Enchanting clock must advance within the active work deadline")
    if state.party.groups:
        raise ValidationError("Grouped campaigns must use their shared party timeline")
    if any(encounter.status == "active" for encounter in state.encounters):
        raise ValidationError("Active combat must settle before advancing enchanting work")
    state = runtime.advance(
        state,
        Advance(
            id="enchantment-advance:" + command.id,
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            to=command.to,
        ),
    )
    return state, EnchantmentOutcome(
        command_id=command.id,
        project_id=project.id,
        status="advanced",
        energy_completed=project.energy_completed,
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
            state, project, outcome = _interrupt(runtime, state, command, project, recipe)
        elif isinstance(command, AdvanceEnchanting):
            state, outcome = _advance_work(runtime, state, command, project)
        elif isinstance(command, AbandonEnchantment):
            if project.status not in ("active", "interrupted"):
                raise ConflictError("Only an unfinished enchantment can be abandoned")
            state = state.model_copy(
                update={"resources": _end_daily_work(state.resources, project, recipe, command.id)}
            )
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
