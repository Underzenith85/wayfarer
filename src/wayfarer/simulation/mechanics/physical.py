"""Scenario-bound physical procedure reducers with explicit domain dependencies."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from typing import Literal

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.character.statistics import (
    CharacterStatistics,
    Encumbrance,
    encumbered_move,
    encumbrance,
)
from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.rules.checks import CheckTrace, RandomSource, draw_dice
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec, require_hazards_settled
from wayfarer.rules.location_types import disabled_locations
from wayfarer.rules.physical import (
    climbing,
    climbing_default,
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
from wayfarer.simulation.mechanics.gurps_melee import exertion, injury_turn
from wayfarer.simulation.party import synchronous
from wayfarer.simulation.physical_traits import physical_traits
from wayfarer.simulation.resources import Advance, Command, Pool, ResourceEvent
from wayfarer.simulation.rules_context import RulesContext


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


RouteResolver = Callable[[RulesContext, PlayState, str, str], PhysicalRoute]


@dataclass(frozen=True)
class PhysicalContext:
    runtime: RulesContext
    resolver: RouteResolver
    advance_rng: RandomSource


@dataclass(frozen=True)
class PreparedFeat:
    state: PlayState
    route: PhysicalRoute
    compiled: ValidatedBuild
    stats: CharacterStatistics
    load: Encumbrance
    hp: Pool
    fp: Pool
    allowed: bool
    move: int


def _prepare(before: PlayState, command: PhysicalCommand, context: PhysicalContext) -> PreparedFeat:
    runtime = context.runtime
    synchronous(before, command.actor_id)
    actor = next(a for a in before.actors if a.actor_id == command.actor_id)
    if actor.available_at > before.resources.game_time or any(
        e.status == "active" and actor.actor_id in e.turn_order for e in before.encounters
    ):
        raise ValidationError("Physical procedure requires an available noncombat actor")
    require_hazards_settled(
        before.resources.hazards, frozenset({actor.actor_id}), before.resources.game_time
    )
    route = context.resolver(runtime, before, actor.actor_id, command.route_id)
    entity = next(e for e in before.world.entities if e.id == actor.actor_id)
    if (
        route.id != command.route_id
        or entity.location_id != route.scene_id
        or route.kind != command.kind
    ):
        raise ValidationError("Physical route is not at the actor's location")
    if route.destination_id is not None:
        raise ValidationError("Cross-scene physical routes require the scene travel integration")
    if (
        not route.distance.is_finite()
        or route.distance < 0
        or route.seconds < 1
        or not route.pounds.is_finite()
        or route.pounds < 0
    ):
        raise ValidationError("Invalid authored route geometry")
    compiled = runtime.approved_build(before, actor.actor_id)
    stats = compiled.statistics
    assert stats is not None
    # Equipment uses authoritative millipounds only when an exact binding exists.
    if before.resources.items and (
        runtime.rules.combat is None or runtime.rules.combat.gurps_equipment is None
    ):
        raise ValidationError("Physical load requires a GURPS equipment binding")
    equipment = runtime.rules.combat.gurps_equipment if runtime.rules.combat else None
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
        Decimal(runtime.resources.carried_weight(before.resources, actor.actor_id)) / 1000,
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
    state = injury_turn(runtime, before, actor.actor_id, command.id, start=True, do_nothing=False)
    state, allowed = exertion(runtime, state, actor.actor_id, command.id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor.actor_id)
    allowed = allowed and hp.injury is not None and not hp.injury.incapacitated
    move = (
        fatigue_value(
            fp, impaired_movement(hp, encumbered_move(stats.profile_id, stats.basic_move, load))
        )
        if allowed
        else 0
    )
    return PreparedFeat(state, route, compiled, stats, load, hp, fp, allowed, move)


def _skill(compiled: ValidatedBuild, key: str, default: int) -> int:
    return int(
        next(
            (v.value for v in compiled.sheet.values if v.target == "skill:" + key), Decimal(default)
        )
    )


def _roll(
    feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource, target: int
) -> CheckTrace:
    return success_roll(
        feat.stats.profile_id,
        max(1, target),
        check_modifiers(
            feat.state.resources, command.actor_id, "dx" if command.kind == "climb" else "ht"
        ),
        rng=rng,
    )


@dataclass(frozen=True)
class FeatOutcome:
    seconds: int = 1
    capacity: Decimal = Decimal(0)
    succeeded: bool = False
    damage: int = 0
    cost: int = 0
    checks: tuple[CheckTrace, ...] = ()


def _climb(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route, stats, load, hp = feat.route, feat.stats, feat.load, feat.hp
    if route.distance != int(route.distance) or not 0 < route.distance <= 300:
        raise ValidationError("Climbs require whole feet and at most five minutes between rolls")
    modifier, seconds = climbing(route.surface, int(route.distance))
    if seconds > 300:
        raise ValidationError("Split long climbs at five-minute checks")
    check = _roll(
        feat,
        command,
        rng,
        _skill(feat.compiled, "climbing", climbing_default(stats.dx))
        + modifier
        - int(load)
        - (hp.injury.shock if hp.injury else 0),
    )
    damage = 0
    if not check.outcome.succeeded:
        dice, adds = falling_damage(stats.hp, route.distance / 3)
        damage = max(0, sum(draw_dice(rng, dice)) + adds)
    return FeatOutcome(seconds, route.distance, check.outcome.succeeded, damage, checks=(check,))


def _jump(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route, stats = feat.route, feat.stats
    capacity = jump_distance(
        stats.basic_move,
        kind=route.jump_kind,
        run_yards=route.run_yards,
        jumping=_skill(feat.compiled, "jumping", stats.basic_move * 2),
        prepared=route.prepared,
    )
    return FeatOutcome(3 if route.prepared else 1, capacity, route.distance <= capacity)


def _lift(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    lifting = next(
        (v.value for v in feat.compiled.sheet.values if v.target == "skill:lifting"), None
    )
    checks = () if lifting is None else (_roll(feat, command, rng, int(lifting)),)
    margin = max(0, checks[0].margin) if checks and checks[0].outcome.succeeded else 0
    capacity, seconds = lift_limit(
        fatigue_value(feat.fp, feat.stats.st), feat.route.lift_kind, margin=margin
    )
    return FeatOutcome(seconds, capacity, feat.route.pounds <= capacity, checks=checks)


def _hike(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route, stats = feat.route, feat.stats
    if route.seconds != 3600:
        raise ValidationError("Hiking resolves hourly fatigue intervals")
    check = _roll(feat, command, rng, _skill(feat.compiled, "hiking", stats.ht - 5))
    capacity = hiking_miles(
        feat.move,
        success=check.outcome.succeeded,
        terrain=route.terrain,
        bad_weather=route.bad_weather,
    )
    return FeatOutcome(
        route.seconds,
        capacity,
        check.outcome.succeeded,
        cost=exertion_cost("hiking", seconds=route.seconds, encumbrance=int(feat.load)),
        checks=(check,),
    )


def _swim(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route, stats, load = feat.route, feat.stats, feat.load
    if route.seconds > 60:
        raise ValidationError("Swimming procedures are limited to one-minute fatigue checks")
    check = _roll(
        feat, command, rng, _skill(feat.compiled, "swimming", stats.ht - 4) + 3 - 2 * int(load)
    )
    succeeded = check.outcome.succeeded
    seconds = route.seconds if succeeded else 1
    capacity = swimming_yards(stats.basic_move, seconds, int(load)) if succeeded else Decimal(0)
    cost = 0 if succeeded else 1
    checks: tuple[CheckTrace, ...] = (check,)
    if seconds == 60:
        fatigue = _roll(
            feat,
            command,
            rng,
            stats.ht + physical_traits(feat.state.resources, command.actor_id).fitness,
        )
        checks += (fatigue,)
        if not fatigue.outcome.succeeded:
            cost += 1
    return FeatOutcome(seconds, capacity, succeeded, cost=cost, checks=checks)


def _fall(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route = feat.route
    dice, adds = falling_damage(feat.stats.hp, route.distance, hard=route.hard)
    damage = max(0, sum(draw_dice(rng, dice)) + adds)
    return FeatOutcome(
        max(1, ceil((float(route.distance) / 5.35) ** 0.5)), route.distance, True, damage
    )


_FEATS: dict[str, Callable[[PreparedFeat, PhysicalCommand, RandomSource], FeatOutcome]] = {
    "climb": _climb,
    "jump": _jump,
    "lift": _lift,
    "hike": _hike,
    "swim": _swim,
    "fall": _fall,
}


def reduce_physical(
    before: PlayState, command: PhysicalCommand, context: PhysicalContext
) -> tuple[PlayState, PhysicalResult]:
    feat = _prepare(before, command, context)
    outcome = (
        _FEATS[command.kind](feat, command, context.runtime.rng) if feat.allowed else FeatOutcome()
    )
    return _finish(feat, command, context, outcome)


def _finish(
    feat: PreparedFeat, command: PhysicalCommand, context: PhysicalContext, outcome: FeatOutcome
) -> tuple[PlayState, PhysicalResult]:
    runtime = context.runtime
    state, route, stats, load, allowed = feat.state, feat.route, feat.stats, feat.load, feat.allowed
    seconds, capacity, succeeded, damage, cost, checks = (
        outcome.seconds,
        outcome.capacity,
        outcome.succeeded,
        outcome.damage,
        outcome.cost,
        outcome.checks,
    )
    resources = state.resources
    internal = hashlib.sha256(command.id.encode()).hexdigest()
    if damage:
        resources, injury = apply_injury(
            resources,
            Wound(
                id="feat-hp:" + internal,
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                basic_damage=damage,
                resistance=0,
                damage_type="cr",
                injury_source="area",
            ),
            ht=stats.ht,
            rng=runtime.rng,
            system=True,
        )
        damage = injury.injury
    if cost:
        resources, fatigue = apply_fatigue(
            resources,
            FatigueCost(
                id="feat-fp:" + internal,
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                amount=cost,
            ),
            ht=stats.ht,
            rng=runtime.rng,
            system=True,
        )
        cost = fatigue.fp_lost
    resources = runtime.resources.apply(
        resources,
        Advance(
            id="feat-time:" + internal,
            actor_id=command.actor_id,
            expected_revision=resources.revision,
            to=resources.game_time + seconds,
        ),
        system=True,
        rng=context.advance_rng,
    )
    if command.kind == "swim" and allowed and not succeeded:
        hazard_id = "swim:" + internal
        schedule_id = (
            "exposure:"
            + hashlib.sha256(json.dumps([command.actor_id, hazard_id]).encode()).hexdigest()
        )
        drowning = HazardSchedule(
            id=schedule_id,
            actor_id=command.actor_id,
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
            swimming=max(1, _skill(feat.compiled, "swimming", stats.ht - 4) - 2 * int(load)),
            stage="struggling",
        )
        resources = resources.model_copy(update={"hazards": resources.hazards + (drowning,)})
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
                    target_id=command.actor_id,
                    kind=result.model_dump_json(),
                ),
            ),
        }
    )
    updated = state.model_copy(update={"revision": resources.revision, "resources": resources})
    return updated, result
