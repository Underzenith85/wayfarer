"""Authoritative sense and trait-specific checks using the existing roll/CAS services.

A scenario binds a finite trigger resolver. Player input identifies a trigger;
only the server selects its sense, conditions and approved skill. Check details
stay in the private resource event, while the public result exposes success.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.traits.physical import Sense
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    definition_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.physical_traits import physical_traits
from wayfarer.engine.simulation.resources import Command, ResourceEvent
from wayfarer.errors import ValidationError
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


class PhysicalCheckCommand(Command):
    trigger_id: str


@dataclass(frozen=True)
class PhysicalCheck:
    kind: Literal["sense", "wake", "fast-draw", "torture", "off-hand", "ht"]
    sense: Sense = "vision"
    darkness: int = 0
    modifier: int = 0
    skill_id: str | None = None


Resolver = Callable[[PlayService, PlayState, PhysicalCheckCommand], PhysicalCheck]


class PhysicalCheckService:
    def __init__(self, play: PlayService, resolve: Resolver) -> None:
        self.play, self.resolve = play, resolve

    async def execute(self, cid: str, command: PhysicalCheckCommand, *, gm_id: str) -> bool:
        command = PhysicalCheckCommand.model_validate(command)
        if not command.trigger_id:
            raise ValidationError("Physical checks require a stable authored trigger")
        play = self.play.for_campaign(await self.play.store.read(cid))
        payload = json.dumps(
            {"physical-check": command.model_dump(mode="json"), "gm": gm_id}, sort_keys=True
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            state = play._load(campaign)
            if gm_id not in play.engine.reviewer.gm_ids or not any(
                m.principal_id == gm_id and m.role == "gm" for m in state.members
            ):
                raise ValidationError("Physical checks require director authority")
            compiled = build(play.rules_context, state, command.actor_id)
            stats = compiled.statistics
            if stats is None or stats.profile_id != "gurps-basic-set-4e-2004":
                raise ValidationError("Physical trait checks require the exact Basic Set profile")
            event_id = "physical-check:" + json.dumps([command.actor_id, command.trigger_id])
            if any(e.id == event_id for e in state.resources.events):
                raise ValidationError("Physical trigger already resolved")
            spec = self.resolve(play, state, command)
            traits = physical_traits(state.resources, command.actor_id)

            require_hazard_capacity(
                state.resources, command.actor_id, spec.sense if spec.kind == "sense" else spec.kind
            )
            if spec.kind == "sense":
                if spec.sense == "vision" and spec.darkness == -10:
                    raise ValidationError("Total darkness prevents ordinary vision")
                target = stats.per + traits.sense_bonus(spec.sense, spec.darkness)
            elif spec.kind == "wake":
                target = stats.iq + 6 * int(traits.combat_reflexes)
            elif spec.kind == "torture":
                target = stats.will + 3 * int(traits.high_pain_threshold)
            elif spec.kind == "ht":
                target = stats.ht + traits.fitness
            elif spec.kind in ("fast-draw", "off-hand"):
                if spec.kind == "fast-draw" and (
                    spec.skill_id is None or not spec.skill_id.startswith("skill:fast-draw")
                ):
                    raise ValidationError("Fast-Draw requires a pinned Fast-Draw skill")
                target = stats.dx
                if spec.skill_id is not None:
                    learned = next(
                        (v for v in compiled.sheet.values if v.target == spec.skill_id), None
                    )
                    if learned is None or learned.value != int(learned.value):
                        raise ValidationError("Physical check requires an approved skill")
                    target = int(learned.value)
                target += (
                    int(traits.combat_reflexes)
                    if spec.kind == "fast-draw"
                    else 0
                    if traits.ambidexterity
                    else -4
                )
            else:
                raise ValidationError("Unsupported physical check")
            attribute = {
                "sense": "per",
                "wake": "iq",
                "torture": "will",
                "ht": "ht",
                "fast-draw": "dx",
                "off-hand": "dx",
            }[spec.kind]
            modifiers = (
                definition_modifiers(
                    state.resources,
                    command.actor_id,
                    spec.skill_id,
                    play.engine.reviewer.compiler.definitions,
                )
                if spec.skill_id is not None
                else check_modifiers(
                    state.resources,
                    command.actor_id,
                    attribute,
                    defensive=spec.kind in ("wake", "torture"),
                )
            )
            trace = success_roll(stats.profile_id, target + spec.modifier, modifiers, rng=play.rng)
            resources = state.resources.model_copy(
                update={
                    "revision": state.revision + 1,
                    "events": state.resources.events
                    + (
                        ResourceEvent(
                            id=event_id,
                            at=state.resources.game_time,
                            target_id=command.actor_id,
                            kind=json.dumps(asdict(trace)),
                        ),
                    ),
                }
            )
            updated = play.checkpoint(
                state.model_copy(update={"revision": resources.revision, "resources": resources}),
                before=state,
            )
            play.commit(campaign, updated)
            return CommandReceipt(action="noncombat", outcome=json.dumps(trace.outcome.succeeded))

        committed = await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=gm_id,
            rng=play.rng,
        )
        # The committed event is returned on retries; the resolver is never rerun.
        stored = play._load(committed["state"])
        event_id = "physical-check:" + json.dumps([command.actor_id, command.trigger_id])
        event = next(e for e in stored.resources.events if e.id == event_id)
        return json.loads(event.kind)["outcome"] in ("success", "critical-success")
