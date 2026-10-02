"""Task-host joins for campaign reaction prerequisites and actual consequences."""

import json
from dataclasses import asdict

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.reactions import (
    CampaignReactionSource,
    PreparedCampaignReaction,
)
from wayfarer.engine.simulation.traits.luck import LuckRoll
from wayfarer.errors import ConflictError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_context import (
    player_actor_ids,
    require_reaction_actor,
    require_reaction_boundary,
)
from wayfarer.orchestration.reaction_luck import admit_reaction, finish_reaction, select_reaction
from wayfarer.orchestration.reaction_records import (
    ChooseReaction,
    PrepareReaction,
    SecretReactionPending,
)
from wayfarer.orchestration.real_play_clock import RealPlayClock
from wayfarer.orchestration.task_records import TaskResult, TaskSnapshot, identity


def open_campaign_reaction(
    play: PlayService,
    state: PlayState,
    command: PrepareReaction,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    source = command.source
    assert isinstance(source, CampaignReactionSource)
    pending_id = identity("reaction-campaign:", source.command.id)
    if (
        saved.pending is not None
        or any(roll.id == pending_id for roll in saved.luck.rolls)
        or any(receipt.command_id == source.command.id for receipt in state.resources.receipts)
    ):
        raise ConflictError("Campaign reaction source is pending or already committed")
    state, prepared, terminal = play.engine.campaign.prepare_reaction(
        state,
        source,
        actor_id=command.actor_id,
        command_id=pending_id,
        rng=play.rng,
        player_actor_ids=player_actor_ids(state),
        recognition_sources=command.recognition_sources,
    )
    if prepared is None:
        assert terminal is not None
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="completed",
                secret=True,
                reaction_json=terminal.model_dump_json(),
            ),
        )
    pending = SecretReactionPending(
        id=pending_id,
        role="campaign",
        actor_id=command.actor_id,
        prepared_elapsed_microseconds=clock.elapsed_microseconds,
        source=source,
        preparation_json=prepared.model_dump_json(),
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


def choose_campaign_reaction(
    play: PlayService,
    state: PlayState,
    command: ChooseReaction,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    pending: SecretReactionPending,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    prepared = PreparedCampaignReaction.model_validate_json(pending.preparation_json)
    if command.choice == "cancel":
        state, _ = play.engine.campaign.cancel_reaction(state, prepared)
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
    play.engine.campaign.validate_reaction(
        state, prepared, player_actor_ids=player_actor_ids(state)
    )
    admitted = admit_reaction(play, state, command, saved, clock)
    recognized = play.engine.campaign.recognize_reaction(prepared, rng=play.rng)
    luck, receipt, selected = select_reaction(
        play, admitted, prepared.profile_id, recognized.modifiers
    )
    state, outcome = play.engine.campaign.resolve_reaction(
        state,
        prepared,
        selected,
        rng=play.rng,
        player_actor_ids=player_actor_ids(state),
        recognized=recognized,
    )
    saved = finish_reaction(saved.model_copy(update={"luck": luck}), pending, selected)
    state = state.model_copy(
        update={
            "last_result": None,
            "party": state.party.model_copy(
                update={
                    "groups": tuple(
                        group.model_copy(
                            update={
                                "ready_through": max(group.ready_through, state.resources.game_time)
                            }
                        )
                        for group in state.party.groups
                    ),
                }
            ),
        }
    )
    state = play.checkpoint(state)
    return (
        state,
        saved,
        admitted.clock,
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="completed",
            secret=True,
            luck=receipt,
            reaction_json=json.dumps(
                {"reaction": asdict(selected), "outcome": outcome.model_dump(mode="json")},
                sort_keys=True,
            ),
        ),
    )
