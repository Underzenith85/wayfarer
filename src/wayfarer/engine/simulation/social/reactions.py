"""Private, unrolled canonical reactions and supplied-dice consequences.

B66 predeclaration and B494/B560-561 high-is-good scoring belong to a trusted
host. Preparation never draws recognition or target dice. After its authority,
current-build and cooldown checks, the host resolves recognition once, uses
those modifiers for every candidate, and atomically commits the selected
reaction together with its own pending/Luck records.

This seam preserves the canonical social context and authored disclosure
policy. A learned fact certifies that bounded consequence, not all NPC behavior
on the Reaction Table. Influence contests and their Diplomacy continuation are
separate: a future adapter can use ``evaluate_reaction`` with the frozen
contest's modifiers, then retain the better outcome without repeating it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from pydantic import Field

from wayfarer.engine.rules.checks import NO_RANDOM, RandomSource
from wayfarer.engine.rules.social.gurps_social import (
    ReactionModifier,
    ReactionTrace,
    evaluate_reaction,
)
from wayfarer.engine.rules.social.social_hooks import (
    RECOGNITION_TARGETS,
    REPUTATION_CAP_SOURCE,
    RecognitionRoll,
    Standing,
    StandingTrace,
    cap_reputation_modifiers,
    standing_modifiers,
    validate_standing,
)
from wayfarer.engine.rules.traits.mundane.runtime import Audience
from wayfarer.engine.simulation.health.fright_state import blocked, requires_adjudication
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social.recognition import (
    LegacyRecognitionAttribution as LegacyRecognitionAttribution,
)
from wayfarer.engine.simulation.social.recognition import (
    _remembered_recognition,
    _validate_recognition,
)
from wayfarer.engine.simulation.social.recognition import (
    attribute_legacy_recognition as attribute_legacy_recognition,
)
from wayfarer.engine.simulation.social.social import (
    SocialCommand,
    SocialContext,
    SocialDisclosure,
    SocialOutcome,
    _commit_social,
    _disclose,
    _interaction_modifiers,
    _social_identity,
    _validate_coercion_scope,
    _validate_disclosure,
)
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class ReactionSource(Record):
    """Immutable copy of the fields the canonical reaction actually consumes."""

    profile_id: str
    modifiers: tuple[ReactionModifier, ...]
    required_fact_ids: tuple[str, ...]
    standing: Standing | None
    audience: Audience
    disclosure: SocialDisclosure


class PreparedReaction(Record):
    """Unrolled private source and provenance; contains no selected reaction."""

    command: SocialCommand
    source: ReactionSource
    modifiers: tuple[ReactionModifier, ...]
    known_recognition: tuple[RecognitionRoll, ...]
    provenance: str
    recognition_sources: tuple[SocialCommand, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


class ResolvedReactionContext(Record):
    """One terminal declaration's recognition, shared by all target attempts."""

    preparation_provenance: str
    modifiers: tuple[ReactionModifier, ...]
    recognition: tuple[RecognitionRoll, ...]


def _standing(
    prepared: PreparedReaction,
    *,
    rng: RandomSource,
    recognition: tuple[RecognitionRoll, ...],
) -> StandingTrace:
    if prepared.source.standing is None:
        return StandingTrace(())
    return standing_modifiers(
        prepared.source.profile_id,
        prepared.source.standing,
        prepared.source.audience,
        rng=rng,
        known_recognition={roll.reputation_id: roll for roll in recognition},
        correct_reputation_cap=True,
    )


def _modifiers(prepared: PreparedReaction, standing: StandingTrace) -> tuple[ReactionModifier, ...]:
    # B494 applies both standing and the already-authored lasting situation
    # modifiers. The legacy immediate reducer's omission remains byte-stable;
    # staged reactions preserve both sources without rewriting old receipts.
    return cap_reputation_modifiers(standing.modifiers + prepared.modifiers)


def _validate_social_admission(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    *,
    profile_id: str,
    player_actor_ids: tuple[str, ...],
    system: bool,
) -> None:
    if not system:
        raise ValidationError("Prepared reactions require authoritative trigger context")
    if context.profile_id != profile_id:
        raise ValidationError("Reaction context does not match campaign profile")
    if not command.trigger_id or not command.subject_id:
        raise ValidationError("Social checks require stable subject and trigger IDs")
    if command.actor_id == command.subject_id:
        raise ValidationError("Reaction requires distinct actor and NPC subject IDs")
    actors = {entity.id for entity in world.entities if entity.kind is EntityKind.ACTOR}
    if not {command.actor_id, command.subject_id} <= actors:
        raise ValidationError("Social checks require world actors")
    if command.subject_id in player_actor_ids:
        raise ValidationError("NPC social outcomes cannot control a player character")
    _validate_coercion_scope(command, context, actors)
    _validate_disclosure(world, command, disclosure)
    known = {fact.id for fact in world.perspective(command.subject_id).facts}
    if not set(context.required_fact_ids) <= known:
        raise ValidationError("Subject lacks the evidence required for this social trigger")
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    if any(receipt.command_id == command.id for receipt in state.receipts):
        raise ConflictError("Social command already committed")
    _, event_id, legacy_id = _social_identity(command)
    if any(
        event.id in (event_id, legacy_id) and event.target_id == command.subject_id
        for event in state.events
    ):
        raise ConflictError("Social trigger already resolved")
    if any(
        requires_adjudication(state, actor_id, handles_aftermath=True)
        for actor_id in (command.actor_id, command.subject_id)
    ):
        raise ValidationError("Resolve permanent fright losses before further social checks")
    if any(blocked(state, actor_id) for actor_id in (command.actor_id, command.subject_id)):
        raise ValidationError("Incapacitated actors cannot participate in a social interaction")


def _prepare_reaction_source(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    *,
    profile_id: str,
    player_actor_ids: tuple[str, ...],
    system: bool = False,
    recognition_sources: tuple[SocialCommand, ...] = (),
) -> PreparedReaction:
    """Validate and bind an unrolled canonical NPC reaction without any effects.

    ``player_actor_ids`` comes from current authoritative campaign membership;
    World intentionally does not invent a second classification of NPCs.
    ``profile_id`` is the campaign's current profile. The host owns approved
    build/trait binding before passing this trusted SocialContext.
    """
    _validate_social_admission(
        state,
        world,
        command,
        context,
        disclosure,
        profile_id=profile_id,
        player_actor_ids=player_actor_ids,
        system=system,
    )

    if any(modifier.source_id == REPUTATION_CAP_SOURCE for modifier in context.modifiers):
        raise ValidationError("Aggregate reputation cap provenance is generated, not authored")
    source = ReactionSource(
        profile_id=context.profile_id,
        modifiers=context.modifiers,
        required_fact_ids=context.required_fact_ids,
        standing=context.standing,
        audience=context.audience,
        disclosure=disclosure,
    )
    # Round-trip also checks nested frozen dataclasses and detaches their inputs.
    source = ReactionSource.model_validate_json(source.model_dump_json())
    modifiers = _interaction_modifiers(state, command, context, influenced=True)
    evaluate_reaction(profile_id, modifiers, (1, 1, 1))
    recognition: tuple[RecognitionRoll, ...] = ()
    if source.standing is not None:
        validate_standing(source.standing)
        reputation_ids = {
            rep.id
            for rep in source.standing.reputations
            if rep.recognition != "always"
            and (rep.scope == "everyone" or set(rep.classes) & set(source.audience.classes))
        }
        recognition = tuple(
            roll
            for key, roll in _remembered_recognition(
                state, command.actor_id, command.subject_id, reputation_ids, recognition_sources
            ).items()
            if key in reputation_ids
        )
        for roll in recognition:
            _validate_recognition(roll)
    source_facts = set(source.required_fact_ids) | set(disclosure.fact_ids)
    provenance = hashlib.sha256(
        json.dumps(
            {
                "command": command.model_dump(mode="json", exclude={"expected_revision"}),
                "source": source.model_dump(mode="json"),
                "actors": [
                    asdict(entity)
                    for entity in world.entities
                    if entity.id in (command.actor_id, command.subject_id)
                ],
                "facts": [asdict(fact) for fact in world.facts if fact.id in source_facts],
                "modifiers": [asdict(modifier) for modifier in modifiers],
                "recognition": [asdict(roll) for roll in recognition],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    prepared = PreparedReaction(
        command=command,
        source=source,
        modifiers=modifiers,
        known_recognition=recognition,
        provenance=provenance,
        recognition_sources=recognition_sources,
    )
    # Validate every potentially applied source before any recognition dice.
    possible = {roll.reputation_id: roll for roll in recognition}
    if source.standing is not None:
        for reputation in source.standing.reputations:
            target = RECOGNITION_TARGETS[reputation.recognition]
            if target is not None and reputation.id not in possible:
                possible[reputation.id] = RecognitionRoll(reputation.id, (1, 1, 1), 3, target, True)
    standing = _standing(prepared, rng=NO_RANDOM, recognition=tuple(possible.values()))
    evaluate_reaction(profile_id, _modifiers(prepared, standing), (1, 1, 1))
    return prepared


def prepare_reaction(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    *,
    profile_id: str,
    player_actor_ids: tuple[str, ...],
    system: bool = False,
    recognition_sources: tuple[SocialCommand, ...] = (),
) -> PreparedReaction:
    """Prepare a canonical reaction; Influence has a distinct frozen prerequisite."""
    if command.kind != "reaction":
        raise ValidationError("Only canonical reactions can use this preparation")
    return _prepare_reaction_source(
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


def validate_prepared_reaction(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    prepared: PreparedReaction,
    *,
    profile_id: str,
    player_actor_ids: tuple[str, ...],
    system: bool = False,
) -> None:
    """Recheck current admission and all bound source data before terminal dice.

    Only expected resource revision may differ after a private pending/clock
    commit; command/trigger identity and actual context remain bound.
    """
    current = prepare_reaction(
        state,
        world,
        command,
        context,
        disclosure,
        profile_id=profile_id,
        player_actor_ids=player_actor_ids,
        system=system,
        recognition_sources=prepared.recognition_sources,
    )
    rebound = prepared.model_copy(
        update={
            "command": prepared.command.model_copy(update={"expected_revision": state.revision})
        }
    )
    if current != rebound:
        raise ValidationError("Prepared reaction source or context changed")


def recognize_reaction(prepared: PreparedReaction, *, rng: RandomSource) -> ResolvedReactionContext:
    """Call once inside an admitted terminal transaction, before target dice.

    Reuse the returned context for one ordinary or all three Luck attempts.
    This function itself records no effect; the host atomically persists only
    the final selected reaction and its recognition history.
    """
    standing = _standing(prepared, rng=rng, recognition=prepared.known_recognition)
    modifiers = _modifiers(prepared, standing)
    evaluate_reaction(prepared.source.profile_id, modifiers, (1, 1, 1))
    return ResolvedReactionContext(
        preparation_provenance=prepared.provenance,
        modifiers=modifiers,
        recognition=standing.recognition,
    )


def _validate_recognized_reaction(
    prepared: PreparedReaction, recognized: ResolvedReactionContext
) -> None:
    if recognized.preparation_provenance != prepared.provenance:
        raise ValidationError("Recognized reaction belongs to another preparation")
    recognition = {roll.reputation_id: roll for roll in recognized.recognition}
    if len(recognition) != len(recognized.recognition):
        raise ValidationError("Duplicate reaction recognition")
    remembered = {roll.reputation_id: roll for roll in prepared.known_recognition}
    targets = (
        {
            rep.id: RECOGNITION_TARGETS[rep.recognition]
            for rep in prepared.source.standing.reputations
        }
        if prepared.source.standing is not None
        else {}
    )
    for roll in recognized.recognition:
        _validate_recognition(roll)
        # A genuinely remembered encounter retains its original check, even if
        # a later source changes recognition frequency. A fresh row must use
        # the exact prepared Reputation's target, never another valid target.
        if roll.reputation_id not in remembered and roll.target != targets.get(roll.reputation_id):
            raise ValidationError("Fresh recognition does not match the prepared reputation")
    if any(recognition.get(key) != roll for key, roll in remembered.items()):
        raise ValidationError("Remembered reaction recognition changed")
    if any(
        type(modifier.hidden) is not bool
        or type(modifier.source_id) is not str
        or type(modifier.kind) is not str
        for modifier in recognized.modifiers
    ):
        raise ValidationError("Recognized reaction modifier provenance changed")
    evaluate_reaction(prepared.source.profile_id, recognized.modifiers, (1, 1, 1))
    standing = _standing(prepared, rng=NO_RANDOM, recognition=recognized.recognition)
    if recognized.recognition != standing.recognition or recognized.modifiers != _modifiers(
        prepared, standing
    ):
        raise ValidationError("Selected reaction recognition or modifiers changed")


def resolve_prepared_reaction(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    prepared: PreparedReaction,
    recognized: ResolvedReactionContext,
    selected: ReactionTrace,
    *,
    profile_id: str,
    player_actor_ids: tuple[str, ...],
    system: bool = False,
) -> tuple[ResourceState, World, SocialOutcome]:
    """Rescore a selected reaction and commit through the canonical World.learn.

    A resolved social receipt cannot reopen a secret target. The outer host owns
    exact command retry. No RNG is accepted here, so target selection cannot
    accidentally become a reputation-recognition or Influence roll.
    """
    validate_prepared_reaction(
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
    _validate_recognized_reaction(prepared, recognized)
    rescored = evaluate_reaction(profile_id, recognized.modifiers, selected.dice)
    if type(selected.total) is not int or rescored != selected:
        raise ValidationError("Selected reaction does not match supplied dice and modifiers")
    outcome = SocialOutcome(kind="reaction", outcome=rescored.outcome)
    details = asdict(rescored) | {
        "recognition": [asdict(roll) for roll in recognized.recognition],
        "recognition_actor_id": command.actor_id,
    }
    updated = _commit_social(state, command, outcome, details)
    return updated, _disclose(world, command.actor_id, disclosure, outcome), outcome


def _cancel_prepared_social(
    state: ResourceState,
    command: SocialCommand,
    details: object,
    *,
    system: bool,
) -> ResourceState:
    """Close the source trigger without checking a context that may have vanished."""
    if not system:
        raise ValidationError("Prepared social cancellation requires authoritative context")
    if any(receipt.command_id == command.id for receipt in state.receipts):
        raise ConflictError("Social command already committed")
    _, event_id, legacy_id = _social_identity(command)
    if any(
        event.id in (event_id, legacy_id) and event.target_id == command.subject_id
        for event in state.events
    ):
        raise ConflictError("Social trigger already resolved")
    return _commit_social(
        state, command, SocialOutcome(kind=command.kind, outcome="cancelled"), details
    )


def cancel_prepared_reaction(
    state: ResourceState, prepared: PreparedReaction, *, system: bool = False
) -> ResourceState:
    """Terminally cancel the original source identity, drawing/revealing nothing.

    The host validates current GM authority and pending identity first. Current
    actor/build/fact/standing loss never prevents cancellation. Recording the
    original command and trigger also closes the old immediate social entry.
    """
    if prepared.command.kind != "reaction":
        raise ValidationError("Reaction cancellation requires a prepared reaction")
    return _cancel_prepared_social(
        state,
        prepared.command,
        {"preparation": prepared.model_dump(mode="json")},
        system=system,
    )
