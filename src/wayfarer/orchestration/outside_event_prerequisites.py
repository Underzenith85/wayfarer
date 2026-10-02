"""Persist settled personal resistance separately from cancellable secret damage."""

from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.hazard_damage import (
    PREREQUISITE_PREFIX,
    PreparedHazardDamage,
    prerequisite_key,
    saved_prerequisite,
)
from wayfarer.engine.simulation.health.hazards import roll_hazard_prerequisite
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.outside_event_records import OutsidePrerequisite, PrepareOutsideEvent
from wayfarer.orchestration.task_records import append_record

PREFIX = PREREQUISITE_PREFIX


def prior_prerequisite(
    state: PlayState, preparation: PreparedHazardDamage
) -> OutsidePrerequisite | None:
    return saved_prerequisite(state.resources, preparation.schedule)


def settle_prerequisite(
    runtime: RulesContext,
    state: PlayState,
    command: PrepareOutsideEvent,
    preparation: PreparedHazardDamage,
) -> tuple[PlayState, PreparedHazardDamage]:
    if not preparation.schedule.spec.resistible:
        return state, preparation
    if command.exposure_command is None:
        raise ValidationError("Natural prerequisite requires its captured source admission")
    previous = prior_prerequisite(state, preparation)
    if previous is not None:
        if previous.secret != command.secret:
            raise ConflictError("A settled personal prerequisite cannot change its disclosure")
        if (
            previous.preparation.schedule != preparation.schedule
            or previous.source != command.source
            or previous.exposure_command != command.exposure_command
        ):
            raise ConflictError("Settled outside-event prerequisite belongs to a different source")
        return state, preparation.model_copy(update={"resistance": previous.preparation.resistance})
    prepared = roll_hazard_prerequisite(
        state.resources,
        preparation,
        rng=runtime.rng,
        physiology=physiology_traits(
            runtime.approved_build(state, command.actor_id), runtime.reviewer.compiler.definitions
        ),
    )
    state = append_record(
        state,
        PREFIX,
        prerequisite_key(prepared.schedule),
        command.actor_id,
        OutsidePrerequisite(
            preparation=prepared,
            source=command.source,
            exposure_command=command.exposure_command,
            secret=command.secret,
        ),
    )
    return state, prepared


def require_settled_prerequisite(state: PlayState, preparation: PreparedHazardDamage) -> None:
    if not preparation.schedule.spec.resistible:
        return
    saved = prior_prerequisite(state, preparation)
    if saved is None or saved.preparation.resistance != preparation.resistance:
        raise ConflictError("Outside damage lost its settled personal resistance prerequisite")
