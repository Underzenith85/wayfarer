"""Approved-character food and water needs through the shared command pipeline."""

import json
from collections.abc import Callable
from dataclasses import dataclass

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.health.survival import (
    BeginSurvival,
    SettleSurvival,
    SurvivalContext,
    SurvivalResult,
    begin_survival,
    settle_survival,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical import _build, _value
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, submit
from wayfarer.orchestration.play import PlayService


@dataclass(frozen=True)
class SurvivalEnvironment:
    meal_item_ids: tuple[str, ...] = ()
    water_item_ids: tuple[str, ...] = ()


EnvironmentResolver = Callable[[PlayService, PlayState, str], SurvivalEnvironment]


class SurvivalService:
    """Supplies are trusted scenario facts; purchases come from current approval."""

    def __init__(self, play: PlayService, environment: EnvironmentResolver) -> None:
        self.play, self.environment = play, environment

    def plan(
        self,
        play: PlayService,
        command: BeginSurvival | SettleSurvival,
        member: CampaignMember,
    ) -> CommandPlan[SurvivalResult]:
        payload = json.dumps(
            {"operation": "gurps-survival", "command": command.model_dump(mode="json")},
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            build = _build(play, before, command.actor_id)
            env = self.environment(play, before, command.actor_id)
            physiology = physiology_traits(build, play.engine.reviewer.compiler.definitions)
            context = SurvivalContext(
                profile_id="gurps-basic-set-4e-2004",
                ht=_value(build, "attribute:ht"),
                will=_value(build, "secondary:will"),
                meal_item_ids=env.meal_item_ids,
                water_item_ids=env.water_item_ids,
                does_not_sleep="sleep" not in physiology.survival_requirements(),
                physiology=physiology,
            )
            if isinstance(command, BeginSurvival):
                resources, result = begin_survival(before.resources, command, context, system=True)
            else:
                resources, result = settle_survival(
                    before.resources, command, context, rng=play.rng, system=True
                )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> SurvivalResult:
            state = play._load(campaign)
            event = next(
                event for event in state.resources.events if event.id == "survival:" + command.id
            )
            return SurvivalResult.model_validate_json(event.kind)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(Controls(member, command.actor_id),),
            rng=play.rng,
        )

    async def execute(
        self, cid: str, command: BeginSurvival | SettleSurvival, *, principal_id: str
    ) -> SurvivalResult:
        command = type(command).model_validate(command)
        play = self.play.for_campaign(await self.play.store.read(cid))
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Survival requires the exact Basic Set profile")
        member = member_for(play._load(await play.store.read(cid)), principal_id)
        return await submit(play, cid, self.plan(play, command, member), principal_id=principal_id)
