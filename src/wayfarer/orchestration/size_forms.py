"""Actor-controlled, durable core size transitions through the common command pipeline."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.settlement import reconcile_equipment
from wayfarer.engine.simulation.combat.thrown.flight import position
from wayfarer.engine.simulation.equipment.world_ground import (
    WorldGroundCommand,
    apply_world_ground,
    require_movable_gear,
)
from wayfarer.engine.simulation.resources import is_carried
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.engine.simulation.traits.size_forms import (
    SizeFormCommand,
    SizeFormEffect,
    apply_size_form,
    effect_for,
    size_delta,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, submit

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def _leave_gear(play: PlayService, state: PlayState, actor_id: str, command_id: str) -> PlayState:
    carried = tuple(
        i
        for i in state.resources.items
        if i.owner_id == actor_id and is_carried(state.resources, i)
    )
    if not carried:
        return state
    require_movable_gear(state.resources, frozenset(i.id for i in carried))
    encounter = next(
        (
            e
            for e in state.encounters
            if e.status == "active" and any(p.actor_id == actor_id for p in e.participants)
        ),
        None,
    )
    subject = (
        next((p for p in encounter.participants if p.actor_id == actor_id), None)
        if encounter
        else None
    )
    resources = state.resources
    if encounter and subject and subject.runtime_position is not None:
        ground = position(encounter, subject)
        identifiers = {i.id for i in carried}
        resources = resources.model_copy(
            update={
                "items": tuple(
                    i.model_copy(
                        update={
                            "ground": ground if i.container_id is None else None,
                            "equipped": False,
                            "ready": False,
                        }
                    )
                    if i.id in identifiers
                    else i
                    for i in resources.items
                )
            }
        )
        play.engine.resources.for_world(state.world).validate(resources)
    else:
        for item in carried:
            if item.container_id is not None:
                continue
            resources, _ = apply_world_ground(
                resources,
                state.world,
                play.engine.resources,
                WorldGroundCommand(
                    id=command_id + ":drop:" + item.id,
                    actor_id=actor_id,
                    expected_revision=resources.revision,
                    kind="drop",
                    item_id=item.id,
                ),
                authorized_actor_id=actor_id,
                system=True,
            )
    return state.model_copy(
        update={
            "resources": resources,
            "actors": tuple(
                a.model_copy(update={"held_item_hands": ()}) if a.actor_id == actor_id else a
                for a in state.actors
            ),
            "encounters": tuple(
                e.model_copy(
                    update={
                        "participants": tuple(
                            p.model_copy(update={"hand_bindings": (), "ready_item_ids": ()})
                            if p.actor_id == actor_id
                            else p
                            for p in e.participants
                        )
                    }
                )
                if e.status == "active"
                else e
                for e in state.encounters
            ),
        }
    )


class SizeFormService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(self, cid: str, value: object, *, principal_id: str) -> SizeFormEffect:
        try:
            command = SizeFormCommand.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid size-form command") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        member = member_for(play._load(await play.store.read(cid)), principal_id)
        payload = json.dumps(
            {"operation": "size-form", "principal": principal_id, "command": command.model_dump()},
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = play._load(campaign)
            if any(
                r.actor_id == command.actor_id
                and r.kind in {"alternate-form", "morph"}
                and r.status in {"active", "reverting", "treatment", "proposed"}
                for r in state.transformations.records
            ):
                raise ValidationError(
                    "Size changes in an alternate body require a separately verified protocol"
                )
            compiled = build(play.rules_context, state, command.actor_id)
            if command.kind == "start" and command.target_delta < 0:
                state = _leave_gear(play, state, command.actor_id, command.id)
            resources, effect = apply_size_form(
                state.resources,
                command.model_copy(update={"expected_revision": state.resources.revision}),
                compiled,
                authorized_actor_id=command.actor_id,
                system=True,
                in_combat=any(
                    e.status == "active"
                    and any(p.actor_id == command.actor_id for p in e.participants)
                    for e in state.encounters
                ),
            )
            revision = state.revision + 1
            updated = state.model_copy(
                update={
                    "revision": revision,
                    "resources": resources.model_copy(update={"revision": revision}),
                }
            )
            play.commit(campaign, updated)
            return CommandReceipt(action="transformation", outcome=effect.model_dump_json())

        async def outcome(campaign: Campaign) -> SizeFormEffect:
            effect = effect_for(play._load(campaign).resources, command.actor_id)
            assert effect is not None
            return effect

        return await submit(
            play,
            cid,
            CommandPlan(
                command_id=command.id,
                expected_revision=command.expected_revision,
                payload=payload,
                resolve=resolve,
                actor_id=principal_id,
                outcome=outcome,
                control=(
                    Controls(
                        member, command.actor_id, state=play._load(await play.store.read(cid))
                    ),
                ),
                rng=play.rng,
            ),
            principal_id=principal_id,
        )

    async def retrieve(self, cid: str, value: object, *, principal_id: str) -> None:
        try:
            command = WorldGroundCommand.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid world equipment command") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        member = member_for(play._load(await play.store.read(cid)), principal_id)
        payload = json.dumps(
            {
                "operation": "world-equipment",
                "principal": principal_id,
                "command": command.model_dump(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = play._load(campaign)
            require_innate_actor_action(state, command.actor_id)
            if command.kind == "retrieve" and size_delta(state.resources, command.actor_id) < 0:
                raise ValidationError("Core Shrinking cannot carry equipment")
            resources, result = apply_world_ground(
                state.resources,
                state.world,
                play.engine.resources,
                command,
                authorized_actor_id=command.actor_id,
                system=True,
            )
            revision = state.revision + 1
            updated = state.model_copy(
                update={
                    "revision": revision,
                    "resources": resources.model_copy(update={"revision": revision}),
                    "actors": tuple(
                        a.model_copy(
                            update={
                                "held_item_hands": tuple(
                                    (i, h) for i, h in a.held_item_hands if i not in result.item_ids
                                )
                            }
                        )
                        if a.actor_id == command.actor_id
                        else a
                        for a in state.actors
                    ),
                }
            )
            for encounter in updated.encounters:
                if encounter.status == "active" and command.actor_id in encounter.turn_order:
                    updated, encounter = reconcile_equipment(updated, encounter)
                    updated = updated.model_copy(
                        update={
                            "encounters": tuple(
                                encounter if e.id == encounter.id else e for e in updated.encounters
                            )
                        }
                    )
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> None:
            return None

        await submit(
            play,
            cid,
            CommandPlan(
                command_id=command.id,
                expected_revision=command.expected_revision,
                payload=payload,
                resolve=resolve,
                actor_id=principal_id,
                outcome=outcome,
                control=(
                    Controls(
                        member, command.actor_id, state=play._load(await play.store.read(cid))
                    ),
                ),
                rng=play.rng,
            ),
            principal_id=principal_id,
        )
