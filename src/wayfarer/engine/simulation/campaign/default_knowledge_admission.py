"""Validate and persist one actual GM B173 declaration without clock or resource costs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.rules.catalog import DefinitionKind
from wayfarer.engine.simulation.campaign.default_knowledge import (
    PROFILE,
    AdoptDefaultKnowledge,
    DeclareDefaultKnowledge,
    DefaultKnowledge,
    KnowledgeCommand,
    adopted,
    identifier,
    observations,
)
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.rules_context import RulesContext


def declare(
    runtime: RulesContext, state: PlayState, command: KnowledgeCommand
) -> tuple[PlayState, DefaultKnowledge]:
    if command.actor_id not in runtime.reviewer.gm_ids or not any(
        member.principal_id == command.actor_id and member.role == "gm" for member in state.members
    ):
        raise ValidationError("Default knowledge requires the trusted seated GM")
    compiler = runtime.reviewer.compiler
    if compiler.statistics_profile != PROFILE:
        raise ValidationError("Default knowledge requires the exact Basic Set profile")
    if command.expected_revision != state.revision:
        raise ConflictError("Default knowledge revision changed")
    actor = next((a for a in state.actors if a.actor_id == command.performer_id), None)
    if actor is None:
        raise ValidationError("Default knowledge requires a current approved actor")
    runtime.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor.actor_id
    )
    adoption = isinstance(command, AdoptDefaultKnowledge)
    skill_ids = (
        command.known_skill_ids if isinstance(command, AdoptDefaultKnowledge) else command.skill_ids
    )
    if adoption and any(
        value.adoption
        and value.performer_id == command.performer_id
        and value.configuration_digest == state.configuration_digest
        for value in observations(state.resources)
    ):
        raise ConflictError(
            "Default knowledge policy already has this actor/configuration baseline"
        )
    if not adoption and not adopted(state, command.performer_id):
        raise ValidationError("Default knowledge changes require explicit actor-policy adoption")
    if len(set(skill_ids)) != len(skill_ids):
        raise ValidationError("Default knowledge skill identities must be unique")
    for skill_id in skill_ids:
        definition = compiler.definitions.get(skill_id)
        if (
            definition is None
            or definition.kind is not DefinitionKind.SKILL
            or definition.skill is None
        ):
            raise ValidationError("Default knowledge requires concrete pinned skill identities")
    if any(value.command_id == command.id for value in observations(state.resources)):
        raise ConflictError("Default knowledge command already exists")
    value = DefaultKnowledge(
        command_id=command.id,
        performer_id=command.performer_id,
        principal_id=command.actor_id,
        configuration_digest=state.configuration_digest,
        skill_ids=skill_ids,
        society_known=command.society_known
        if isinstance(command, DeclareDefaultKnowledge)
        else True,
        adoption=adoption,
        reason=command.reason,
    )
    event = ResourceEvent(
        id=identifier(command.id),
        at=state.resources.game_time,
        target_id=command.performer_id,
        kind=value.model_dump_json(),
    )
    revision = state.revision + 1
    return state.model_copy(
        update={
            "revision": revision,
            "resources": state.resources.model_copy(
                update={
                    "revision": revision,
                    "events": state.resources.events + (event,),
                }
            ),
        }
    ), value
