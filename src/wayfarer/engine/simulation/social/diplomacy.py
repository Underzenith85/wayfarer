"""B359 staged Diplomacy fallback for both canonical social entry routes.

Recognition and the preceding Influence contest are prerequisites of the still
unrolled reaction. The trusted host persists them once, then selects only the
fallback. Terminal validation and consequences draw no dice and apply no
prerequisite costs a second time. B187/B359's better reaction decides the NPC
response; the private skill verdict continues to describe the actual contest.
"""

from __future__ import annotations

from dataclasses import asdict, replace

from pydantic import field_serializer

from wayfarer.engine.rules.checks import Modifier, RandomSource
from wayfarer.engine.rules.skills.mundane.social.attempts import (
    SocialSkillContext,
    SocialSkillTrace,
    prepare_diplomacy_skill,
    resolve_diplomacy_skill,
    validate_diplomacy_skill_context,
    validate_prepared_diplomacy_skill,
)
from wayfarer.engine.rules.social.gurps_social import (
    InfluenceConditions,
    InfluenceTrace,
    ReactionTrace,
    prepare_influence,
    resolve_diplomacy,
    validate_influence,
    validate_prepared_influence,
)
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social.reactions import (
    PreparedReaction,
    ResolvedReactionContext,
    _cancel_prepared_social,
    _prepare_reaction_source,
    _validate_recognized_reaction,
    recognize_reaction,
)
from wayfarer.engine.simulation.social.social import (
    SocialCommand,
    SocialContext,
    SocialDisclosure,
    SocialOutcome,
    _commit_social,
    _disclose,
    _require_propaganda_media,
    _social_skill_details,
)
from wayfarer.engine.world import World
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class DiplomacySource(Record):
    """Current approved levels and authored conditions, before recognition."""

    target: int
    will: int
    conditions: InfluenceConditions
    skill_context: SocialSkillContext | None = None
    actor_check_modifiers: tuple[Modifier, ...] = ()
    subject_check_modifiers: tuple[Modifier, ...] = ()

    @field_serializer("skill_context")
    def serialize_skill_context(self, value: SocialSkillContext | None) -> dict[str, object] | None:
        # The prerequisite is persisted as JSON inside the private task snapshot.
        # A frozenset's iteration order must not change its bytes after restart.
        return None if value is None else asdict(value) | {"conditions": sorted(value.conditions)}

    @property
    def effective_target(self) -> int:
        return self.target + sum(modifier.value for modifier in self.actor_check_modifiers)

    @property
    def effective_will(self) -> int:
        return self.will + sum(modifier.value for modifier in self.subject_check_modifiers)


class PreparedDiplomacy(Record):
    """The exact source and frozen prerequisite; no fallback dice or effects."""

    reaction: PreparedReaction
    recognized: ResolvedReactionContext
    source: DiplomacySource
    influence: InfluenceTrace
    skill: SocialSkillTrace | None = None


def _source(
    state: ResourceState, command: SocialCommand, context: SocialContext
) -> DiplomacySource:
    if command.kind == "influence" and context.skill == "diplomacy":
        validate_influence(context.profile_id, "diplomacy", context.influence_conditions)
        return DiplomacySource(
            target=context.target,
            will=context.will,
            conditions=context.influence_conditions,
            actor_check_modifiers=check_modifiers(state, command.actor_id, "iq"),
            subject_check_modifiers=check_modifiers(
                state, command.subject_id, "will", defensive=True
            ),
        )
    if command.kind != "skill" or context.procedure_id != "skill:diplomacy":
        raise ValidationError("A prepared Diplomacy fallback requires a Diplomacy entry route")
    _require_propaganda_media("skill:diplomacy", context)
    skill_context = SocialSkillContext(
        context.skill_level,
        context.will,
        context.partner_skill,
        context.conditions,
        command.actor_id,
        command.subject_id,
        influence_conditions=context.influence_conditions,
        coercion=context.coercion,
        callous=context.callous,
    )
    validate_diplomacy_skill_context(
        context.profile_id, skill_context, campaign_specialties=context.campaign_specialties
    )
    return DiplomacySource(
        target=context.skill_level,
        will=context.will,
        conditions=context.influence_conditions,
        skill_context=skill_context,
    )


def prepare_diplomacy(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    *,
    profile_id: str,
    player_actor_ids: tuple[str, ...],
    rng: RandomSource,
    system: bool = False,
    recognition_sources: tuple[SocialCommand, ...] = (),
) -> PreparedDiplomacy:
    """Roll only the admitted prerequisite, retaining an unrolled secret fallback.

    The host checks GM authority before calling and must commit the returned
    prerequisite privately with the pending target. No World.learn, resource
    receipt, target die or Luck use is produced by preparation.
    """
    reaction = _prepare_reaction_source(
        state,
        world,
        command,
        context,
        disclosure,
        profile_id=profile_id,
        player_actor_ids=player_actor_ids,
        system=system,
        recognition_sources=recognition_sources,
    )
    source = _source(state, command, context)
    source = DiplomacySource.model_validate_json(source.model_dump_json())
    recognized = recognize_reaction(reaction, rng=rng)
    skill = None
    if source.skill_context is not None:
        skill = prepare_diplomacy_skill(
            profile_id,
            replace(source.skill_context, reaction_modifiers=recognized.modifiers),
            rng=rng,
            campaign_specialties=context.campaign_specialties,
        )
        assert skill.influence is not None
        influence = skill.influence
    else:
        influence = prepare_influence(
            profile_id,
            "diplomacy",
            command.actor_id,
            command.subject_id,
            source.effective_target,
            source.effective_will,
            recognized.modifiers,
            rng=rng,
            conditions=source.conditions,
        )
    return PreparedDiplomacy(
        reaction=reaction, recognized=recognized, source=source, influence=influence, skill=skill
    )


def validate_prepared_diplomacy(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    prepared: PreparedDiplomacy,
    *,
    profile_id: str,
    player_actor_ids: tuple[str, ...],
    system: bool = False,
) -> None:
    """Revalidate source and the committed prerequisite without rolling again."""
    current = _prepare_reaction_source(
        state,
        world,
        command,
        context,
        disclosure,
        profile_id=profile_id,
        player_actor_ids=player_actor_ids,
        system=system,
        recognition_sources=prepared.reaction.recognition_sources,
    )
    rebound = prepared.reaction.model_copy(
        update={
            "command": prepared.reaction.command.model_copy(
                update={"expected_revision": state.revision}
            )
        }
    )
    source = _source(state, command, context)
    if current != rebound or source != prepared.source:
        raise ValidationError("Prepared Diplomacy source or context changed")
    _validate_recognized_reaction(prepared.reaction, prepared.recognized)
    if source.skill_context is not None:
        if prepared.skill is None or prepared.skill.influence != prepared.influence:
            raise ValidationError("Prepared Diplomacy skill and Influence prerequisite disagree")
        validate_prepared_diplomacy_skill(
            profile_id,
            replace(source.skill_context, reaction_modifiers=prepared.recognized.modifiers),
            prepared.skill,
            campaign_specialties=context.campaign_specialties,
        )
    else:
        if prepared.skill is not None:
            raise ValidationError("An Influence route cannot replace its preceding skill")
        validate_prepared_influence(
            profile_id,
            "diplomacy",
            command.actor_id,
            command.subject_id,
            source.effective_target,
            source.effective_will,
            prepared.recognized.modifiers,
            prepared.influence,
            conditions=source.conditions,
        )


def resolve_prepared_diplomacy(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    prepared: PreparedDiplomacy,
    selected: ReactionTrace,
    *,
    profile_id: str,
    player_actor_ids: tuple[str, ...],
    system: bool = False,
) -> tuple[ResourceState, World, SocialOutcome]:
    """Commit the better response and its authored disclosure exactly once.

    On the skill route, the private skill effect still describes its preceding
    contest. Explicit effect-ID disclosure policies address that prerequisite;
    reaction-band policies address the final NPC response. The final public
    outcome is the combined reaction, so an old failed-contest label cannot
    masquerade as the resolved NPC response or drive a second bad consequence.
    """
    validate_prepared_diplomacy(
        state,
        world,
        command,
        context,
        disclosure,
        prepared,
        profile_id=profile_id,
        player_actor_ids=player_actor_ids,
        system=system,
    )
    recognition = {
        "recognition": [asdict(roll) for roll in prepared.recognized.recognition],
        "recognition_actor_id": command.actor_id,
    }
    source = prepared.source
    skill = None
    if source.skill_context is not None:
        assert prepared.skill is not None
        skill = resolve_diplomacy_skill(
            profile_id,
            replace(source.skill_context, reaction_modifiers=prepared.recognized.modifiers),
            prepared.skill,
            selected,
            campaign_specialties=context.campaign_specialties,
        )
        assert skill.influence is not None
        influence = skill.influence
        details = _social_skill_details(skill, recognition, None)
    else:
        influence = resolve_diplomacy(
            profile_id,
            command.actor_id,
            command.subject_id,
            source.effective_target,
            source.effective_will,
            prepared.recognized.modifiers,
            prepared.influence,
            selected,
            conditions=source.conditions,
        )
        details = asdict(influence) | recognition
    outcome = SocialOutcome(kind=command.kind, outcome=influence.outcome)
    updated = _commit_social(state, command, outcome, details)
    disclosure_outcome = (
        outcome.model_copy(update={"outcome": skill.effect.id})
        if skill is not None and skill.effect.id in disclosure.outcomes
        else outcome
    )
    return updated, _disclose(world, command.actor_id, disclosure, disclosure_outcome), outcome


def cancel_prepared_diplomacy(
    state: ResourceState, prepared: PreparedDiplomacy, *, system: bool = False
) -> ResourceState:
    """Close the source trigger while preserving its private frozen prerequisite.

    No current source checks, target roll, disclosure or prerequisite replay is
    needed. Its already-made recognition remains actor-scoped encounter memory.
    """
    command = prepared.reaction.command
    if command.kind not in ("influence", "skill"):
        raise ValidationError("Diplomacy cancellation requires a prepared Diplomacy route")
    return _cancel_prepared_social(
        state,
        command,
        {
            "preparation": prepared.model_dump(mode="json"),
            "recognition": [asdict(roll) for roll in prepared.recognized.recognition],
            "recognition_actor_id": command.actor_id,
        },
        system=system,
    )
