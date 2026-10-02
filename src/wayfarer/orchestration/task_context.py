"""Approved current actors and authored B346 work, without caller numeric targets."""

from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.physical import physical_traits
from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.rules.catalog import DefinitionKind
from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.rules.effects import EffectEvaluator, MechanicalTarget
from wayfarer.engine.rules.skills.cinematic import BINDINGS as CINEMATIC_SKILLS
from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.simulation.actions import CheckRule, PlayState
from wayfarer.engine.simulation.actors import fatigue_ready
from wayfarer.engine.simulation.campaign.activities import ActivityActor
from wayfarer.engine.simulation.campaign.encounter_context import activity_for
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    definition_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.fright_state import blocked, requires_adjudication
from wayfarer.engine.simulation.magic.effects import dazed
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import (
    BINDING_PREFIX,
    BindLongTask,
    LongTaskBinding,
    append_record,
)

PROFILE = "gurps-basic-set-4e-2004"


def approved(play: PlayService, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next((a for a in state.actors if a.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Task requires a current approved actor")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
    )
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        raise ValidationError("Task Luck requires the exact Basic Set profile")
    return build


def require_noncinematic(play: PlayService, identifier: str) -> None:
    definition = play.engine.reviewer.compiler.definitions.get(identifier)
    if definition is None or definition.kind not in (
        DefinitionKind.SKILL,
        DefinitionKind.ATTRIBUTE,
    ):
        raise ValidationError("Task requires an authored ordinary attribute or skill")
    if identifier in {skill.id for skill in CINEMATIC_SKILLS}:
        raise ValidationError("Cinematic skill consequences use their dedicated consumer")


def ready(play: PlayService, state: PlayState, actor_id: str) -> None:
    approved(play, state, actor_id)
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    if state.lifecycle != "active" or activity_for(state, actor_id).encounter is not None:
        raise ConflictError("Tasks require active noncombat play")
    synchronous(state, actor_id)
    require_innate_actor_action(state, actor_id)
    require_hazards_settled(
        state.resources.hazards, frozenset({actor_id}), state.resources.game_time
    )
    require_hazard_capacity(state.resources, actor_id, "work")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    if (
        actor.conditions
        or actor.available_at > state.resources.game_time
        or hp.injury is None
        or hp.injury.incapacitated
        or hp.injury.stunned
        or not fatigue_ready(state, actor_id)
        or blocked(state.resources, actor_id, kind="inspect")
        or requires_adjudication(state.resources, actor_id)
        or dazed(state.resources, actor_id)
        or actor_id in state.recovery.dead_actor_ids
        or any(c.actor_id == actor_id and c.released_at is None for c in state.recovery.captivity)
        or any(t.status == "pending" and t.actor_id == actor_id for t in tasks(state.resources))
        or any(
            form.actor_id == actor_id
            and form.status == "active"
            and form.recovery_until is not None
            and form.recovery_until > state.resources.game_time
            for form in state.transformations.records
        )
    ):
        raise ValidationError("Task actor is not ready to work")


def binding(state: PlayState, identifier: str) -> LongTaskBinding:
    selected = next(
        (
            LongTaskBinding.model_validate_json(event.kind)
            for event in reversed(state.resources.events)
            if event.id.startswith(BINDING_PREFIX)
            and LongTaskBinding.model_validate_json(event.kind).rule.id == identifier
        ),
        None,
    )
    if selected is None or selected.campaign_id != state.campaign_id:
        raise ValidationError("Unknown authored long task")
    return selected


def bind(play: PlayService, state: PlayState, command: BindLongTask, principal: str) -> PlayState:
    require_noncinematic(play, command.rule.target_id)
    if not any(
        entity.id == command.location_id and entity.kind is EntityKind.LOCATION
        for entity in state.world.entities
    ):
        raise ValidationError("Task location is not in this world")
    if command.rule.time_spent_modifier == -10:
        raise ValidationError("Instant cinematic haste is outside an ordinary long task")
    if command.required_equipment is not None:
        definition = play.engine.reviewer.compiler.definitions.get(command.required_equipment)
        if definition is None or definition.kind is not DefinitionKind.EQUIPMENT:
            raise ValidationError("Task equipment must name a catalog definition")
    if command.supervisor_skill_id not in (None, "skill:administration", "skill:leadership"):
        raise ValidationError("Supervision requires Administration or Leadership")
    if command.supervisor_skill_id is not None:
        require_noncinematic(play, command.supervisor_skill_id)
    if any(
        event.id.startswith(BINDING_PREFIX)
        and LongTaskBinding.model_validate_json(event.kind).rule.id == command.rule.id
        for event in state.resources.events
    ):
        raise ConflictError("An authored task cannot replace prior progress or requirements")
    selected = LongTaskBinding(
        rule=command.rule,
        location_id=command.location_id,
        required_equipment=command.required_equipment,
        supervisor_skill_id=command.supervisor_skill_id,
        declared_by=principal,
        campaign_id=state.campaign_id,
    )
    return append_record(state, BINDING_PREFIX, command.id, command.rule.id, selected)


def require_work_context(
    state: PlayState, source: LongTaskBinding, actor_id: str, *, equipment: bool = True
) -> None:
    entity = next((entity for entity in state.world.entities if entity.id == actor_id), None)
    if entity is None or entity.location_id != source.location_id:
        raise ValidationError("Task actor must be at the authored work site")
    if (
        equipment
        and source.required_equipment is not None
        and not any(
            item.owner_id == actor_id
            and item.definition_id == source.required_equipment
            and item.ready
            and item.container_id is None
            and (item.condition is None or not item.condition.disabled)
            for item in state.resources.items
        )
    ):
        raise ValidationError("Task requires its current usable equipment")


def activity_actor(
    play: PlayService, state: PlayState, actor_id: str, target: str
) -> ActivityActor:
    ready(play, state, actor_id)
    build = play.rules_context.approved_build(state, actor_id)
    assert build.statistics is not None
    statistics = build.statistics
    values = {value.target: value.value for value in build.sheet.values}
    effects = play.engine.resources.equipment_effects(state.resources, actor_id)
    evaluator = EffectEvaluator(tuple(MechanicalTarget(key) for key in values))

    def attribute(identifier: str) -> int:
        result = evaluator.evaluate(
            identifier,
            values[identifier],
            effects,
            context={"actor_id": actor_id},
            at=state.resources.game_time,
        ).value
        if not result.is_finite() or result != result.to_integral_value():
            raise ValidationError("Task attribute effects require whole values")
        return int(result)

    if target.startswith("attribute:"):
        if target not in values:
            raise ValidationError("Task attribute is not present in the approved build")
        level = (
            EffectEvaluator((MechanicalTarget(target),))
            .evaluate(
                target,
                values[target],
                effects,
                context={"actor_id": actor_id},
                at=state.resources.game_time,
            )
            .value
        )
    else:
        if target not in values:
            raise ValidationError("Task skill is not available in the approved build")
        definition = play.engine.reviewer.compiler.definitions[target]
        rule = CheckRule(
            id="private-task-target",
            action="inspect",
            target_id=actor_id,
            definition_id=target,
            package_id=definition.source_id,
            package_version=PROFILE,
        )
        level = play.engine._target(state, actor_id, build, rule)[0].value
    if not level.is_finite() or level != level.to_integral_value():
        raise ValidationError("Task target must be a finite whole number")
    fp = next(pool for pool in state.resources.pools if pool.id == "fp:" + actor_id)
    return ActivityActor(
        actor_id=actor_id,
        basic_lift=max(1, int(statistics.basic_lift)),
        basic_move=max(1, statistics.basic_move),
        ht=attribute("attribute:ht"),
        will=attribute("secondary:will"),
        current_fp=fp.current,
        maximum_fp=fp.maximum,
        targets=((target, int(level)),),
        physiology=physiology_traits(build, play.engine.reviewer.compiler.definitions),
    )


def task_modifiers(
    play: PlayService, state: PlayState, actor_id: str, target: str
) -> tuple[Modifier, ...]:
    return definition_modifiers(
        state.resources, actor_id, target, play.engine.reviewer.compiler.definitions
    )


def overtime_modifiers(play: PlayService, state: PlayState, actor_id: str) -> tuple[Modifier, ...]:
    result = check_modifiers(state.resources, actor_id, "ht")
    fitness = physical_traits(
        approved(play, state, actor_id), play.engine.reviewer.compiler.definitions
    ).fitness
    if fitness:
        result += (Modifier(fitness, "Fitness HT check", "B55/B160", PROFILE),)
    return result


def supervision_skill(
    source: LongTaskBinding,
) -> Literal["skill:administration", "skill:leadership"] | None:
    if source.supervisor_skill_id is None:
        return None
    if source.supervisor_skill_id == "skill:administration":
        return "skill:administration"
    if source.supervisor_skill_id == "skill:leadership":
        return "skill:leadership"
    raise ValidationError("Invalid persisted supervision skill")
