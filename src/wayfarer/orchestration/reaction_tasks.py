"""Atomic B66 secret reaction choices on the existing task host transaction."""

import json
from dataclasses import asdict

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.reactions import CampaignReactionSource
from wayfarer.engine.simulation.social.diplomacy import (
    PreparedDiplomacy,
    cancel_prepared_diplomacy,
    prepare_diplomacy,
    resolve_prepared_diplomacy,
    validate_prepared_diplomacy,
)
from wayfarer.engine.simulation.social.reactions import (
    PreparedReaction,
    cancel_prepared_reaction,
    prepare_reaction,
    recognize_reaction,
    resolve_prepared_reaction,
    validate_prepared_reaction,
)
from wayfarer.engine.simulation.traits.luck import (
    LuckRoll,
)
from wayfarer.errors import ConflictError
from wayfarer.orchestration.campaign_reaction_tasks import (
    choose_campaign_reaction,
    open_campaign_reaction,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_context import (
    player_actor_ids,
    require_reaction_actor,
    require_reaction_boundary,
    social_context,
)
from wayfarer.orchestration.reaction_luck import admit_reaction, finish_reaction, select_reaction
from wayfarer.orchestration.reaction_occurrences import (
    PreparedOccurrence,
    finish_occurrence,
    occurrence_source,
    prepare_occurrence,
)
from wayfarer.orchestration.reaction_records import (
    AuthoredSocialReaction,
    ChooseReaction,
    NPCReactionSource,
    PrepareReaction,
    SecretReactionPending,
)
from wayfarer.orchestration.real_play_clock import RealPlayClock
from wayfarer.orchestration.task_records import TaskResult, TaskSnapshot, identity


def _source(
    play: PlayService,
    state: PlayState,
    pending: SecretReactionPending,
) -> AuthoredSocialReaction:
    if isinstance(pending.source, AuthoredSocialReaction):
        return pending.source
    assert isinstance(pending.source, NPCReactionSource)
    source, current = occurrence_source(play, state, pending.actor_id, pending.source)
    if current.model_dump_json() != pending.occurrence_json:
        raise ConflictError("NPC occurrence changed before its reaction")
    return source


def open_reaction(
    play: PlayService,
    state: PlayState,
    command: PrepareReaction,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    require_reaction_actor(play.rules_context, state, command.actor_id)
    require_reaction_boundary(state, command.actor_id)
    if isinstance(command.source, CampaignReactionSource):
        return open_campaign_reaction(play, state, command, saved, clock)
    occurrence = None
    if isinstance(command.source, NPCReactionSource):
        state, source, occurrence = prepare_occurrence(
            play,
            state,
            command.actor_id,
            command.source,
        )
    else:
        source = command.source
    pending_id = identity(
        "reaction-check:",
        json.dumps([command.actor_id, source.subject_id, source.trigger_id, source.mode]),
    )
    if saved.pending is not None or any(roll.id == pending_id for roll in saved.luck.rolls):
        raise ConflictError("Reaction opportunity is pending or its identity was already used")
    social, context = social_context(
        play, state, command.actor_id, identity("reaction-social:", pending_id), source
    )
    prepared: PreparedReaction | PreparedDiplomacy
    if source.mode == "reaction":
        prepared = prepare_reaction(
            state.resources,
            state.world,
            social,
            context,
            source.disclosure,
            profile_id=context.profile_id,
            player_actor_ids=player_actor_ids(state),
            system=True,
            recognition_sources=command.recognition_sources,
        )
    else:
        prepared = prepare_diplomacy(
            state.resources,
            state.world,
            social,
            context,
            source.disclosure,
            profile_id=context.profile_id,
            player_actor_ids=player_actor_ids(state),
            system=True,
            recognition_sources=command.recognition_sources,
            rng=play.rng,
        )
    pending = SecretReactionPending(
        id=pending_id,
        role="reaction" if source.mode == "reaction" else "diplomacy",
        actor_id=command.actor_id,
        prepared_elapsed_microseconds=clock.elapsed_microseconds,
        source=command.source,
        preparation_json=prepared.model_dump_json(),
        occurrence_json=occurrence.model_dump_json() if occurrence else None,
    )
    roll = LuckRoll(
        id=pending.id,
        actor_id=pending.actor_id,
        kind="reaction",
        scope="own",
        secret=True,
        task_class="social",
    )
    saved = saved.model_copy(
        update={
            "pending": pending,
            "luck": saved.luck.model_copy(
                update={
                    "revision": state.revision,
                    "game_time": state.resources.game_time,
                    "rolls": saved.luck.rolls + (roll,),
                    "pending_roll_id": pending.id,
                }
            ),
        }
    )
    return (
        state,
        saved,
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="pending",
            pending_id=pending.id,
            secret=True,
        ),
    )


def choose_reaction(
    play: PlayService,
    state: PlayState,
    command: ChooseReaction,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if (
        not isinstance(pending, SecretReactionPending)
        or pending.id != command.pending_id
        or pending.actor_id != command.actor_id
    ):
        raise ConflictError("Reaction is no longer the immediate unrolled choice")
    if isinstance(pending.source, CampaignReactionSource):
        return choose_campaign_reaction(play, state, command, saved, clock, pending)
    occurrence = (
        PreparedOccurrence.model_validate_json(pending.occurrence_json)
        if pending.occurrence_json
        else None
    )
    if command.choice == "cancel":
        resources = (
            cancel_prepared_reaction(
                state.resources,
                PreparedReaction.model_validate_json(pending.preparation_json),
                system=True,
            )
            if pending.role == "reaction"
            else cancel_prepared_diplomacy(
                state.resources,
                PreparedDiplomacy.model_validate_json(pending.preparation_json),
                system=True,
            )
        )
        state = state.model_copy(update={"resources": resources})
        if occurrence:
            state = finish_occurrence(state, occurrence, cancelled=True)
        return (
            state,
            finish_reaction(saved, pending, None),
            clock,
            TaskResult(
                command_id=command.id, actor_id=command.actor_id, status="cancelled", secret=True
            ),
        )
    require_reaction_actor(play.rules_context, state, command.actor_id)
    require_reaction_boundary(state, command.actor_id)
    source = _source(play, state, pending)
    prepared: PreparedReaction | PreparedDiplomacy = (
        PreparedReaction.model_validate_json(pending.preparation_json)
        if source.mode == "reaction"
        else PreparedDiplomacy.model_validate_json(pending.preparation_json)
    )
    original = (
        prepared.command if isinstance(prepared, PreparedReaction) else prepared.reaction.command
    )
    social, context = social_context(play, state, pending.actor_id, original.id, source)
    if isinstance(prepared, PreparedReaction):
        validate_prepared_reaction(
            state.resources,
            state.world,
            social,
            context,
            source.disclosure,
            prepared,
            profile_id=context.profile_id,
            player_actor_ids=player_actor_ids(state),
            system=True,
        )
    else:
        validate_prepared_diplomacy(
            state.resources,
            state.world,
            social,
            context,
            source.disclosure,
            prepared,
            profile_id=context.profile_id,
            player_actor_ids=player_actor_ids(state),
            system=True,
        )
    admitted = admit_reaction(play, state, command, saved, clock)
    recognized = (
        recognize_reaction(prepared, rng=play.rng)
        if isinstance(prepared, PreparedReaction)
        else prepared.recognized
    )
    luck, receipt, selected = select_reaction(
        play, admitted, context.profile_id, recognized.modifiers
    )
    clock = admitted.clock
    if isinstance(prepared, PreparedReaction):
        resources, world, outcome = resolve_prepared_reaction(
            state.resources,
            state.world,
            social,
            context,
            source.disclosure,
            prepared,
            recognized,
            selected,
            profile_id=context.profile_id,
            player_actor_ids=player_actor_ids(state),
            system=True,
        )
    else:
        resources, world, outcome = resolve_prepared_diplomacy(
            state.resources,
            state.world,
            social,
            context,
            source.disclosure,
            prepared,
            selected,
            profile_id=context.profile_id,
            player_actor_ids=player_actor_ids(state),
            system=True,
        )
    state = state.model_copy(update={"resources": resources, "world": world, "last_result": None})
    if occurrence:
        state = finish_occurrence(state, occurrence, cancelled=False)
    saved = finish_reaction(saved.model_copy(update={"luck": luck}), pending, selected)
    state = play.checkpoint(state)
    result = TaskResult(
        command_id=command.id,
        actor_id=command.actor_id,
        status="completed",
        secret=True,
        luck=receipt,
        reaction_json=json.dumps(
            {
                "reaction": asdict(selected),
                "recognition": [asdict(value) for value in recognized.recognition],
                "outcome": outcome.model_dump(mode="json"),
            },
            sort_keys=True,
        ),
    )
    return state, saved, clock, result
