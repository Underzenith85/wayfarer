"""Trusted single-dose drowsy toxin exposure and settlement on the shared store."""

import json
from collections.abc import Callable

from wayfarer import validation
from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.types.toxin import DeliveryEvidence, ToxinProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.drowsiness import capture
from wayfarer.engine.simulation.health.toxins import ToxinCommand, ToxinResult, apply_toxin
from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.ability_boundary import require_boundary
from wayfarer.orchestration.medical import _build, _value
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload


class DrowsyExposure(Record):
    """Scenario facts, captured privately; never accepted from a player command."""

    profile: ToxinProfile
    evidence: DeliveryEvidence


ExposureResolver = Callable[[PlayService, PlayState, ToxinCommand], DrowsyExposure]


class DrowsyToxinService:
    def __init__(self, play: PlayService, resolver: ExposureResolver) -> None:
        self.play, self.resolver = play, resolver

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: ToxinCommand,
        context: DrowsyExposure | None,
        *,
        principal_id: str,
    ) -> CommandPlan[ToxinResult]:
        payload = json.dumps(
            {
                "operation": "drowsy-toxin",
                "command": command.model_dump(mode="json"),
                "exposure": context.model_dump(mode="json") if context is not None else None,
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            approved = _build(play, before, command.actor_id)
            ht = _value(approved, "attribute:ht")
            if context is None:
                exposure = next(
                    (e for e in before.resources.toxins if e.id == command.exposure_id), None
                )
                profile = exposure.profile if exposure is not None else None
            else:
                profile = context.profile
            if (
                profile is None
                or profile.condition != "drowsy"
                or profile.cycles != 1
                or any((profile.hp_dice, profile.hp_add, profile.fp_dice, profile.fp_add))
                or profile.addictive != "none"
                or profile.depressant
                or not 1 <= profile.condition_seconds < 7200
                or profile.duration_per_margin != 0
            ):
                raise ValidationError(
                    "Drowsy toxin service requires one non-damaging dose lasting under two hours"
                )
            if context is not None and any(
                e.actor_id == command.actor_id
                and e.profile.condition == "drowsy"
                and (e.active or e.condition_until > before.resources.game_time)
                for e in before.resources.toxins
            ):
                raise ValidationError("Overlapping drowsy toxin doses require their own timing")
            resources, result = apply_toxin(
                before.resources,
                command,
                rng=play.rng,
                system=True,
                profile=profile if context is not None else None,
                evidence=context.evidence if context is not None else None,
                ht=ht,
            )
            if context is not None:
                resources = capture(resources, command.exposure_id, command.actor_id)
            updated = play.checkpoint(
                before.model_copy(update={"revision": resources.revision, "resources": resources}),
                before=before,
            )
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> ToxinResult:
            after = play._load(campaign)
            event = next(e for e in after.resources.events if e.id == "toxin:" + command.id)
            return ToxinResult.model_validate_json(event.kind)

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

    async def execute(self, cid: str, command: ToxinCommand, *, principal_id: str) -> ToxinResult:
        command = ToxinCommand.model_validate(command)
        if command.kind not in ("expose", "resolve"):
            raise ValidationError("Drowsy toxin service supports exposure and resolution only")
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Drowsy toxin service requires the exact Basic Set profile")
        state = play._load(campaign)
        context = None
        if command.kind == "expose":
            replay = recorded_command.get()
            prior = None if replay is not None else await play.store.command_input(cid, command.id)
            text = replay.command_input if replay is not None else prior.text if prior else None
            if text is not None:
                captured = validation.mapping(replay_payload(text)).get("exposure")
                context = DrowsyExposure.model_validate(captured)
            elif prior is not None:
                raise ValidationError("Drowsy toxin retry requires its captured source")
            else:
                require_boundary(campaign, None)
                context = DrowsyExposure.model_validate(self.resolver(play, state, command))
        return await submit(
            play,
            cid,
            self.plan(play, state, command, context, principal_id=principal_id),
            principal_id=principal_id,
        )
