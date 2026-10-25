"""Approved-character alcohol checks through the shared command pipeline."""

import json
from collections.abc import Callable

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.toxins import (
    DrinkCommand,
    IntoxicationResult,
    apply_drinking,
)
from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.ability_boundary import require_boundary
from wayfarer.orchestration.condition_environments import recorded_environment
from wayfarer.orchestration.medical import _build, _value
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit
from wayfarer.orchestration.play import PlayService


class DrinkingEnvironment(Record):
    recently_ate: bool = False
    empty_stomach: bool = False


EnvironmentResolver = Callable[[PlayService, PlayState, str], DrinkingEnvironment]


class DrinkingService:
    """Meals are trusted scenario facts; purchased tolerance comes from current approval."""

    def __init__(self, play: PlayService, environment: EnvironmentResolver) -> None:
        self.play, self.environment = play, environment

    def plan(
        self,
        play: PlayService,
        command: DrinkCommand,
        *,
        environment: DrinkingEnvironment | None = None,
    ) -> CommandPlan[IntoxicationResult]:
        source: dict[str, object] = {}
        if environment is not None:
            source["drinking_environment"] = environment.model_dump(mode="json")
        payload = json.dumps(
            {"operation": "gurps-drinking", "command": command.model_dump(mode="json"), **source},
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            build = _build(play, before, command.actor_id)
            effects = mundane_trait_effects(build, play.engine.reviewer.compiler.definitions)
            identifiers = {effect.definition_id for effect in effects}
            tolerance = 2 * int("trait:advantage:alcohol-tolerance" in identifiers) - 2 * int(
                "trait:disadvantage:alcohol-intolerance" in identifiers
            )
            env = environment or self.environment(play, before, command.actor_id)
            carousing = next(
                (
                    int(value.value)
                    for value in build.sheet.values
                    if value.target == "skill:carousing"
                ),
                0,
            )
            resources, result = apply_drinking(
                before.resources,
                command,
                rng=play.rng,
                system=True,
                st=_value(build, "attribute:st"),
                ht=_value(build, "attribute:ht"),
                carousing=carousing,
                recently_ate=env.recently_ate,
                empty_stomach=env.empty_stomach,
                tolerance=tolerance,
            )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> IntoxicationResult:
            state = play._load(campaign)
            event = next(
                event
                for event in state.resources.events
                if event.id == "intoxication:" + command.id
            )
            return IntoxicationResult.model_validate_json(event.kind)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(ActsAs(command.actor_id),),
            rng=play.rng,
        )

    async def execute(
        self, cid: str, command: DrinkCommand, *, principal_id: str
    ) -> IntoxicationResult:
        command = DrinkCommand.model_validate(command)
        play = self.play.for_campaign(await self.play.store.read(cid))
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Drinking requires the exact Basic Set profile")
        ActsAs(command.actor_id)(principal_id)
        saved, raw = await recorded_environment(play, cid, command.id, "drinking_environment")
        if not saved:
            require_boundary(await play.store.read(cid), None)
        environment = (
            DrinkingEnvironment.model_validate_json(json.dumps(raw))
            if raw is not None
            else None
            if saved
            else self.environment(play, play._load(await play.store.read(cid)), command.actor_id)
        )
        return await submit(
            play, cid, self.plan(play, command, environment=environment), principal_id=principal_id
        )
