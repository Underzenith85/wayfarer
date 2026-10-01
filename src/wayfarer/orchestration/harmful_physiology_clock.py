"""Chronological host advancement with effective-body refresh after checkpoints."""

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.traits.harmful_physiology import save
from wayfarer.engine.simulation.traits.harmful_physiology_play import reconcile_actor, settle_actor
from wayfarer.engine.simulation.traits.harmful_physiology_state import (
    AdvancePhysiology,
    HarmfulReceipt,
    conditions,
)
from wayfarer.engine.simulation.traits.physiology import history as injury_history
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def reconcile_all(play: PlayService, state: PlayState, command_id: str) -> PlayState:
    approved = {a.actor_id for a in state.actors if a.approval is not None}
    subjects = sorted(
        {i.actor_id for i in conditions(state.resources) if not i.retired or i.actor_id in approved}
    )
    for actor_id in subjects:
        state = reconcile_actor(play.rules_context, state, actor_id, command_id)
    return state


def advance(
    play: PlayService, state: PlayState, command: AdvancePhysiology
) -> tuple[PlayState, HarmfulReceipt]:
    if command.to < state.resources.game_time:
        raise ValidationError("Game time cannot move backwards")
    revision = state.revision + 1
    previous_injuries = len(injury_history(state.resources))
    state = reconcile_all(play, state, command.id)
    for index in range(10000):
        now = state.resources.game_time
        deadlines = [
            i.deadline
            for i in conditions(state.resources)
            if not i.retired and i.deadline is not None and i.deadline <= command.to
        ]
        # Expiring resource effects and automatic fire exposure checkpoints can
        # change the effective body before the next harmful interval. Other
        # families retain ResourceEngine's own unresolved-obligation guards.
        deadlines += [max(now, s.due) for s in state.resources.scheduled if s.due <= command.to]
        deadlines += [
            h.due
            for h in state.resources.hazards
            if h.active and h.spec.id.startswith("spell-fire:") and now <= h.due <= command.to
        ]
        due = min(deadlines, default=command.to)
        before = state
        state = play.advance_clock(
            state.model_copy(update={"revision": revision - 1}),
            Advance(
                id=f"physiology-clock:{command.id}:{index}",
                actor_id=command.actor_id,
                expected_revision=state.resources.revision,
                to=due,
            ),
        )
        resources = state.resources
        state = state.model_copy(update={"revision": revision})
        subjects = sorted({i.actor_id for i in conditions(resources) if not i.retired})
        for actor_id in subjects:
            state = settle_actor(play.rules_context, state, actor_id, command.id)
        state = play.checkpoint(state, before=before)
        state = reconcile_all(play, state, command.id)
        if due == command.to:
            break
    else:
        raise ValidationError("Physiology advancement exceeds the bounded interval limit")
    receipt = HarmfulReceipt(
        command_id=command.id,
        conditions=conditions(state.resources),
        injuries=tuple(i.outcome for i in injury_history(state.resources)[previous_injuries:]),
        game_time=state.resources.game_time,
    )
    resources = save(state.resources, receipt).model_copy(update={"revision": revision})
    play.engine.resources.validate(resources)
    return state.model_copy(update={"resources": resources, "revision": revision}), receipt
