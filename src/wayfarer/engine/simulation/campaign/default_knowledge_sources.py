"""Current societal knowledge from actual generic and specialized GM receipts."""

import hashlib

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.default_knowledge import identifier, observations
from wayfarer.engine.simulation.equipment.repair_defaults import (
    TARGETS,
    TRAINING_PREFIX,
    ArmouryTraining,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError


def _armoury_observations(
    resources: ResourceState, actor_id: str
) -> tuple[tuple[ResourceEvent, ArmouryTraining], ...]:
    result: list[tuple[ResourceEvent, ArmouryTraining]] = []
    identifiers: set[str] = set()
    for event in resources.events:
        if not event.id.startswith(TRAINING_PREFIX):
            continue
        facts = ArmouryTraining.model_validate_json(event.kind)
        if facts.performer_id != actor_id:
            continue
        key = TRAINING_PREFIX + hashlib.sha256(facts.command_id.encode()).hexdigest()
        if (
            event.id in identifiers
            or event.id != key
            or event.target_id != actor_id
            or event.at > resources.game_time
            or len(set(facts.society_known_skills)) != len(facts.society_known_skills)
            or not set(facts.society_known_skills) <= TARGETS
        ):
            raise ValidationError("Armoury knowledge receipt does not match its source identity")
        identifiers.add(event.id)
        result.append((event, facts))
    return tuple(result)


def _armoury_source(
    state: PlayState, actor_id: str
) -> tuple[ResourceEvent, ArmouryTraining] | None:
    observations = _armoury_observations(state.resources, actor_id)
    if not observations:
        return None
    event, facts = observations[-1]
    actor = next((actor for actor in state.actors if actor.actor_id == actor_id), None)
    if (
        actor is None
        or actor.approval is None
        or actor.approval.build_revision != facts.build_revision
    ):
        return None
    return event, facts


def current_skills(state: PlayState, actor_id: str) -> frozenset[str]:
    """Unknown is no assertion; the last actual GM assertion decides each skill.

    Specialized proof remains tied to its current approved build. Reading this
    ledger neither adopts an actor nor writes substitute generic observations.
    """
    generic = {identifier(value.command_id): value for value in observations(state.resources)}
    specialized = _armoury_source(state, actor_id)
    decisions: dict[str, bool] = {}
    for event in state.resources.events:
        if specialized is not None and event == specialized[0]:
            decisions.update(dict.fromkeys(specialized[1].society_known_skills, True))
        value = generic.get(event.id)
        if (
            value is not None
            and value.performer_id == actor_id
            and value.configuration_digest == state.configuration_digest
        ):
            decisions.update(dict.fromkeys(value.skill_ids, value.society_known))
    return frozenset(skill for skill, known in decisions.items() if known)
