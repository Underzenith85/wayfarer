"""Campaign-authored media context for Propaganda/TL (Basic Set B216)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record


class PropagandaMedium(Record):
    """One medium whose availability and effects are pinned by campaign policy.

    B216 does not prescribe a universal technology table. Campaign authors must
    therefore state the medium's reach and timing instead of the engine inventing
    either value from a TL number.
    """

    id: Id
    capability_id: Id
    minimum_technology_level: int = Field(ge=0, le=12)
    maximum_technology_level: int = Field(ge=0, le=12)
    audience_limit: int = Field(ge=2, le=1_000_000_000)
    attempt_seconds: int = Field(ge=1)
    persistence_seconds: int = Field(default=0, ge=0)
    required_definition_ids: tuple[Id, ...] = ()
    required_item_definition_ids: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def valid_range_and_prerequisites(self) -> PropagandaMedium:
        if self.maximum_technology_level < self.minimum_technology_level:
            raise ValueError("Propaganda medium has an inverted technology range")
        if len(set(self.required_definition_ids)) != len(self.required_definition_ids):
            raise ValueError("Duplicate Propaganda definition prerequisite")
        if len(set(self.required_item_definition_ids)) != len(self.required_item_definition_ids):
            raise ValueError("Duplicate Propaganda equipment prerequisite")
        return self


class PropagandaRules(Record):
    """Pinned media policy; configured media are not automatically executable."""

    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    media: tuple[PropagandaMedium, ...] = Field(min_length=1)
    executable_capability_ids: tuple[Id, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_media_and_capabilities(self) -> PropagandaRules:
        if len({medium.id for medium in self.media}) != len(self.media):
            raise ValueError("Duplicate Propaganda medium")
        if len(set(self.executable_capability_ids)) != len(self.executable_capability_ids):
            raise ValueError("Duplicate executable Propaganda capability")
        return self


class PropagandaMediaContext(Record):
    """The server-derived media facts recorded with one social receipt."""

    medium_id: Id
    technology_level: int = Field(ge=0, le=12)
    audience_limit: int = Field(ge=2)
    attempt_seconds: int = Field(ge=1)
    persistence_seconds: int = Field(ge=0)
    ends_at: int | None = Field(default=None, ge=0)
    scene_bound: bool


def bind_media(
    rules: PropagandaRules | None,
    *,
    profile_id: str,
    campaign_technology_level: int | None,
    medium_id: str | None,
    actor_id: str,
    build: ValidatedBuild,
    resources: ResourceState,
) -> PropagandaMediaContext:
    """Resolve one requested medium entirely from pinned, server-owned state."""

    if rules is None:
        raise ValidationError("Propaganda requires an authored campaign media policy")
    if rules.profile_id != profile_id:
        raise ValidationError("Propaganda media policy does not match the campaign profile")
    if campaign_technology_level is None:
        raise ValidationError("Propaganda requires an explicit campaign technology level")
    if medium_id is None:
        raise ValidationError("Propaganda requires an authored medium")
    medium = next((value for value in rules.media if value.id == medium_id), None)
    if medium is None:
        raise ValidationError(f"Unknown Propaganda medium: {medium_id}")
    if medium.capability_id not in rules.executable_capability_ids:
        raise ValidationError(f"Unsupported Propaganda media capability: {medium.capability_id}")
    if not (
        medium.minimum_technology_level
        <= campaign_technology_level
        <= medium.maximum_technology_level
    ):
        raise ValidationError("Propaganda medium is unavailable at the campaign technology level")
    purchased = {value.definition_id for value in build.purchases}
    missing_definitions = sorted(set(medium.required_definition_ids) - purchased)
    if missing_definitions:
        raise ValidationError(
            "Propaganda medium requires approved definitions: " + ", ".join(missing_definitions)
        )
    owned_items = {
        value.definition_id
        for value in resources.items
        if value.owner_id == actor_id and value.quantity > 0
    }
    missing_items = sorted(set(medium.required_item_definition_ids) - owned_items)
    if missing_items:
        raise ValidationError(
            "Propaganda medium requires available equipment: " + ", ".join(missing_items)
        )
    persistence = medium.persistence_seconds
    return PropagandaMediaContext(
        medium_id=medium.id,
        technology_level=campaign_technology_level,
        audience_limit=medium.audience_limit,
        attempt_seconds=medium.attempt_seconds,
        persistence_seconds=persistence,
        ends_at=resources.game_time + medium.attempt_seconds + persistence if persistence else None,
        scene_bound=persistence == 0,
    )
