"""Scenario-bound environmental commands with CAS/replay and shared-clock deadlines."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from wayfarer.character.statistics import encumbrance
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.medical import _build, _value
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.hazards import HazardCommand, HazardResult, apply_hazard


@dataclass(frozen=True)
class HazardContext:
    spec: HazardSpec
    resistance: int = 0
    safe: bool = False


HazardResolver = Callable[[PlayService, PlayState, str, str], HazardContext]


class HazardService:
    """Internal service; only the trusted resolver supplies exposure and protection.

    Enter records a scenario's exposure, resolve settles one deadline, and leave
    requires a trusted safe environment. Players cannot set damage, duration or HT.
    """

    def __init__(self, play: PlayService, resolver: HazardResolver) -> None:
        self.play, self.resolver = play, resolver

    async def execute(
        self, cid: str, command: HazardCommand, *, authenticated_actor_id: str
    ) -> HazardResult:
        command = HazardCommand.model_validate(command)
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Hazard actor does not match authenticated actor")
        play = self.play.for_campaign(await self.play.store.read(cid))
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Hazards require exact Basic Set profile")
        payload = json.dumps(
            {"operation": "gurps-hazard", "command": command.model_dump(mode="json")},
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> Event:
            before = play._load(campaign)
            schedule_id = (
                "exposure:"
                + hashlib.sha256(
                    json.dumps([command.actor_id, command.hazard_id]).encode()
                ).hexdigest()
            )
            schedule = next((h for h in before.resources.hazards if h.id == schedule_id), None)
            if command.kind == "enter" or command.kind == "leave":
                context = self.resolver(play, before, command.actor_id, command.hazard_id)
                entity = next(e for e in before.world.entities if e.id == command.actor_id)
                if context.spec.id != command.hazard_id:
                    raise ValidationError("Scenario hazard binding mismatch")
                if command.kind == "enter" and (
                    entity.location_id != context.spec.scene_id or context.safe
                ):
                    raise ValidationError("Actor is not exposed to this hazard")
                if command.kind == "leave" and not context.safe:
                    raise ValidationError("Exposure has not ended")
                if command.kind == "enter":
                    build = _build(play, before, command.actor_id)
                    ht = _value(build, "attribute:ht")
                    swimming = ht
                    if context.spec.kind == "drowning":
                        assert build.statistics is not None
                        if before.resources.items and (
                            play.engine.rules.combat is None
                            or play.engine.rules.combat.gurps_equipment is None
                        ):
                            raise ValidationError("Swimming load requires exact GURPS equipment")
                        load = encumbrance(
                            context.spec.profile_id,
                            build.statistics.basic_lift,
                            Decimal(
                                play.engine.resources.carried_weight(
                                    before.resources, command.actor_id
                                )
                            )
                            / 1000,
                        )
                        if load is None:
                            raise ValidationError("Unsupported overloaded swimming")
                        swimming = max(
                            1,
                            int(
                                next(
                                    (
                                        v.value
                                        for v in build.sheet.values
                                        if v.target == "skill:swimming"
                                    ),
                                    Decimal(ht - 4),
                                )
                            )
                            - 2 * int(load),
                        )
                    schedule = HazardSchedule(
                        id=schedule_id,
                        actor_id=command.actor_id,
                        spec=context.spec,
                        started=before.resources.game_time,
                        due=before.resources.game_time + context.spec.delay,
                        remaining=context.spec.cycles,
                        ht=ht,
                        will=_value(build, "secondary:will"),
                        swimming=swimming,
                        resistance=context.resistance,
                        no_air_since=before.resources.game_time
                        if context.spec.kind == "suffocation"
                        else None,
                    )
            if schedule is None:
                raise ValidationError("Unknown exposure")
            resources, result = apply_hazard(
                before.resources, command, schedule, rng=play.rng, system=True
            )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            updated = play.checkpoint(updated, before=before)
            play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            return Event(
                input=payload, action="noncombat", outcome=result.model_dump_json(), roll=None
            )

        committed = await play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        state = play._load(committed["state"])
        event = next(e for e in state.resources.events if e.id == "hazard:" + command.id)
        return HazardResult.model_validate_json(event.kind)
