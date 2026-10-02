"""Private reconstructible source definitions for unrolled B66 reactions."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.social.gurps_social import InfluenceConditions, ReactionModifier
from wayfarer.engine.rules.social.social_hooks import Standing
from wayfarer.engine.rules.traits.mundane.runtime import Audience
from wayfarer.engine.simulation.campaign.npcs import NPCSocialStanding
from wayfarer.engine.simulation.campaign.reactions import CampaignReactionSource
from wayfarer.engine.simulation.resources import Command
from wayfarer.engine.simulation.social.social import SocialCommand, SocialDisclosure
from wayfarer.models import Id, Record


class AuthoredSocialReaction(Record):
    """Trusted authored circumstances; trait values are always rebound from builds."""

    kind: Literal["social"] = "social"
    trigger_id: Id
    subject_id: Id
    active_interaction: bool
    sapient: bool
    mode: Literal["reaction", "influence", "skill"] = "reaction"
    modifiers: tuple[ReactionModifier, ...] = ()
    standing: NPCSocialStanding | Standing | None = None
    audience: Audience | None = None
    required_fact_ids: tuple[Id, ...] = ()
    disclosure: SocialDisclosure = SocialDisclosure()
    npc_will: int = Field(default=10, ge=1, le=100)
    influence_conditions: InfluenceConditions = InfluenceConditions()
    conditions: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def authored_only(self) -> AuthoredSocialReaction:
        if any(modifier.kind == "trait" for modifier in self.modifiers):
            raise ValueError("Reaction trait modifiers must come from approved builds")
        if self.mode != "skill" and self.conditions:
            raise ValueError("Reaction conditions belong to a skill procedure")
        return self


class NPCReactionSource(Record):
    """Select the current bounded authored occurrence, never caller supplied outcomes."""

    kind: Literal["npc-occurrence"] = "npc-occurrence"
    plan_id: Id
    action_id: Id
    active_interaction: bool
    sapient: bool


ReactionDefinition = Annotated[
    AuthoredSocialReaction | NPCReactionSource | CampaignReactionSource,
    Field(discriminator="kind"),
]


class PrepareReaction(Command):
    kind: Literal["prepare-reaction"] = "prepare-reaction"
    source: ReactionDefinition
    recognition_sources: tuple[SocialCommand, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


class ChooseReaction(Command):
    kind: Literal["choose-reaction"] = "choose-reaction"
    pending_id: Id
    choice: Literal["use-luck", "resolve", "cancel"]


class AttributeReactionRecognition(Command):
    kind: Literal["attribute-reaction-recognition"] = "attribute-reaction-recognition"
    event_id: Id
    reason: str = Field(min_length=1, max_length=1000)


class SecretReactionPending(Record):
    kind: Literal["reaction-unrolled"] = "reaction-unrolled"
    role: Literal["reaction", "diplomacy", "campaign"] = "reaction"
    id: Id
    actor_id: Id
    prepared_elapsed_microseconds: int = Field(ge=0)
    source: ReactionDefinition
    preparation_json: str
    # The full current authored occurrence and progress are part of admission.
    occurrence_json: str | None = None
