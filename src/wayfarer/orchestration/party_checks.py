"""Pure preflight of all due B345 checks before any queued action dice."""

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import ACTION_ADAPTER, ActionCommand, PlayState
from wayfarer.engine.simulation.campaign.default_knowledge_producers import (
    enabled as knowledge_enabled,
)
from wayfarer.engine.simulation.campaign.default_knowledge_producers import (
    required as knowledge_required,
)
from wayfarer.engine.simulation.campaign.minimum_skill_state import enabled as minimum_enabled
from wayfarer.engine.simulation.campaign.party import QueuedActivity
from wayfarer.engine.simulation.campaign.skill_context_state import enabled as context_enabled
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.action_admission import require_minimum
from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def require_activity_minimum(
    play: PlayService,
    state: PlayState,
    command: ActionCommand,
    family: str,
    *,
    enabled: bool,
    correct_skill_context: bool,
    correct_default_knowledge: bool | None = None,
    require_default_knowledge: bool | None = None,
) -> None:
    if not enabled and not correct_default_knowledge:
        return
    if family == "noncombat":
        check = NoncombatService(play).prepare_approach_check(
            state,
            NoncombatCommand.model_validate(command),
            correct_skill_context=correct_skill_context,
        )
        if check.target + sum(m.value for m in check.modifiers) < 3:
            raise ValidationError("Noncombat effective skill must be at least 3")
    elif family == "action":
        require_minimum(
            play,
            state,
            ACTION_ADAPTER.validate_python(command),
            enabled=enabled,
            correct_skill_context=correct_skill_context,
            correct_default_knowledge=correct_default_knowledge,
            require_default_knowledge=require_default_knowledge,
        )


def preflight_due(
    play: PlayService, state: PlayState, activities: list[QueuedActivity]
) -> frozenset[str]:
    """Return rejected IDs, preserving other independently authorized work.

    This runs after authoritative clock consequences, before the first action
    roll. Resolution repeats the gate in case an earlier action changes state.
    """
    rejected: set[str] = set()
    for activity in activities:
        if not minimum_enabled(state.resources, "queued-activity", activity.id, activity.actor_id):
            continue
        try:
            model = NoncombatCommand if activity.family == "noncombat" else None
            if activity.family not in ("action", "noncombat"):
                continue
            command = (
                model.model_validate_json(activity.command_json)
                if model
                else ACTION_ADAPTER.validate_json(activity.command_json)
            ).model_copy(update={"expected_revision": state.revision})
            require_activity_minimum(
                play,
                state,
                command,
                activity.family,
                enabled=True,
                correct_skill_context=context_enabled(
                    state.resources, "queued-activity", activity.id, activity.actor_id
                ),
                correct_default_knowledge=knowledge_enabled(
                    state.resources, "queued-activity", activity.id, activity.actor_id
                ),
                require_default_knowledge=knowledge_required(
                    state.resources, "queued-activity", activity.id, activity.actor_id
                ),
            )
        except ValidationError, ConflictError:
            rejected.add(activity.id)
    return frozenset(rejected)
