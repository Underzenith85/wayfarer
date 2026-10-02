"""Current-build binding shared by immediate and pending social dispatch."""

from wayfarer.engine.character.traits.social import (
    bind_standing,
    reaction_modifiers,
    skill_conditions,
)
from wayfarer.engine.rules.skills.mundane.social.inventory import Resolution, require_procedure
from wayfarer.engine.rules.traits.mundane.runtime import Check
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.npcs import NPCSocialRules
from wayfarer.engine.simulation.campaign.propaganda import bind_media
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService


def bind_trait_modifiers(
    play: PlayService, state: PlayState, command: SocialCommand, context: SocialContext
) -> None:
    """Add the reaction/influence modifiers the initiator's approved build implies.

    An approved build is the only source: a resolver cannot supply a trait
    modifier, and an unbound or unpurchased trait contributes nothing. An
    unapproved initiator (an NPC without a build) contributes nothing either.
    """

    check: Check = "influence" if command.kind in ("influence", "skill") else "reaction"
    actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
    if actor is None or actor.approval is None:
        context.bind_trait_modifiers(())
        return

    approved = build(play.rules_context, state, command.actor_id)
    definitions = play.engine.reviewer.compiler.definitions
    context.standing = bind_standing(approved, definitions, context.standing, context.modifiers)
    context.bind_trait_modifiers(
        reaction_modifiers(
            approved,
            definitions,
            check,
            context.audience,
        )
    )


def bind_skill_conditions(
    play: PlayService, state: PlayState, command: SocialCommand, context: SocialContext
) -> None:
    """Derive the #345 procedure's build-supplied conditions and reaction modifiers.

    The approved build decides only whether a named condition holds; the procedure
    owns what each one is worth. An initiator without an approved build asserts
    nothing extra, and a resolver still cannot supply a trait modifier itself.
    """

    if context.procedure_id is None:
        raise ValidationError("Social skill dispatch requires a declared procedure")
    procedure = require_procedure(
        context.profile_id, context.procedure_id, context.campaign_specialties
    )
    if procedure.id == "skill:interrogation" and context.callous:
        raise ValidationError("Callous coercion is derived from the approved build")
    if procedure.id != "skill:propaganda" and context.medium_id is not None:
        raise ValidationError("Only Propaganda can select an authored medium")
    actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
    if actor is None or actor.approval is None:
        context.bind_trait_modifiers(())
        return

    approved = build(play.rules_context, state, command.actor_id)
    if procedure.id == "skill:interrogation":
        context.callous = any(
            purchase.definition_id == "trait:disadvantage:callous"
            for purchase in approved.trait_purchases
        )
    if procedure.id == "skill:propaganda":
        npc_rules = play.engine.rules.npcs
        context.media = bind_media(
            npc_rules.propaganda if isinstance(npc_rules, NPCSocialRules) else None,
            profile_id=context.profile_id,
            campaign_technology_level=play.engine.reviewer.compiler.policy.technology_level,
            medium_id=context.medium_id,
            actor_id=command.actor_id,
            build=approved,
            resources=state.resources,
        )
    definitions = play.engine.reviewer.compiler.definitions
    context.conditions = context.conditions | skill_conditions(
        approved,
        definitions,
        procedure.id,
        context.audience,
        context.campaign_specialties,
    )
    if procedure.resolution is not Resolution.INFLUENCE:
        # Reaction modifiers reach influence rolls only (B359); an unopposed
        # procedure must not silently collect them.
        context.bind_trait_modifiers(())
        return
    bind_trait_modifiers(play, state, command, context)
