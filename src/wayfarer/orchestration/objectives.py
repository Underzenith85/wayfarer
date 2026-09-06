"""Objective checkpoints and exactly-once atomic reward settlement."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.advancement import _build
from wayfarer.simulation.actions import ActionCommand, PlayState
from wayfarer.simulation.advancement import AdvancementEntry
from wayfarer.simulation.objectives import ObjectiveState, evaluate
from wayfarer.simulation.resources import Transfer

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


class ObjectiveCommand(ActionCommand):
    kind: Literal["evaluate_objectives", "abandon_scenario"]


def checkpoint(
    play: PlayService, state: PlayState, *, abandon: bool = False, before: PlayState | None = None
) -> PlayState:
    rules = play.engine.rules.objectives
    if rules is None or state.objectives.outcome != "ongoing":
        return state
    if (
        before is not None
        and rules.deadline is not None
        and before.resources.game_time < rules.deadline <= state.resources.game_time
    ):
        boundary = before.model_copy(
            update={
                "revision": state.revision,
                "resources": before.resources.model_copy(
                    update={"revision": state.revision, "game_time": rules.deadline}
                ),
            }
        )
        outcome = evaluate(boundary, rules, abandon=abandon)
    else:
        outcome = evaluate(state, rules, abandon=abandon)
    settled = list(outcome.settled_reward_ids)
    resources, advancement = state.resources, state.advancement
    for reward in rules.rewards:
        if outcome.outcome not in reward.outcomes or reward.id in settled:
            continue
        if reward.points:
            build = _build(play, state, reward.actor_id)
            advancement += (
                AdvancementEntry(
                    id=f"reward:{rules.id}:{reward.id}",
                    actor_id=reward.actor_id,
                    kind="earned",
                    points=reward.points,
                    revision=state.revision,
                    build_before=build.revision,
                    build_after=build.revision,
                    reason=f"Scenario {rules.id}: {outcome.outcome}",
                ),
            )
        if reward.item_id is not None:
            item = next(i for i in resources.items if i.id == reward.item_id)
            resources = play.engine.resources.apply(
                resources,
                Transfer(
                    id=f"reward:{rules.id}:{reward.id}",
                    actor_id=item.owner_id,
                    expected_revision=resources.revision,
                    item_id=item.id,
                    quantity=item.quantity,
                    owner_id=reward.actor_id,
                ),
            )
        settled.append(reward.id)
    return state.model_copy(
        update={
            "resources": resources.model_copy(update={"revision": state.revision}),
            "advancement": advancement,
            "objectives": outcome.model_copy(update={"settled_reward_ids": tuple(settled)}),
        }
    )


class ObjectiveService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> ObjectiveState:
        try:
            command = ObjectiveCommand.model_validate(value)
        except ValueError as exc:
            raise ValidationError("Invalid objective command") from exc
        if command.actor_id != authenticated_actor_id or command.hypothetical:
            raise ValidationError("Objective command is not authorized")
        if self.play.engine.rules.objectives is None:
            raise ValidationError("No configured objectives")
        if (
            command.kind == "abandon_scenario"
            and command.actor_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("Scenario abandonment requires GM authority")
        payload = json.dumps(
            {"operation": "objectives", "command": command.model_dump(mode="json")}, sort_keys=True
        )

        def resolve(campaign: Campaign) -> Event:
            state = self.play._load(campaign)
            if (
                command.actor_id
                not in {a.actor_id for a in state.actors} | self.play.engine.reviewer.gm_ids
            ):
                raise ValidationError("Unknown objective actor")
            revision = state.revision + 1
            state = state.model_copy(
                update={
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                }
            )
            state = checkpoint(self.play, state, abandon=command.kind == "abandon_scenario")
            self.play.engine.validate(state)
            campaign["revision"], campaign["play_json"] = revision, state.model_dump_json()
            return Event(
                input=payload,
                action="objectives",
                outcome=state.objectives.model_dump_json(),
                roll=None,
            )

        committed = await self.play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        return self.play._load(committed["state"]).objectives
