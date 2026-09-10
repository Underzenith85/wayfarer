"""Scenario-bound physical procedures in the existing play transaction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from typing import Literal

from wayfarer.character.statistics import encumbered_move, encumbrance
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.gurps_melee import exertion, injury_turn
from wayfarer.orchestration.medical import _build
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import CheckTrace
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec, require_hazards_settled
from wayfarer.rules.location_types import disabled_locations
from wayfarer.rules.physical import (
    climbing,
    falling_damage,
    hiking_miles,
    jump_distance,
    lift_limit,
    swimming_yards,
)
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.fatigue import FatigueCost, apply_fatigue, exertion_cost, fatigue_value
from wayfarer.simulation.injury import Wound, apply_injury, impaired_movement
from wayfarer.simulation.party import synchronous
from wayfarer.simulation.physical_traits import physical_traits
from wayfarer.simulation.resources import Advance, Command, Record, ResourceEvent


class PhysicalCommand(Command):
    kind: Literal["climb", "jump", "lift", "hike", "swim", "fall"]
    route_id: str


@dataclass(frozen=True)
class PhysicalRoute:
    """Trusted scene geometry; destination must be an authored scene connection."""

    id: str
    scene_id: str
    kind: Literal["climb", "jump", "lift", "hike", "swim", "fall"] = "lift"
    destination_id: str | None = None
    distance: Decimal = Decimal(1)
    seconds: int = 1
    surface: str = "tree"
    jump_kind: Literal["high", "broad"] = "broad"
    run_yards: int = 0
    prepared: bool = True
    lift_kind: str = "two-hand"
    pounds: Decimal = Decimal(1)
    terrain: str = "average"
    bad_weather: bool = False
    hard: bool = True


class PhysicalResult(Record):
    succeeded: bool
    seconds: int
    capacity: str
    checks: tuple[CheckTrace, ...] = ()
    injury: int = 0
    fp_lost: int = 0


RouteResolver = Callable[[PlayService, PlayState, str, str], PhysicalRoute]


class PhysicalService:
    def __init__(self, play: PlayService, resolver: RouteResolver) -> None:
        self.play, self.resolver = play, resolver

    async def execute(
        self, cid: str, command: PhysicalCommand, *, authenticated_actor_id: str
    ) -> PhysicalResult:
        command = PhysicalCommand.model_validate(command)
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Physical actor does not match authenticated actor")
        play = self.play.for_campaign(await self.play.store.read(cid))
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Physical feats require the exact Basic Set profile")
        payload = json.dumps(
            {"operation": "gurps-physical", "command": command.model_dump(mode="json")},
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> Event:
            before = play._load(campaign)
            synchronous(before, command.actor_id)
            actor = next(a for a in before.actors if a.actor_id == command.actor_id)
            if actor.available_at > before.resources.game_time or any(
                e.status == "active" and actor.actor_id in e.turn_order for e in before.encounters
            ):
                raise ValidationError("Physical procedure requires an available noncombat actor")
            require_hazards_settled(
                before.resources.hazards, frozenset({actor.actor_id}), before.resources.game_time
            )
            route = self.resolver(play, before, actor.actor_id, command.route_id)
            entity = next(e for e in before.world.entities if e.id == actor.actor_id)
            if (
                route.id != command.route_id
                or entity.location_id != route.scene_id
                or route.kind != command.kind
            ):
                raise ValidationError("Physical route is not at the actor's location")
            if route.destination_id is not None:
                raise ValidationError(
                    "Cross-scene physical routes require the scene travel integration"
                )
            if (
                not route.distance.is_finite()
                or route.distance < 0
                or route.seconds < 1
                or not route.pounds.is_finite()
                or route.pounds < 0
            ):
                raise ValidationError("Invalid authored route geometry")
            compiled = _build(play, before, actor.actor_id)
            stats = compiled.statistics
            assert stats is not None
            # Equipment uses authoritative millipounds only when an exact binding exists.
            if before.resources.items and (
                play.engine.rules.combat is None or play.engine.rules.combat.gurps_equipment is None
            ):
                raise ValidationError("Physical load requires a GURPS equipment binding")
            equipment = (
                play.engine.rules.combat.gurps_equipment if play.engine.rules.combat else None
            )
            worn_armor = equipment is not None and any(
                item.owner_id == actor.actor_id
                and item.equipped
                and any(
                    entry.definition_id == item.definition_id and entry.armor is not None
                    for entry in equipment.entries
                )
                for item in before.resources.items
            )
            if worn_armor and command.kind in ("climb", "fall"):
                raise ValidationError("Armored falls require the blunt-trauma integration")
            load = encumbrance(
                stats.profile_id,
                stats.basic_lift,
                Decimal(play.engine.resources.carried_weight(before.resources, actor.actor_id))
                / 1000,
            )
            if load is None:
                raise ValidationError("Physical movement exceeds maximum encumbrance")
            hp = next(p for p in before.resources.pools if p.id == "hp:" + actor.actor_id)
            fp = next(p for p in before.resources.pools if p.id == "fp:" + actor.actor_id)
            if (
                hp.injury is None
                or hp.injury.incapacitated
                or hp.injury.stunned
                or fp.fatigue is None
                or fp.fatigue.unconscious
                or fp.fatigue.heart_attack
            ):
                raise ValidationError("Physical activity requires a capable actor")
            disabled = disabled_locations(
                hp.injury.lasting_injuries,
                now=before.resources.game_time,
                full_hp=hp.current >= hp.maximum,
            )
            legs = {"left-leg", "right-leg", "left-foot", "right-foot"}
            arms = {"left-arm", "right-arm", "left-hand", "right-hand"}
            if command.kind != "fall" and (
                {"left-eye", "right-eye"}.issubset(disabled)
                or command.kind in ("climb", "swim")
                and bool(disabled & (legs | arms))
                or command.kind in ("jump", "hike")
                and bool(disabled & legs)
                or command.kind == "lift"
                and bool(disabled & arms)
            ):
                raise ValidationError("This feat requires an unsupported lasting-injury adaptation")
            state = injury_turn(
                play, before, actor.actor_id, command.id, start=True, do_nothing=False
            )
            state, allowed = exertion(play, state, actor.actor_id, command.id)
            hp = next(p for p in state.resources.pools if p.id == "hp:" + actor.actor_id)
            allowed = allowed and hp.injury is not None and not hp.injury.incapacitated
            checks: list[CheckTrace] = []
            seconds, capacity, succeeded, damage, cost = 1, Decimal(0), False, 0, 0

            def skill(key: str, default: int) -> int:
                return int(
                    next(
                        (v.value for v in compiled.sheet.values if v.target == "skill:" + key),
                        Decimal(default),
                    )
                )

            def roll(target: int) -> bool:
                check = success_roll(
                    stats.profile_id,
                    max(1, target),
                    check_modifiers(
                        state.resources, actor.actor_id, "dx" if command.kind == "climb" else "ht"
                    ),
                    rng=play.rng,
                )
                checks.append(check)
                return check.outcome.succeeded

            if allowed:
                move = fatigue_value(
                    fp,
                    impaired_movement(
                        hp, encumbered_move(stats.profile_id, stats.basic_move, load)
                    ),
                )
                if command.kind == "climb":
                    if route.distance != int(route.distance) or not 0 < route.distance <= 300:
                        raise ValidationError(
                            "Climbs require whole feet and at most five minutes between rolls"
                        )
                    modifier, seconds = climbing(route.surface, int(route.distance))
                    if seconds > 300:
                        raise ValidationError("Split long climbs at five-minute checks")
                    succeeded = roll(
                        skill("climbing", stats.dx - 5)
                        + modifier
                        - int(load)
                        - (hp.injury.shock if hp.injury else 0)
                    )
                    capacity = route.distance
                    if not succeeded:
                        dice, adds = falling_damage(stats.hp, route.distance / 3)
                        damage = max(0, sum(play.rng.randbelow(6) + 1 for _ in range(dice)) + adds)
                elif command.kind == "jump":
                    capacity = jump_distance(
                        stats.basic_move,
                        kind=route.jump_kind,
                        run_yards=route.run_yards,
                        jumping=skill("jumping", stats.basic_move * 2),
                        prepared=route.prepared,
                    )
                    seconds = 3 if route.prepared else 1
                    succeeded = route.distance <= capacity
                elif command.kind == "lift":
                    margin = 0
                    lifting = next(
                        (v.value for v in compiled.sheet.values if v.target == "skill:lifting"),
                        None,
                    )
                    if lifting is not None and roll(int(lifting)):
                        margin = max(0, checks[-1].margin)
                    capacity, seconds = lift_limit(
                        fatigue_value(fp, stats.st), route.lift_kind, margin=margin
                    )
                    succeeded = route.pounds <= capacity
                elif command.kind == "hike":
                    if route.seconds != 3600:
                        raise ValidationError("Hiking resolves hourly fatigue intervals")
                    succeeded = roll(skill("hiking", stats.ht - 5))
                    capacity = hiking_miles(
                        move,
                        success=succeeded,
                        terrain=route.terrain,
                        bad_weather=route.bad_weather,
                    )
                    seconds = route.seconds
                    cost = exertion_cost("hiking", seconds=seconds, encumbrance=int(load))
                elif command.kind == "swim":
                    if route.seconds > 60:
                        raise ValidationError(
                            "Swimming procedures are limited to one-minute fatigue checks"
                        )
                    seconds = route.seconds
                    succeeded = roll(skill("swimming", stats.ht - 4) + 3 - 2 * int(load))
                    capacity = (
                        swimming_yards(stats.basic_move, seconds, int(load))
                        if succeeded
                        else Decimal(0)
                    )
                    cost = 0 if succeeded else 1
                    if not succeeded:
                        seconds = 1
                    if seconds == 60 and not roll(
                        stats.ht + physical_traits(state.resources, command.actor_id).fitness
                    ):
                        cost += 1
                else:
                    dice, adds = falling_damage(stats.hp, route.distance, hard=route.hard)
                    damage = max(0, sum(play.rng.randbelow(6) + 1 for _ in range(dice)) + adds)
                    capacity, seconds, succeeded = (
                        route.distance,
                        max(1, ceil((float(route.distance) / 5.35) ** 0.5)),
                        True,
                    )
            resources = state.resources
            internal = hashlib.sha256(command.id.encode()).hexdigest()
            if damage:
                resources, injury = apply_injury(
                    resources,
                    Wound(
                        id="feat-hp:" + internal,
                        actor_id=actor.actor_id,
                        expected_revision=resources.revision,
                        basic_damage=damage,
                        resistance=0,
                        damage_type="cr",
                        injury_source="area",
                    ),
                    ht=stats.ht,
                    rng=play.rng,
                    system=True,
                )
                damage = injury.injury
            if cost:
                resources, fatigue = apply_fatigue(
                    resources,
                    FatigueCost(
                        id="feat-fp:" + internal,
                        actor_id=actor.actor_id,
                        expected_revision=resources.revision,
                        amount=cost,
                    ),
                    ht=stats.ht,
                    rng=play.rng,
                    system=True,
                )
                cost = fatigue.fp_lost
            resources = play.engine.resources.apply(
                resources,
                Advance(
                    id="feat-time:" + internal,
                    actor_id=actor.actor_id,
                    expected_revision=resources.revision,
                    to=resources.game_time + seconds,
                ),
                system=True,
                rng=self.play.rng,
            )
            if command.kind == "swim" and allowed and not succeeded:
                hazard_id = "swim:" + internal
                schedule_id = (
                    "exposure:"
                    + hashlib.sha256(json.dumps([actor.actor_id, hazard_id]).encode()).hexdigest()
                )
                drowning = HazardSchedule(
                    id=schedule_id,
                    actor_id=actor.actor_id,
                    spec=HazardSpec(
                        id=hazard_id,
                        scene_id=route.scene_id,
                        kind="drowning",
                        interval=5,
                        cycles=100000,
                        reference="B354/B436",
                    ),
                    started=resources.game_time,
                    due=resources.game_time + 5,
                    remaining=100000,
                    ht=stats.ht,
                    will=stats.will,
                    swimming=max(1, skill("swimming", stats.ht - 4) - 2 * int(load)),
                    stage="struggling",
                )
                resources = resources.model_copy(
                    update={"hazards": resources.hazards + (drowning,)}
                )
            result = PhysicalResult(
                succeeded=succeeded,
                seconds=seconds,
                capacity=str(capacity),
                checks=tuple(checks),
                injury=damage,
                fp_lost=cost,
            )
            resources = resources.model_copy(
                update={
                    "revision": command.expected_revision + 1,
                    "events": resources.events
                    + (
                        ResourceEvent(
                            id="feat:" + command.id,
                            at=resources.game_time,
                            target_id=actor.actor_id,
                            kind=result.model_dump_json(),
                        ),
                    ),
                }
            )
            updated = state.model_copy(
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
        event = next(e for e in state.resources.events if e.id == "feat:" + command.id)
        return PhysicalResult.model_validate_json(event.kind)
