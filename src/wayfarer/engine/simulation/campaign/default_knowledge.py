"""B173 GM observations of societal default eligibility; never paid training."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator

from wayfarer.engine.simulation.resources import Command, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState

PREFIX = "skill-default-knowledge:"
PROFILE = "gurps-basic-set-4e-2004"


class DeclareDefaultKnowledge(Command):
    kind: Literal["declare-default-knowledge"] = "declare-default-knowledge"
    performer_id: Id
    skill_ids: tuple[Id, ...] = Field(min_length=1, max_length=1000)
    society_known: bool
    reason: str = Field(min_length=1, max_length=2000)


class AdoptDefaultKnowledge(Command):
    kind: Literal["adopt-default-knowledge"] = "adopt-default-knowledge"
    performer_id: Id
    known_skill_ids: tuple[Id, ...] = Field(default=(), max_length=1000)
    reason: str = Field(min_length=1, max_length=2000)


KnowledgeCommand = Annotated[
    AdoptDefaultKnowledge | DeclareDefaultKnowledge, Field(discriminator="kind")
]
ADAPTER: TypeAdapter[KnowledgeCommand] = TypeAdapter(KnowledgeCommand)


class DefaultKnowledge(Record):
    command_id: Id
    performer_id: Id
    principal_id: Id
    configuration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_ids: tuple[Id, ...] = Field(max_length=1000)
    society_known: bool
    reason: str = Field(min_length=1, max_length=2000)
    adoption: bool = False
    generation: Literal[1] = 1

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported default knowledge generation")
        return value


def identifier(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def observations(resources: ResourceState) -> tuple[DefaultKnowledge, ...]:
    result = []
    identifiers: set[str] = set()
    adopted_actors: set[str] = set()
    baselines: set[tuple[str, str]] = set()
    for event in resources.events:
        if not event.id.startswith(PREFIX):
            continue
        value = DefaultKnowledge.model_validate_json(event.kind)
        if (
            event.id in identifiers
            or event.id != identifier(value.command_id)
            or event.target_id != value.performer_id
            or event.at > resources.game_time
            or len(set(value.skill_ids)) != len(value.skill_ids)
            or (value.adoption and not value.society_known)
            or (not value.adoption and not value.skill_ids)
            or (not value.adoption and value.performer_id not in adopted_actors)
            or (value.adoption and (value.performer_id, value.configuration_digest) in baselines)
        ):
            raise ValidationError(
                "Default knowledge receipt does not match its authoritative identity"
            )
        identifiers.add(event.id)
        if value.adoption:
            adopted_actors.add(value.performer_id)
            baselines.add((value.performer_id, value.configuration_digest))
        result.append(value)
    return tuple(result)


def known_skills(state: PlayState, actor_id: str) -> frozenset[str]:
    """Unknown and explicit false both deny future untrained default attempts."""
    decisions: dict[str, bool] = {}
    for value in observations(state.resources):
        if (
            value.performer_id == actor_id
            and value.configuration_digest == state.configuration_digest
        ):
            decisions.update(dict.fromkeys(value.skill_ids, value.society_known))
    return frozenset(key for key, known in decisions.items() if known)


def adopted(state: PlayState, actor_id: str) -> bool:
    # Adoption is sticky across configuration migration. Old observations cease
    # to authorize new definitions, leaving unknown rather than legacy fallback.
    return any(
        value.adoption and value.performer_id == actor_id for value in observations(state.resources)
    )
