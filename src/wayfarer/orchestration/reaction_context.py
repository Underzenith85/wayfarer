"""Rebind private authored reaction sources to current campaign authority."""

from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.npcs import NPCSocialStanding
from wayfarer.engine.simulation.health.fright_state import requires_adjudication
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.npcs import _standing
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_records import AuthoredSocialReaction
from wayfarer.orchestration.social_binding import bind_skill_conditions, bind_trait_modifiers


def require_reaction_actor(runtime: RulesContext, state: PlayState, actor_id: str) -> None:
    build = runtime.approved_build(state, actor_id)
    if build.statistics is None or build.statistics.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Reaction Luck requires the exact Basic Set profile")


def require_reaction_boundary(state: PlayState, actor_id: str) -> None:
    """A new target cannot cross an attack or mandatory consequence already pending."""
    if any(
        encounter.status == "active"
        and (encounter.pending_defense is not None or encounter.pending_unarmed is not None)
        for encounter in state.encounters
    ):
        raise ConflictError("Resolve the pending attack before preparing another reaction")
    require_hazards_settled(
        state.resources.hazards,
        frozenset(entity.id for entity in state.world.entities),
        state.resources.game_time,
    )
    if requires_adjudication(state.resources, actor_id, handles_aftermath=True):
        raise ValidationError("Resolve permanent fright losses before further social checks")


def player_actor_ids(state: PlayState) -> tuple[str, ...]:
    return tuple(
        actor for member in state.members if member.role == "player" for actor in member.actor_ids
    )


def social_context(
    play: PlayService,
    state: PlayState,
    actor_id: str,
    command_id: str,
    source: AuthoredSocialReaction,
) -> tuple[SocialCommand, SocialContext]:
    profile_id = play.engine.reviewer.compiler.statistics_profile
    if profile_id is None:
        raise ValidationError("Reaction requires an exact GURPS profile")
    command = SocialCommand(
        id=command_id,
        actor_id=actor_id,
        subject_id=source.subject_id,
        kind=source.mode,
        trigger_id=source.trigger_id,
        expected_revision=state.resources.revision,
    )
    context = SocialContext(
        profile_id,
        0,
        modifiers=source.modifiers,
        required_fact_ids=source.required_fact_ids,
        will=source.npc_will,
        campaign_specialties=play.engine.reviewer.compiler.social_skill_specialties,
    )
    if isinstance(source.standing, NPCSocialStanding):
        context.standing, context.audience = _standing(source.standing)
    else:
        context.standing = source.standing
    if source.audience is not None:
        context.audience = source.audience
    if source.mode != "reaction":
        require_innate_actor_action(state, actor_id)
        compiled = build(play.rules_context, state, actor_id)
        value = next(
            (value for value in compiled.sheet.values if value.target == "skill:diplomacy"), None
        )
        if value is None:
            raise ValidationError("Diplomacy fallback requires an approved Diplomacy skill")
        context.target = context.skill_level = int(value.value)
        context.skill = "diplomacy"
        context.influence_conditions = source.influence_conditions
        if any(actor.actor_id == source.subject_id for actor in state.actors):
            subject = build(play.rules_context, state, source.subject_id)
            assert subject.statistics is not None
            context.will = subject.statistics.will
        if source.mode == "skill":
            context.procedure_id = "skill:diplomacy"
            context.conditions = frozenset(source.conditions)
            bind_skill_conditions(play, state, command, context)
        else:
            bind_trait_modifiers(play, state, command, context)
    else:
        bind_trait_modifiers(play, state, command, context)
    if not source.active_interaction or not source.sapient:
        context.modifiers = tuple(
            modifier for modifier in context.modifiers if modifier.source_id != "trait:charisma"
        )
    return command, context
