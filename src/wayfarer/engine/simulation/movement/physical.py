"""Scenario-bound physical procedure reducers with explicit domain dependencies."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from math import ceil
from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.statistics import (
    CharacterStatistics,
    Encumbrance,
    encumbered_move,
    encumbrance,
)
from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.physical import (
    climbing,
    climbing_default,
    falling_damage,
    falling_injury,
    hiking_miles,
    jump_distance,
    lift_limit,
    swimming_yards,
)
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec, require_hazards_settled
from wayfarer.engine.rules.types.location import disabled_locations
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import migrate, synchronous
from wayfarer.engine.simulation.combat.melee import exertion, injury_turn
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import (
    FatigueCost,
    apply_fatigue,
    exertion_cost,
    fatigue_value,
)
from wayfarer.engine.simulation.health.injury import Wound, apply_injury, impaired_movement
from wayfarer.engine.simulation.health.physical_traits import physical_traits
from wayfarer.engine.simulation.movement.hiking import group_hiking
from wayfarer.engine.simulation.movement.scene_travel import travel_scene
from wayfarer.engine.simulation.resources import (
    Advance,
    Command,
    Pool,
    ResourceEvent,
    decimal_weight,
)
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError
from wayfarer.models import Record


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
    exit_id: str | None = None
    gravity: Decimal = Decimal(1)
    pressure: Decimal = Decimal(1)
    terminal_velocity: int = 60
    controlled: bool = False
    slow_swimming: bool = False
    intentional: bool = True
    rope_length: Decimal | None = None
    hiking_day_seconds: int = 28800
    group_hike: bool = False


class PhysicalResult(Record):
    succeeded: bool
    seconds: int
    capacity: str
    checks: tuple[CheckTrace, ...] = ()
    injury: int = 0
    fp_lost: int = 0
    progress: str = "0"
    completed: bool = False
    elapsed: int = 0
    route_id: str = ""
    route_binding: str = ""


RouteResolver = Callable[[RulesContext, PlayState, str, str], PhysicalRoute]


@dataclass(frozen=True)
class PhysicalContext:
    runtime: RulesContext
    resolver: RouteResolver
    advance_rng: RandomSource


@dataclass(frozen=True)
class PreparedFeat:
    runtime: RulesContext
    state: PlayState
    route: PhysicalRoute
    compiled: ValidatedBuild
    stats: CharacterStatistics
    load: Encumbrance
    hp: Pool
    fp: Pool
    allowed: bool
    move: int
    armor_dr: int
    innate_dr: int
    binding: str
    progress: Decimal
    elapsed: int


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
        rules = runtime.rules.scenes
        if (
            rules is None
            or route.exit_id is None
            or not any(
                scene.location_id == route.scene_id
                and any(
                    value.id == route.exit_id and value.destination_id == route.destination_id
                    for value in scene.exits
                )
                for scene in rules.scenes
            )
        ):
            raise ValidationError("Travel requires a bound authored scene exit")
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
    armor_dr = max(
        (
            entry.armor.dr
            for item in before.resources.items
            if item.owner_id == actor.actor_id
            and item.equipped
            and (item.condition is None or not item.condition.disabled)
            for entry in (equipment.entries if equipment else ())
            if entry.definition_id == item.definition_id
            and entry.armor is not None
            and "torso" in entry.armor.locations
        ),
        default=0,
    )
    from wayfarer.engine.simulation.abilities import damage_resistance

    innate_dr = (
        damage_resistance(before.resources, actor.actor_id, build_revision=compiled.revision)
        if runtime.rules.abilities is not None
        else 0
    )
    load = encumbrance(
        stats.profile_id,
        stats.basic_lift,
        decimal_weight(runtime.resources.carried_weight(before.resources, actor.actor_id)) / 1000,
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
    binding = json.dumps(asdict(route), sort_keys=True, default=str)
    previous = next(
        (
            PhysicalResult.model_validate_json(event.kind)
            for event in reversed(state.resources.events)
            if event.id.startswith("feat:")
            and event.target_id == actor.actor_id
            and json.loads(event.kind).get("route_id") == route.id
        ),
        None,
    )
    if previous is not None and previous.route_binding != binding:
        raise ValidationError("In-progress route geometry changed")
    if previous is not None and previous.completed:
        raise ValidationError("Route already completed; author a new journey ID")
    if route.rope_length is not None and (
        not route.rope_length.is_finite() or route.rope_length < 0
    ):
        raise ValidationError("Invalid safety rope")
    if not 3600 <= route.hiking_day_seconds <= 86400:
        raise ValidationError("Invalid authored hiking day")
    if route.group_hike and command.kind != "hike":
        raise ValidationError("Group pacing is only supported for hiking")
    return PreparedFeat(
        runtime,
        state,
        route,
        compiled,
        stats,
        load,
        hp,
        fp,
        allowed,
        move,
        armor_dr,
        innate_dr,
        binding,
        Decimal(previous.progress) if previous else Decimal(0),
        previous.elapsed if previous else 0,
    )


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
    progress: Decimal = Decimal(0)
    elapsed: int = 0
    state: PlayState | None = None
    group_costs: tuple[tuple[str, int, int], ...] = ()


def _climb(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route, stats, load, hp = feat.route, feat.stats, feat.load, feat.hp
    if route.distance != int(route.distance) or route.distance <= 0:
        raise ValidationError("Climbs require positive whole feet")
    modifier, total_seconds = climbing(route.surface, int(route.distance))
    seconds = min(300, total_seconds - feat.elapsed)
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
    progress = feat.progress
    elapsed = feat.elapsed
    if check.outcome.succeeded:
        progress = min(route.distance, route.distance * (elapsed + seconds) / total_seconds)
    else:
        fall_feet = progress
        if route.rope_length is not None and check.outcome is not Outcome.CRITICAL_FAILURE:
            fall_feet = min(fall_feet, route.rope_length)
        dice, adds = falling_damage(
            stats.hp,
            fall_feet / 3,
            gravity=route.gravity,
            pressure=route.pressure,
            terminal_velocity=route.terminal_velocity,
        )
        damage = max(0, sum(draw_dice(rng, dice)) + adds)
        seconds = 1
        progress, elapsed = Decimal(0), 0
    return FeatOutcome(
        seconds,
        route.distance,
        check.outcome.succeeded,
        damage,
        checks=(check,),
        progress=progress,
        elapsed=elapsed,
    )


def _jump(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route, stats = feat.route, feat.stats
    capacity = jump_distance(
        stats.basic_move,
        kind=route.jump_kind,
        run_yards=route.run_yards,
        jumping=_skill(feat.compiled, "jumping", stats.basic_move * 2),
        prepared=route.prepared,
    )
    succeeded = route.distance <= capacity
    return FeatOutcome(
        3 if route.prepared else 1,
        capacity,
        succeeded,
        progress=route.distance if succeeded else feat.progress,
        elapsed=feat.elapsed,
    )


def _lift(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    lifting = next(
        (v.value for v in feat.compiled.sheet.values if v.target == "skill:lifting"), None
    )
    checks = () if lifting is None else (_roll(feat, command, rng, int(lifting)),)
    margin = max(0, checks[0].margin) if checks and checks[0].outcome.succeeded else 0
    capacity, seconds = lift_limit(
        fatigue_value(feat.fp, feat.stats.st), feat.route.lift_kind, margin=margin
    )
    succeeded = feat.route.pounds <= capacity
    return FeatOutcome(
        seconds,
        capacity,
        succeeded,
        checks=checks,
        progress=feat.route.distance if succeeded else feat.progress,
        elapsed=feat.elapsed,
    )


def _hike(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route, stats = feat.route, feat.stats
    if route.seconds not in (3600, 86400):
        raise ValidationError("Hiking resolves hourly exertion or a full travel day")
    day = feat.state.resources.game_time // 86400
    daily_id = f"hiking-day:{command.actor_id}:{day}"
    daily = next((e for e in feat.state.resources.events if e.id == daily_id), None)
    state = feat.state
    checks: tuple[CheckTrace, ...] = ()
    group_costs: tuple[tuple[str, int, int], ...] = ()
    if route.group_hike:
        state, capacity, checks, group_costs = group_hiking(
            feat.runtime,
            state,
            command.actor_id,
            terrain=route.terrain,
            bad_weather=route.bad_weather,
        )
    else:
        if daily is None:
            check = _roll(feat, command, rng, _skill(feat.compiled, "hiking", stats.ht - 5))
            checks = (check,)
            succeeded = check.outcome.succeeded
            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "events": state.resources.events
                            + (
                                ResourceEvent(
                                    id=daily_id,
                                    at=state.resources.game_time,
                                    target_id=command.actor_id,
                                    kind=json.dumps({"succeeded": succeeded}),
                                ),
                            )
                        }
                    )
                }
            )
        else:
            succeeded = bool(json.loads(daily.kind)["succeeded"])
        capacity = hiking_miles(
            feat.move,
            success=succeeded,
            terrain=route.terrain,
            bad_weather=route.bad_weather,
        )
    progress = min(
        route.distance,
        feat.progress
        + capacity
        * (
            Decimal(1)
            if route.seconds == 86400
            else Decimal(route.seconds) / route.hiking_day_seconds
        ),
    )
    return FeatOutcome(
        route.seconds,
        capacity,
        True,
        cost=(
            exertion_cost("hiking", seconds=route.seconds, encumbrance=int(feat.load))
            if route.seconds == 3600
            else 0
        ),
        checks=checks,
        progress=progress,
        elapsed=feat.elapsed,
        state=state,
        group_costs=group_costs,
    )


def _swim(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route, stats, load = feat.route, feat.stats, feat.load
    if not 1 <= route.seconds <= 60:
        raise ValidationError("Swimming advances at most one fatigue minute")
    hazards = tuple(
        h
        for h in feat.state.resources.hazards
        if h.active and h.actor_id == command.actor_id and h.spec.kind == "drowning"
    )
    if any(h.stage not in ("recovering", "swimming") for h in hazards):
        raise ValidationError("Resolve swimming distress before route progress")
    seconds = min(route.seconds, 60 - feat.elapsed % 60)
    checks: tuple[CheckTrace, ...] = ()
    succeeded = True
    if feat.elapsed % 300 == 0 and not hazards:
        check = _roll(
            feat,
            command,
            rng,
            _skill(feat.compiled, "swimming", stats.ht - 4)
            + (3 if route.intentional and feat.elapsed == 0 else 0)
            - 2 * int(load),
        )
        checks = (check,)
        succeeded = check.outcome.succeeded
    water_move = fatigue_value(feat.fp, impaired_movement(feat.hp, max(1, stats.basic_move // 5)))
    capacity = swimming_yards(water_move * 5, seconds, int(load)) if succeeded else Decimal(0)
    if route.slow_swimming:
        capacity /= 2
    progress = min(route.distance, feat.progress + capacity)
    cost = 0 if succeeded else 1
    if not succeeded:
        seconds = 1
    elif (feat.elapsed + seconds) % (1800 if route.slow_swimming else 60) == 0:
        fatigue = _roll(
            feat,
            command,
            rng,
            max(
                stats.ht + physical_traits(feat.state.resources, command.actor_id).fitness,
                _skill(feat.compiled, "swimming", stats.ht - 4),
            ),
        )
        checks += (fatigue,)
        if not fatigue.outcome.succeeded:
            cost += 1
    return FeatOutcome(
        seconds,
        capacity,
        succeeded,
        cost=cost,
        checks=checks,
        progress=progress,
        elapsed=feat.elapsed,
    )


def _fall(feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource) -> FeatOutcome:
    route = feat.route
    checks: tuple[CheckTrace, ...] = ()
    controlled = False
    if route.controlled:
        check = _roll(feat, command, rng, _skill(feat.compiled, "acrobatics", feat.stats.dx - 6))
        checks = (check,)
        controlled = check.outcome.succeeded
    dice, adds = falling_damage(
        feat.stats.hp,
        route.distance,
        hard=route.hard,
        controlled=controlled,
        gravity=route.gravity,
        pressure=route.pressure,
        terminal_velocity=route.terminal_velocity,
    )
    damage = max(0, sum(draw_dice(rng, dice)) + adds)
    return FeatOutcome(
        max(1, ceil((float(route.distance) / 5.35) ** 0.5)),
        route.distance,
        True,
        damage,
        checks=checks,
        progress=route.distance,
        elapsed=feat.elapsed,
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
    before = migrate(before)
    feat = _prepare(before, command, context)
    outcome = (
        _FEATS[command.kind](feat, command, context.runtime.rng) if feat.allowed else FeatOutcome()
    )
    if not feat.allowed:
        outcome = FeatOutcome(progress=feat.progress, elapsed=feat.elapsed)
    return _finish(feat, command, context, outcome)


def _finish(
    feat: PreparedFeat, command: PhysicalCommand, context: PhysicalContext, outcome: FeatOutcome
) -> tuple[PlayState, PhysicalResult]:
    runtime = context.runtime
    state = outcome.state or feat.state
    route, stats, load, allowed = feat.route, feat.stats, feat.load, feat.allowed
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
    if command.kind in ("climb", "fall"):
        damage = falling_injury(damage, feat.armor_dr, feat.innate_dr)
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
    for member, member_ht, member_load in outcome.group_costs:
        if member != command.actor_id and seconds == 3600:
            resources, _ = apply_fatigue(
                resources,
                FatigueCost(
                    id="group-hike-fp:" + internal + ":" + member,
                    actor_id=member,
                    expected_revision=resources.revision,
                    amount=exertion_cost("hiking", seconds=seconds, encumbrance=member_load),
                ),
                ht=member_ht,
                rng=runtime.rng,
                system=True,
            )
    state = injury_turn(
        runtime,
        state.model_copy(update={"resources": resources}),
        command.actor_id,
        command.id,
        start=False,
        do_nothing=False,
    )
    resources = state.resources
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
        progress=str(outcome.progress),
        completed=succeeded and outcome.progress >= route.distance,
        elapsed=0 if command.kind == "climb" and not succeeded else outcome.elapsed + seconds,
        route_id=route.id,
        route_binding=feat.binding,
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
    if result.completed and route.destination_id is not None:
        assert route.exit_id is not None
        travelers = tuple(member for member, _, _ in outcome.group_costs) or (command.actor_id,)
        for traveler in travelers:
            updated = travel_scene(
                updated,
                actor_id=traveler,
                command_id="feat-travel:" + internal + ":" + traveler,
                exit_id=route.exit_id,
                runtime=runtime,
                group_travel=bool(outcome.group_costs),
                revision=resources.revision,
            )
    if updated.party.groups:
        updated = updated.model_copy(
            update={
                "party": updated.party.model_copy(
                    update={
                        "groups": tuple(
                            group.model_copy(update={"ready_through": resources.game_time})
                            for group in updated.party.groups
                        )
                    }
                )
            }
        )
    return updated, result
