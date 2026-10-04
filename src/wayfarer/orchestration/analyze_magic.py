"""Authenticated secret Information casting and GM reporting transaction."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    ADAPTER,
    AnalyzeMagicCommand,
    AnalyzeMagicResult,
    DirectorAnalyzeMagicResult,
    ObserveAnalyzeMagicSubject,
    ReportAnalyzeMagic,
    identifier,
    secret_result,
)
from wayfarer.engine.simulation.magic.analyze_magic_transitions import apply
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class AnalyzeMagicService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: AnalyzeMagicCommand,
        *,
        principal_id: str,
    ) -> CommandPlan[AnalyzeMagicResult]:
        member = member_for(state, principal_id)
        trusted = isinstance(command, (ObserveAnalyzeMagicSubject, ReportAnalyzeMagic))
        payload = json.dumps(
            {
                "operation": "analyze-magic",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, result = apply(play.rules_context, before, command)
            revision = before.revision + 1
            updated = updated.model_copy(
                update={
                    "revision": revision,
                    "resources": updated.resources.model_copy(update={"revision": revision}),
                }
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="analyze-magic:" + result.outcome)

        async def outcome(campaign: Campaign) -> AnalyzeMagicResult:
            resources = play._load(campaign).resources
            value = AnalyzeMagicResult.model_validate_json(
                next(e.kind for e in resources.events if e.id == identifier("receipt", command.id))
            )
            if (
                member.role == "gm"
                and principal_id in play.engine.reviewer.gm_ids
                and hasattr(command, "cast_id")
            ):
                return DirectorAnalyzeMagicResult(
                    **value.model_dump(), secret=secret_result(resources, command.cast_id)
                )
            return value

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Seats(state), Trusted(play.engine.reviewer.gm_ids))
            if trusted
            else (Controls(member, command.actor_id, state=state),),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> AnalyzeMagicResult:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
