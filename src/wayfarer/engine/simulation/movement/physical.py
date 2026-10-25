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
from wayfarer.engine.rules.checks import CheckTrace, Modifier, Outcome, RandomSource, draw_dice
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
from wayfarer.engine.simulation.abilities import damage_resistance
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import exertion, injury_turn
from wayfarer.engine.simulation.campaign.default_knowledge_fallbacks import (
    optional_skill as optional_default_skill,
)
from wayfarer.engine.simulation.campaign.default_knowledge_fallbacks import (
    require_skill as require_default_skill,
)
from wayfarer.engine.simulation.campaign.default_knowledge_policy import (
    active as default_knowledge_active,
)
from wayfarer.engine.simulation.campaign.party import group_for, migrate, synchronous
from wayfarer.engine.simulation.equipment.corrosion_state import projected_profile
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import (
    FatigueCost,
    apply_fatigue,
    exertion_cost,
    fatigue_value,
)
from wayfarer.engine.simulation.health.injury import Wound, apply_injury, impaired_movement
from wayfarer.engine.simulation.health.physical_traits import physical_traits
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.enchanting import busy_actor_ids as enchanting_actor_ids
from wayfarer.engine.simulation.magic.enchanting_calendar import require_enchanting_free
from wayfarer.engine.simulation.magic.rooted_feet_state import require_locomotion
from wayfarer.engine.simulation.movement.hiking import group_hiking, require_hiking_defaults
from wayfarer.engine.simulation.movement.scene_travel import travel_scene
from wayfarer.engine.simulation.resources import (
    Advance,
    Command,
    Pool,
    ResourceEvent,
    decimal_weight,
)
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.skills.power_blow import power_blow_lift_strength
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.errors import ValidationError
from wayfarer.models import Record

ENCHANTING_REST_KINDS = frozenset({"lift", "jump", "swim"})
PHYSICAL_SKILL_KINDS = frozenset({"climb", "lift", "hike", "swim"})


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
    use_lifting: bool = True
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
    enchanting_rest: bool = False
    skill_minimum: bool = False


@dataclass(frozen=True)
class PhysicalSkillInputs:
    compiled: ValidatedBuild
    stats: CharacterStatistics
    route: PhysicalRoute
    load: Encumbrance
    hp: Pool
    elapsed: int
    runtime: RulesContext
    state: PlayState
    actor_id: str


@dataclass(frozen=True)
class PreparedFeat(PhysicalSkillInputs):
    fp: Pool
    allowed: bool
    move: int
    armor_dr: int
    innate_dr: int
    binding: str
    progress: Decimal


def _require_feat_locomotion(before: PlayState, command: PhysicalCommand) -> None:
    if command.kind in ("climb", "jump", "hike", "swim"):
        require_locomotion(before.resources, command.actor_id)


def _require_group_locomotion(before: PlayState, actor_id: str, route: PhysicalRoute) -> None:
    if route.group_hike:
        for member in group_for(before, actor_id).actor_ids:
            require_locomotion(before.resources, member)


def _require_physical_interval(
    before: PlayState,
    command: PhysicalCommand,
    context: PhysicalContext,
    route: PhysicalRoute,
    strength: int,
    elapsed: int,
) -> None:
    if not context.enchanting_rest or command.kind not in ENCHANTING_REST_KINDS:
        return
    # B353 fixes lifting time independently of a possible Lifting roll. B352
    # jumping takes two preparation maneuvers and one Move, or one unprepared.
    # Admit the whole interval before injury/exertion or skill dice.
    if command.kind == "swim":
        seconds = _require_swim_interval(before, command.actor_id, route, elapsed)
    else:
        seconds = (
            lift_limit(strength, route.lift_kind)[1]
            if command.kind == "lift"
            else 3
            if route.prepared
            else 1
        )
    require_enchanting_free(
        before.resources,
        command.actor_id,
        through=before.resources.game_time + seconds,
    )
    if command.actor_id in enchanting_actor_ids(before.resources.enchantment_projects):
        require_idle_concentration(before.resources, command.actor_id)


def _require_swim_interval(
    before: PlayState, actor_id: str, route: PhysicalRoute, elapsed: int
) -> int:
    if not 1 <= route.seconds <= 60:
        raise ValidationError("Swimming advances at most one fatigue minute")
    if actor_id in enchanting_actor_ids(before.resources.enchantment_projects) and (
        route.group_hike or route.destination_id is not None or route.exit_id is not None
    ):
        raise ValidationError(
            "Nighttime swimming requires an individual route without scene travel"
        )
    if any(
        h.active
        and h.actor_id == actor_id
        and h.spec.kind == "drowning"
        and h.stage not in ("recovering", "swimming")
        for h in before.resources.hazards
    ):
        raise ValidationError("Resolve swimming distress before route progress")
    seconds = min(route.seconds, 60 - elapsed % 60)
    # Reaching a due boundary is legal; passing it must resolve the hazard first.
    require_hazards_settled(
        before.resources.hazards,
        frozenset({actor_id}),
        before.resources.game_time + seconds - 1,
    )
    return seconds


def route_binding(route: PhysicalRoute) -> str:
    """The default assistance choice retains historical captured-route bytes."""
    value = asdict(route)
    if route.use_lifting is True:
        value.pop("use_lifting")
    return json.dumps(value, sort_keys=True, default=str)


def _route_progress(
    before: PlayState, actor_id: str, route: PhysicalRoute
) -> tuple[str, Decimal, int]:
    binding = route_binding(route)
    previous = next(
        (
            PhysicalResult.model_validate_json(event.kind)
            for event in reversed(before.resources.events)
            if event.id.startswith("feat:")
            and event.target_id == actor_id
            and json.loads(event.kind).get("route_id") == route.id
        ),
        None,
    )
    if previous is not None and previous.route_binding != binding:
        raise ValidationError("In-progress route geometry changed")
    if previous is not None and previous.completed:
        raise ValidationError("Route already completed; author a new journey ID")
    return (
        binding,
        Decimal(previous.progress) if previous else Decimal(0),
        previous.elapsed if previous else 0,
    )


def require_route_actor(
    before: PlayState, command: PhysicalCommand, *, enchanting_rest: bool = False
) -> None:
    """Pure actor admission before resolving or capturing scenario geometry."""
    _require_feat_locomotion(before, command)
    if command.kind != "fall":
        require_innate_actor_action(before, command.actor_id)
    synchronous(
        before,
        command.actor_id,
        enchanting_rest=enchanting_rest and command.kind in ENCHANTING_REST_KINDS,
    )
    actor = next(a for a in before.actors if a.actor_id == command.actor_id)
    if actor.available_at > before.resources.game_time or any(
        e.status == "active" and actor.actor_id in e.turn_order for e in before.encounters
    ):
        raise ValidationError("Physical procedure requires an available noncombat actor")
    require_hazards_settled(
        before.resources.hazards, frozenset({actor.actor_id}), before.resources.game_time
    )


def require_default_route_actor(
    runtime: RulesContext, state: PlayState, command: PhysicalCommand
) -> None:
    """Known required skill attempts can be refused before querying route facts."""
    if not default_knowledge_active(runtime, state, command.actor_id):
        return
    keys = {"climb": "climbing", "hike": "hiking", "swim": "swimming"}
    if command.kind not in keys:
        return
    if command.kind == "hike" and any(
        e.id == f"hiking-day:{command.actor_id}:{state.resources.game_time // 86400}"
        for e in state.resources.events
    ):
        return
    if command.kind == "swim":
        if any(
            h.active and h.actor_id == command.actor_id and h.spec.kind == "drowning"
            for h in state.resources.hazards
        ):
            return
        previous = next(
            (
                PhysicalResult.model_validate_json(e.kind)
                for e in reversed(state.resources.events)
                if e.id.startswith("feat:")
                and e.target_id == command.actor_id
                and json.loads(e.kind).get("route_id") == command.route_id
            ),
            None,
        )
        if previous is not None and previous.elapsed % 300:
            return
    build = runtime.approved_build(state, command.actor_id)
    assert build.statistics is not None
    legacy = {
        "climb": climbing_default(build.statistics.dx),
        "hike": build.statistics.ht - 5,
        "swim": build.statistics.ht - 4,
    }
    require_default_skill(
        runtime, state, command.actor_id, build, keys[command.kind], legacy[command.kind]
    )


def _prepare(before: PlayState, command: PhysicalCommand, context: PhysicalContext) -> PreparedFeat:
    runtime = context.runtime
    require_route_actor(before, command, enchanting_rest=context.enchanting_rest)
    require_default_route_actor(runtime, before, command)
    actor = next(a for a in before.actors if a.actor_id == command.actor_id)
    route = context.resolver(runtime, before, actor.actor_id, command.route_id)
    _require_group_locomotion(before, actor.actor_id, route)
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
        or type(route.use_lifting) is not bool
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
            for pinned in (equipment.entries if equipment else ())
            if pinned.definition_id == item.definition_id
            for entry in (projected_profile(before.resources, item, pinned),)
            if entry.armor is not None and "torso" in entry.armor.locations
        ),
        default=0,
    )

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
    binding, progress, elapsed = _route_progress(before, actor.actor_id, route)
    _require_physical_interval(before, command, context, route, stats.st, elapsed)
    inputs = PhysicalSkillInputs(
        compiled, stats, route, load, hp, elapsed, runtime, before, actor.actor_id
    )
    _require_default_inputs(before, command, inputs)
    if context.skill_minimum:
        _require_skill_minimum(
            before,
            command,
            PhysicalSkillInputs(
                compiled, stats, route, load, hp, elapsed, runtime, before, actor.actor_id
            ),
        )
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
    if route.rope_length is not None and (
        not route.rope_length.is_finite() or route.rope_length < 0
    ):
        raise ValidationError("Invalid safety rope")
    if not 3600 <= route.hiking_day_seconds <= 86400:
        raise ValidationError("Invalid authored hiking day")
    if route.group_hike and command.kind != "hike":
        raise ValidationError("Group pacing is only supported for hiking")
    return PreparedFeat(
        compiled=compiled,
        actor_id=actor.actor_id,
        stats=stats,
        route=route,
        load=load,
        hp=hp,
        elapsed=elapsed,
        runtime=runtime,
        state=state,
        fp=fp,
        allowed=allowed,
        move=move,
        armor_dr=armor_dr,
        innate_dr=innate_dr,
        binding=binding,
        progress=progress,
    )


def _skill(feat: PhysicalSkillInputs, key: str, default: int) -> int:
    return require_default_skill(
        feat.runtime, feat.state, feat.actor_id, feat.compiled, key, default
    )


def _require_default_inputs(
    state: PlayState, command: PhysicalCommand, feat: PhysicalSkillInputs
) -> None:
    if command.kind == "climb":
        _skill(feat, "climbing", climbing_default(feat.stats.dx))
    elif command.kind == "hike":
        if feat.route.group_hike:
            require_hiking_defaults(feat.runtime, state, command.actor_id)
        elif not any(
            e.id == f"hiking-day:{command.actor_id}:{state.resources.game_time // 86400}"
            for e in state.resources.events
        ):
            _skill(feat, "hiking", feat.stats.ht - 5)
    elif (
        command.kind == "swim"
        and feat.elapsed % 300 == 0
        and not any(
            h.active and h.actor_id == command.actor_id and h.spec.kind == "drowning"
            for h in state.resources.hazards
        )
    ):
        _skill(feat, "swimming", feat.stats.ht - 4)


def _roll(
    feat: PreparedFeat, command: PhysicalCommand, rng: RandomSource, target: int
) -> CheckTrace:
    return success_roll(
        feat.stats.profile_id,
        max(1, target),
        _physical_modifiers(feat.state, command),
        rng=rng,
    )


def _physical_modifiers(state: PlayState, command: PhysicalCommand) -> tuple[Modifier, ...]:
    return check_modifiers(
        state.resources, command.actor_id, "dx" if command.kind == "climb" else "ht"
    )


def _climbing_target(feat: PhysicalSkillInputs) -> tuple[int, int]:
    route = feat.route
    if route.distance != int(route.distance) or route.distance <= 0:
        raise ValidationError("Climbs require positive whole feet")
    modifier, seconds = climbing(route.surface, int(route.distance))
    return (
        _skill(feat, "climbing", climbing_default(feat.stats.dx))
        + modifier
        - int(feat.load)
        - (feat.hp.injury.shock if feat.hp.injury else 0),
        seconds,
    )


def _lifting_target(feat: PhysicalSkillInputs) -> int | None:
    if not feat.route.use_lifting:
        return None
    value = next((v.value for v in feat.compiled.sheet.values if v.target == "skill:lifting"), None)
    return int(value) if value is not None else None


def _hiking_target(feat: PhysicalSkillInputs) -> int:
    return _skill(feat, "hiking", feat.stats.ht - 5)


def _swimming_target(feat: PhysicalSkillInputs) -> int:
    return (
        _skill(feat, "swimming", feat.stats.ht - 4)
        + (3 if feat.route.intentional and feat.elapsed == 0 else 0)
        - 2 * int(feat.load)
    )


def _voluntary_skill_target(
    state: PlayState, command: PhysicalCommand, feat: PhysicalSkillInputs
) -> int | None:
    if command.kind == "climb":
        return _climbing_target(feat)[0]
    if command.kind == "lift":
        return _lifting_target(feat)
    if command.kind == "hike":
        daily_id = f"hiking-day:{command.actor_id}:{state.resources.game_time // 86400}"
        if feat.route.group_hike or any(e.id == daily_id for e in state.resources.events):
            return None
        return _hiking_target(feat)
    if command.kind == "swim" and feat.route.intentional and feat.elapsed % 300 == 0:
        if not any(
            h.active and h.actor_id == command.actor_id and h.spec.kind == "drowning"
            for h in state.resources.hazards
        ):
            return _swimming_target(feat)
    return None


def _require_skill_minimum(
    state: PlayState, command: PhysicalCommand, feat: PhysicalSkillInputs
) -> None:
    target = _voluntary_skill_target(state, command, feat)
    if (
        target is not None
        and max(1, target) + sum(m.value for m in _physical_modifiers(state, command)) < 3
    ):
        raise ValidationError("Voluntary physical skill requires effective skill at least 3")


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
    route, stats = feat.route, feat.stats
    target, total_seconds = _climbing_target(feat)
    seconds = min(300, total_seconds - feat.elapsed)
    check = _roll(
        feat,
        command,
        rng,
        target,
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
        jumping=optional_default_skill(
            feat.runtime, feat.state, feat.actor_id, feat.compiled, "jumping", stats.basic_move * 2
        )
        or stats.basic_move * 2,
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
    lifting = _lifting_target(feat)
    checks = () if lifting is None else (_roll(feat, command, rng, int(lifting)),)
    margin = max(0, checks[0].margin) if checks and checks[0].outcome.succeeded else 0
    capacity, seconds = lift_limit(
        fatigue_value(
            feat.fp,
            power_blow_lift_strength(
                feat.state.resources,
                command.actor_id,
                feat.compiled.revision,
                feat.route.id,
                command.id,
                feat.stats.st,
            ),
        ),
        feat.route.lift_kind,
        margin=margin,
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
    route = feat.route
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
            check = _roll(feat, command, rng, _hiking_target(feat))
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
            _swimming_target(feat),
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
                optional_default_skill(
                    feat.runtime, feat.state, feat.actor_id, feat.compiled, "swimming", stats.ht - 4
                )
                or stats.ht,
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
    acrobatics = optional_default_skill(
        feat.runtime, feat.state, feat.actor_id, feat.compiled, "acrobatics", feat.stats.dx - 6
    )
    if route.controlled and acrobatics is not None:
        check = _roll(feat, command, rng, acrobatics)
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


def feat_event_id(command_id: str) -> str:
    """Keep existing IDs while bounding event IDs for long valid commands."""
    suffix = (
        command_id if len(command_id) <= 195 else hashlib.sha256(command_id.encode()).hexdigest()
    )
    return "feat:" + suffix


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
    state = runtime.advance(
        state,
        Advance(
            id="feat-time:" + internal,
            actor_id=command.actor_id,
            expected_revision=resources.revision,
            to=resources.game_time + seconds,
        ),
        rng=context.advance_rng,
    )
    resources = state.resources
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
            swimming=max(1, _skill(feat, "swimming", stats.ht - 4) - 2 * int(load)),
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
                    id=feat_event_id(command.id),
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
