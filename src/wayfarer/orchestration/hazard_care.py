"""Scenario-bound B354/B439/B443 lifesaving, diagnosis and antibiotics.

Medical resuscitation remains in MedicalService, including its one-minute task.
These commands cannot prescribe their own bonuses, targets or inventory items.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import CheckTrace, Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.resources import Command, Consume, ResourceEvent
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.medical import _build
from wayfarer.orchestration.play import PlayService


class HazardCareCommand(Command):
    treatment_id: str


@dataclass(frozen=True)
class HazardCare:
    kind: Literal["lifesaving", "diagnosis", "antibiotics"]
    target_id: str
    schedule_id: str
    item_id: str | None = None
    technology_level: int = 0
    known_disease: bool = True
    safe_landing: bool = False


class HazardCareResult(Record):
    succeeded: bool
    check: CheckTrace | None = None
    fp_lost: int = 0
    treatment_bonus: int = 0


CareResolver = Callable[[PlayService, PlayState, str, str], HazardCare]


class HazardCareService:
    def __init__(self, play: PlayService, resolver: CareResolver) -> None:
        self.play, self.resolver = play, resolver

    async def execute(
        self, cid: str, command: HazardCareCommand, *, authenticated_actor_id: str
    ) -> HazardCareResult:
        command = HazardCareCommand.model_validate(command)
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Care actor does not match authenticated actor")
        play = self.play.for_campaign(await self.play.store.read(cid))
        payload = json.dumps({"hazard-care": command.model_dump(mode="json")}, sort_keys=True)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            synchronous(before, command.actor_id)
            context = self.resolver(play, before, command.actor_id, command.treatment_id)
            actors = {a.actor_id: a for a in before.actors}
            locations = {e.id: e.location_id for e in before.world.entities}
            if (
                context.target_id not in actors
                or command.actor_id not in actors
                or locations.get(context.target_id) != locations.get(command.actor_id)
                or actors[command.actor_id].available_at > before.resources.game_time
                or any(
                    e.status == "active" and command.actor_id in e.turn_order
                    for e in before.encounters
                )
            ):
                raise ValidationError("Care requires available, co-located noncombat actors")
            from wayfarer.orchestration.recovery import guard

            guard(before, command.actor_id, context.kind)
            hp = next(p for p in before.resources.pools if p.id == "hp:" + command.actor_id)
            fp = next(p for p in before.resources.pools if p.id == "fp:" + command.actor_id)
            if (
                hp.injury is None
                or hp.injury.incapacitated
                or hp.injury.stunned
                or (fp.fatigue is None or fp.fatigue.unconscious or fp.fatigue.heart_attack)
            ):
                raise ValidationError("Caregiver must be capable")
            schedule = next(
                (
                    h
                    for h in before.resources.hazards
                    if h.id == context.schedule_id and h.actor_id == context.target_id
                ),
                None,
            )
            if schedule is None or not schedule.active:
                raise ValidationError("Unknown active condition")
            build = _build(play, before, command.actor_id)
            stats = build.statistics
            if stats is None or stats.profile_id != "gurps-basic-set-4e-2004":
                raise ValidationError("Care requires exact Basic Set statistics")

            def skill(key: str, default: int) -> int:
                return int(
                    next(
                        (v.value for v in build.sheet.values if v.target == "skill:" + key), default
                    )
                )

            resources = before.resources
            check = None
            cost = bonus = 0
            success = False
            cooldown = 0
            if context.kind == "lifesaving":
                if (
                    schedule.spec.kind != "drowning"
                    or not context.safe_landing
                    or context.target_id == command.actor_id
                    or schedule.stage == "rescued"
                ):
                    raise ValidationError("Lifesaving requires a drowning victim and safe landing")
                require_hazards_settled(
                    resources.hazards, frozenset({command.actor_id}), resources.game_time
                )
                broken_off = "lifesaving-abandoned:" + command.actor_id + ":" + schedule.id
                if any(e.id == broken_off for e in resources.events):
                    raise ConflictError("Critical failure requires breaking off this rescue")
                victim = _build(play, before, context.target_id).statistics
                assert victim is not None
                check = success_roll(
                    stats.profile_id,
                    max(1, skill("swimming", stats.ht - 4) - 5 + stats.st - victim.st),
                    check_modifiers(resources, command.actor_id, "ht"),
                    rng=play.rng,
                )
                success = check.outcome.succeeded
                if success:
                    schedule = schedule.model_copy(
                        update={
                            "stage": "rescued",
                            "due": resources.game_time + 1,
                            "next_check_at": None,
                        }
                    )
                else:
                    cost = 6 if check.outcome is Outcome.CRITICAL_FAILURE else 1
                    cooldown = 60
                    if check.outcome is Outcome.CRITICAL_FAILURE:
                        resources = resources.model_copy(
                            update={
                                "events": resources.events
                                + (
                                    ResourceEvent(
                                        id=broken_off,
                                        at=resources.game_time,
                                        target_id=command.actor_id,
                                        kind="critical-failure",
                                    ),
                                )
                            }
                        )
            elif context.kind == "diagnosis":
                if schedule.spec.kind not in ("poison", "disease") or schedule.symptoms == 0:
                    raise ValidationError("Diagnosis requires visible symptoms")
                attempt = f"diagnosis:{command.actor_id}:{schedule.id}:{schedule.cycle}"
                if any(e.id == attempt for e in resources.events):
                    raise ConflictError("Diagnosis already attempted for these symptoms")
                check = success_roll(
                    stats.profile_id,
                    max(1, skill("diagnosis", stats.iq - 6)),
                    check_modifiers(resources, command.actor_id, "iq"),
                    rng=play.rng,
                )
                success = check.outcome.succeeded and context.known_disease
                if success:
                    schedule = schedule.model_copy(
                        update={
                            "diagnosed_by": tuple(
                                sorted(set(schedule.diagnosed_by) | {command.actor_id})
                            )
                        }
                    )
                resources = resources.model_copy(
                    update={
                        "events": resources.events
                        + (
                            ResourceEvent(
                                id=attempt,
                                at=resources.game_time,
                                target_id=command.actor_id,
                                kind=json.dumps({"succeeded": success}),
                            ),
                        )
                    }
                )
            else:
                if (
                    schedule.spec.kind != "disease"
                    or context.technology_level < 6
                    or command.actor_id not in schedule.diagnosed_by
                    or context.item_id is None
                ):
                    raise ValidationError("Antibiotics require diagnosis, TL6+ and a bound dose")
                item = next((i for i in resources.items if i.id == context.item_id), None)
                if item is None or item.owner_id != command.actor_id:
                    raise ValidationError("Caregiver does not own the bound antibiotic dose")
                if schedule.treatment_bonus:
                    raise ConflictError("Antibiotic treatment is already active")
                resources = play.engine.resources.apply(
                    resources,
                    Consume(
                        id="hazard-drug:" + command.id,
                        actor_id=command.actor_id,
                        expected_revision=resources.revision,
                        item_id=item.id,
                        quantity=1,
                    ),
                    system=True,
                    rng=play.rng,
                )
                bonus = 3 if schedule.spec.bacterial and not schedule.spec.drug_resistant else 0
                schedule = schedule.model_copy(update={"treatment_bonus": bonus})
                success = bonus > 0
            if cost:
                resources, loss = apply_fatigue(
                    resources,
                    FatigueCost(
                        id="lifesaving:" + command.id,
                        actor_id=command.actor_id,
                        expected_revision=resources.revision,
                        amount=cost,
                    ),
                    ht=stats.ht,
                    rng=play.rng,
                    system=True,
                )
                cost = loss.fp_lost
            result = HazardCareResult(
                succeeded=success, check=check, fp_lost=cost, treatment_bonus=bonus
            )
            resources = resources.model_copy(
                update={
                    "revision": command.expected_revision + 1,
                    "hazards": tuple(
                        schedule if h.id == schedule.id else h for h in resources.hazards
                    ),
                    "events": resources.events
                    + (
                        ResourceEvent(
                            id="hazard-care:" + command.id,
                            at=resources.game_time,
                            target_id=command.actor_id,
                            kind=result.model_dump_json(),
                        ),
                    ),
                }
            )
            updated = before.model_copy(
                update={
                    "revision": resources.revision,
                    "resources": resources,
                    "actors": tuple(
                        a.model_copy(update={"available_at": resources.game_time + cooldown})
                        if a.actor_id == command.actor_id and cooldown
                        else a
                        for a in before.actors
                    ),
                }
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="noncombat", outcome=result.model_dump_json())

        committed = await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=play.rng,
        )
        state = play._load(committed["state"])
        event = next(e for e in state.resources.events if e.id == "hazard-care:" + command.id)
        return HazardCareResult.model_validate_json(event.kind)
