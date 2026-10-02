"""Source-appropriate approved standing for private campaign reactions.

The authored interaction describes who is being reacted to and what the NPC
can perceive. It does not supply purchased trait bonuses or recognition dice.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from pydantic import model_validator

from wayfarer.engine.character.traits.social import bind_standing, reaction_modifiers
from wayfarer.engine.rules.social.gurps_social import ReactionModifier
from wayfarer.engine.rules.social.social_hooks import Standing
from wayfarer.engine.rules.traits.mundane.runtime import Audience
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.campaign.procedures import CampaignProcedureEngine
    from wayfarer.engine.simulation.campaign.reactions import CampaignReactionSource


class CampaignReactionInteraction(Record):
    """Trusted GM context about the actual reaction recipient, not its proxy.

    Proxy means an absent recipient represented by that named actor. A present
    but nonparticipating recipient uses passive mode, retaining perceived looks.
    """

    actor_id: Id
    mode: Literal["active", "passive", "absent", "proxy"]
    audience: Audience = Audience()
    standing: Standing | None = None
    sapient: bool = True
    proxy_actor_id: Id | None = None

    @model_validator(mode="after")
    def valid_proxy(self) -> CampaignReactionInteraction:
        if (self.proxy_actor_id is not None) != (self.mode == "proxy"):
            raise ValueError("Only a proxy interaction requires a named proxy actor")
        if self.proxy_actor_id == self.actor_id:
            raise ValueError("A proxy must differ from the actual reaction recipient")
        return self


def campaign_social_command(
    state: PlayState, source: CampaignReactionSource, npc_id: str
) -> SocialCommand:
    identity = hashlib.sha256((state.campaign_id + "\0" + source.command.id).encode()).hexdigest()
    return SocialCommand(
        id="campaign-social:" + identity,
        actor_id=source.command.actor_id,
        expected_revision=state.resources.revision,
        kind="reaction",
        subject_id=npc_id,
        trigger_id="campaign-reaction:" + identity,
    )


def campaign_social_context(
    engine: CampaignProcedureEngine,
    state: PlayState,
    source: CampaignReactionSource,
    profile_id: str,
    modifiers: tuple[ReactionModifier, ...],
) -> SocialContext:
    interaction = source.interaction
    if interaction.actor_id != source.command.actor_id:
        raise ValidationError("Campaign interaction must name its actual reaction recipient")
    if interaction.proxy_actor_id is not None:
        proxy = next(
            (item for item in state.world.entities if item.id == interaction.proxy_actor_id), None
        )
        if proxy is None or proxy.kind is not EntityKind.ACTOR:
            raise ValidationError("Campaign proxy must be a current world actor")
    audience = interaction.audience
    if interaction.mode in ("absent", "proxy"):
        # B27 recognition may follow a name, but an absent recipient cannot lend
        # their looks, audible Voice or personally perceived Charisma to a proxy.
        audience = replace(audience, perceptible=False, visible=False, audible=False)
    actor = next((item for item in state.actors if item.actor_id == interaction.actor_id), None)
    if actor is None:
        raise ValidationError("Campaign reaction requires its receiving actor's approved build")
    approved, _ = engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor.actor_id
    )
    definitions = engine.reviewer.compiler.definitions
    standing = bind_standing(approved, definitions, interaction.standing, modifiers)
    context = SocialContext(
        profile_id, 0, modifiers=modifiers, standing=standing, audience=audience
    )
    traits = reaction_modifiers(approved, definitions, "reaction", audience)
    if interaction.mode != "active" or not interaction.sapient:
        # B41 specifically requires active interaction with a sapient observer;
        # B21 appearance can still apply during passive visible observation.
        traits = tuple(item for item in traits if item.source_id != "trait:charisma")
    context.bind_trait_modifiers(traits)
    return context
