"""Director-only care and panic decisions using the existing campaign CAS ledger."""

import json
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.simulation.fright import effects, save
from wayfarer.simulation.resources import Command


class FrightDecision(Command):
    kind: Literal["care", "panic-response"]
    fright_id: str
    care: bool | None = None
    response: str | None = Field(default=None, min_length=1, max_length=1000)


class FrightService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(self, cid: str, value: object, *, authenticated_gm_id: str) -> None:
        try:
            command = FrightDecision.model_validate(value)
        except ValueError as exc:
            raise ValidationError("Invalid fright decision") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        member = CampaignAccess(play)._member(
            play._load(await play.store.read(cid)), authenticated_gm_id
        )
        if member.role != "gm" or authenticated_gm_id not in play.engine.reviewer.gm_ids:
            raise ValidationError("Fright decisions require director authority")
        payload = json.dumps(
            {
                "operation": "fright-decision",
                "principal": authenticated_gm_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> Event:
            before = play._load(campaign)
            item = next(
                (
                    i
                    for i in effects(before.resources)
                    if i.id == command.fright_id and i.actor_id == command.actor_id and i.active
                ),
                None,
            )
            if item is None:
                raise ConflictError("No active fright consequence for this actor")
            resources = before.resources
            if command.kind == "care":
                if not item.effect.neglect_progression or command.care is None or command.response:
                    raise ValidationError(
                        "Care decision requires active catatonia and a care value"
                    )
                if item.due is not None and item.due <= resources.game_time:
                    raise ConflictError("Settle due catatonia before changing care")
                item = item.model_copy(update={"care": command.care})
            else:
                if (
                    item.effect.table_total != 33
                    or command.response is None
                    or command.care is not None
                ):
                    raise ValidationError("Panic response requires an explicit row-33 adjudication")
                # Record a completed, externally adjudicated response, not a forced player action.
                passed = success_roll(
                    "gurps-basic-set-4e-2004", item.recovery_target, rng=play.rng
                ).outcome.succeeded
                effect = item.effect
                if not passed:
                    effect = effect.model_copy(
                        update={
                            "panic_severity": sum(play.rng.randbelow(6) + 1 for _ in range(3)),
                        }
                    )
                item = item.model_copy(
                    update={
                        "active": not passed,
                        "effect": effect,
                        "panic_responses": item.panic_responses + (command.response,),
                    }
                )
            resources = save(resources, item, command.id).model_copy(
                update={"revision": before.revision + 1}
            )
            updated = before.model_copy(
                update={"revision": before.revision + 1, "resources": resources}
            )
            play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            return Event(input=payload, action="npc", outcome="fright decision recorded", roll=None)

        await play.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=authenticated_gm_id,
        )
