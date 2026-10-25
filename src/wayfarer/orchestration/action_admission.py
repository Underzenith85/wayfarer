"""B345 admission for voluntary, authored ordinary checks before effects."""

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.action_engine.engine import TaskCheckPreparation
from wayfarer.engine.simulation.actions import ActionResult, Inspect, PlayState, Social, TypedAction
from wayfarer.engine.simulation.campaign.default_knowledge import adopted
from wayfarer.errors import ValidationError
from wayfarer.orchestration.symptom_generations import correct_symptom_attributes

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def require_prepared_minimum(prepared: TaskCheckPreparation) -> None:
    """B345 uses the exact canonical inputs before costs or original dice."""
    if prepared.target + sum(m.value for m in prepared.modifiers) < 3:
        raise ValidationError("Noncombat effective skill must be at least 3")


def _minimum_allowed(
    play: PlayService,
    state: PlayState,
    command: TypedAction,
    *,
    enabled: bool,
    correct_skill_context: bool,
    correct_default_knowledge: bool | None = None,
    require_default_knowledge: bool | None = None,
    ruling_id: str | None = None,
) -> bool:
    knowledge = (
        play.rules_context.correct_default_knowledge
        if correct_default_knowledge is None
        else correct_default_knowledge
    )
    required = (
        play.rules_context.require_default_knowledge
        if require_default_knowledge is None
        else require_default_knowledge
    )
    knowledge = knowledge and (required or adopted(state, command.actor_id))
    if (
        (not enabled and not knowledge)
        or play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004"
        or not isinstance(command, (Inspect, Social))
    ):
        return True
    prepared = play.engine.prepare_task_check(
        state,
        command,
        correct_symptom_attributes=correct_symptom_attributes(),
        correct_skill_context=correct_skill_context,
        correct_default_knowledge=knowledge,
        require_default_knowledge=required,
        ruling_id=ruling_id,
    )
    return not enabled or prepared.target + sum(m.value for m in prepared.modifiers) >= 3


def assess_minimum(
    play: PlayService,
    state: PlayState,
    command: TypedAction,
    assessment: ActionResult,
    *,
    enabled: bool,
    correct_skill_context: bool = False,
    correct_default_knowledge: bool | None = None,
    require_default_knowledge: bool | None = None,
) -> ActionResult:
    if assessment.status != "feasible" or _minimum_allowed(
        play,
        state,
        command,
        enabled=enabled,
        correct_skill_context=correct_skill_context,
        correct_default_knowledge=correct_default_knowledge,
        require_default_knowledge=require_default_knowledge,
    ):
        return assessment
    return assessment.model_copy(update={"status": "rejected", "code": "check.minimum_skill"})


def require_minimum(
    play: PlayService,
    state: PlayState,
    command: TypedAction,
    *,
    enabled: bool,
    correct_skill_context: bool = False,
    correct_default_knowledge: bool | None = None,
    require_default_knowledge: bool | None = None,
    ruling_id: str | None = None,
) -> None:
    if not _minimum_allowed(
        play,
        state,
        command,
        enabled=enabled,
        correct_skill_context=correct_skill_context,
        correct_default_knowledge=correct_default_knowledge,
        require_default_knowledge=require_default_knowledge,
        ruling_id=ruling_id,
    ):
        raise ValidationError("Noncombat effective skill must be at least 3")
