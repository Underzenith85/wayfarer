"""Resolve only source-reviewed default edges against current approved facts."""

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.skills.mundane import candidate_package
from wayfarer.engine.rules.skills.technology_level import technology_level_penalty
from wayfarer.engine.rules.types.skill import ControllingAttribute
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, level
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.firearm_repair_profile import require_profile
from wayfarer.engine.simulation.equipment.repair_defaults import (
    TARGETS,
    ArmouryTraining,
    RepairDefaultSelection,
    selected,
    training,
)
from wayfarer.engine.simulation.equipment.repair_parts import digest
from wayfarer.engine.simulation.equipment.repair_time import Method
from wayfarer.engine.simulation.equipment.repair_time import selected as selected_time
from wayfarer.engine.simulation.resources import Item, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError

ARMOR = frozenset({"body-armor", "vehicular-armor", "battlesuits", "force-shields"})


def source_skill(runtime: RulesContext, identifier: str) -> None:
    skills = runtime.reviewer.compiler.skills
    if skills is None:
        raise ValidationError("Repair default requires a canonical skill compiler")
    actual = skills.specs.get(identifier)
    canonical = next((d.skill for d in candidate_package().definitions if d.id == identifier), None)
    if canonical is None or actual != canonical:
        raise ValidationError("Repair default requires its exact source skill definition")


def _edge(
    runtime: RulesContext,
    compiled: ValidatedBuild,
    facts: ArmouryTraining,
    skill_id: str,
    source_id: str,
) -> tuple[int, int, int]:
    source_skill(runtime, skill_id)
    if any(p.definition_id == skill_id for p in compiled.purchases):
        raise ValidationError("Default selection requires an unpurchased repair specialty")
    if source_id == "attribute:iq":
        return (
            min(20, int(level(compiled, "attribute:iq").value)),
            -5,
            facts.personal_technology_level,
        )
    source_skill(runtime, source_id)
    purchase = next((p for p in compiled.purchases if p.definition_id == source_id), None)
    if purchase is None or purchase.technology_level is None:
        raise ValidationError(
            "Repair skill defaults cannot use an untrained or unverified-TL source"
        )
    if purchase.technology_level > facts.personal_technology_level:
        raise ValidationError("IQ-based default source exceeds verified personal TL")
    skills = runtime.reviewer.compiler.skills
    assert skills is not None
    target = skills.specs[skill_id].specialty
    source = skills.specs[source_id].specialty
    assert target is not None
    if source is None:
        raise ValidationError("Repair default requires a concrete source specialty")
    if source.family == "engineer" and source.name == target.name:
        modifier = -4
    elif source.family == "armoury" and source_id != skill_id:
        if purchase.technology_level > 4 and (source.name in ARMOR) != (target.name in ARMOR):
            raise ValidationError("Armor and weapon Armoury defaults do not cross above TL4")
        modifier = -4
    else:
        raise ValidationError("Repair default source has the wrong specialty")
    return int(level(compiled, source_id).value), modifier, purchase.technology_level


def derive(
    runtime: RulesContext,
    state: PlayState,
    *,
    actor_id: str,
    item: Item,
    entry: EquipmentProfile,
    source_id: str,
    command_id: str,
    start_command_id: str,
    repair_time_method: Method | None = None,
) -> RepairDefaultSelection:
    compiled = build(runtime, state, actor_id)
    facts = training(state.resources, actor_id)
    if facts is None or facts.build_revision != compiled.revision:
        raise ValidationError("Repair default lacks current verified training and society facts")
    profile = entry.durability
    if profile is None or profile.repair_skill_id not in TARGETS or item.condition is None:
        raise ValidationError("Repair default requires a supported profiled Armoury item")
    skill_id = profile.repair_skill_id
    if skill_id not in facts.society_known_skills:
        raise ValidationError("Repair specialty is not known in the verified actor society")
    if not isinstance(entry.technology_level, int):
        raise ValidationError("Repair default requires a concrete current item TL")
    if skill_id == "skill:armoury-small-arms":
        require_profile(entry)
    source_level, modifier, tl = _edge(runtime, compiled, facts, skill_id, source_id)
    # Preserve target-specific catalog effects already applied by canonical compilation.
    iq_base = min(20, int(level(compiled, "attribute:iq").value)) - 5
    target_adjustment = int(level(compiled, skill_id).value) - iq_base
    penalty = technology_level_penalty(tl, entry.technology_level, ControllingAttribute.IQ)
    return RepairDefaultSelection(
        command_id=command_id,
        actor_id=actor_id,
        item_id=item.id,
        start_command_id=start_command_id,
        build_revision=compiled.revision,
        training_command_id=facts.command_id,
        definition_id=item.definition_id,
        equipment_digest=digest(entry),
        condition=item.condition,
        skill_id=skill_id,
        source_id=source_id,
        source_level=source_level,
        default_modifier=modifier,
        skill_level=source_level + modifier + target_adjustment,
        training_tl=tl,
        equipment_tl=entry.technology_level,
        tl_penalty=penalty,
        repair_time_method=repair_time_method,
    )


def current(
    runtime: RulesContext,
    state: PlayState,
    *,
    actor_id: str,
    item: Item,
    entry: EquipmentProfile,
    start_command_id: str,
    required: bool = False,
) -> RepairDefaultSelection | None:
    plan = selected(state.resources, start_command_id)
    if plan is None:
        if required:
            raise ValidationError("Repair context requires its explicit selected default")
        return None
    recalculated = derive(
        runtime,
        state,
        actor_id=actor_id,
        item=item,
        entry=entry,
        source_id=plan.source_id,
        command_id=plan.command_id,
        start_command_id=start_command_id,
        repair_time_method=plan.repair_time_method,
    )
    time_plan = selected_time(state.resources, start_command_id)
    if plan.repair_time_method is not None and (
        time_plan is None or time_plan.method != plan.repair_time_method
    ):
        raise ConflictError("Selected repair default requires its captured work method")
    if plan != recalculated:
        raise ConflictError("Selected repair default no longer matches current authority or facts")
    return plan


def finish_current(
    resources: ResourceState,
    *,
    actor_id: str,
    item: Item,
    entry: EquipmentProfile,
    start_command_id: str,
) -> None:
    """Accepted skill/training stay captured; current physical linkage must still match."""
    plan = selected(resources, start_command_id)
    if plan is not None and (
        plan.actor_id != actor_id
        or plan.item_id != item.id
        or plan.definition_id != item.definition_id
        or plan.condition != item.condition
        or plan.equipment_digest != digest(entry)
    ):
        raise ConflictError("Accepted repair default no longer matches current equipment")
