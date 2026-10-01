"""Trusted B130/B161 host commands on the single-writer private event stream."""

import json

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.traits.harmful_physiology import HarmfulContext, apply
from wayfarer.engine.simulation.traits.harmful_physiology_state import (
    COMMAND_ADAPTER,
    AdvancePhysiology,
    DeclarePhysiologyCalendar,
    HarmfulCommand,
    HarmfulReceipt,
    conditions,
    history,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


def _subjects(state: PlayState, command: HarmfulCommand) -> tuple[str, ...]:
    if isinstance(command, DeclarePhysiologyCalendar):
        return ()
    approved = {actor.actor_id for actor in state.actors if actor.approval is not None}
    candidates = (
        {
            item.actor_id
            for item in conditions(state.resources)
            if not item.retired or item.actor_id in approved
        }
        if isinstance(command, AdvancePhysiology)
        else {command.actor_id}
    )
    dead = {
        pool.id.removeprefix("hp:")
        for pool in state.resources.pools
        if pool.id.startswith("hp:") and pool.injury is not None and pool.injury.dead
    }
    return tuple(sorted(candidates - dead))


class HarmfulPhysiologyService:
    """The director records facts; approved builds supply rates, HT and injury.

    No client-supplied active flag, start, damage amount or frequency reaches the
    interval reducer. Calendar and observation commands persist those facts,
    while advance settles their exact deadlines inside one seeded transaction.
    """

    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, play: PlayService, state: PlayState, command: HarmfulCommand, *, principal_id: str
    ) -> CommandPlan[HarmfulReceipt]:
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Harmful physiology requires the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "harmful-physiology",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            transformations = play.engine.rules.transformations
            context = HarmfulContext(
                builds={
                    actor_id: play.rules_context.approved_build(before, actor_id)
                    for actor_id in _subjects(before, command)
                },
                definitions=play.engine.reviewer.compiler.definitions,
                engine=play.engine.resources.for_world(before.world),
                rng=play.rng,
                transformation_actor_ids=frozenset(
                    rule.actor_id for rule in transformations.transformations
                )
                if transformations is not None
                else frozenset(),
            )
            resources, result = apply(before.resources, command, context)
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> HarmfulReceipt:
            result = next(
                (
                    r
                    for r in reversed(history(play._load(campaign).resources))
                    if r.command_id == command.id
                ),
                None,
            )
            if result is None:
                raise ConflictError("Harmful physiology canonical receipt is missing")
            return result

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Seats(state), Trusted(play.engine.reviewer.gm_ids)),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> HarmfulReceipt:
        try:
            command = COMMAND_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid harmful physiology command") from exc
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
