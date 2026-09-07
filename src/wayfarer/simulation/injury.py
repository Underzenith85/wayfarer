"""GURPS torso injury on the existing resource checkpoint and receipt ledger.

Rules reconstructed from model knowledge: Lite 28-30; B378-381, B419-423.
Exact source/errata audit is pending. Server call sites supply damage, DR and HT;
this module is not an endpoint accepting player-authored wound commands.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.location_types import HitLocation, HumanLocation, LastingInjury
from wayfarer.rules.recovery_types import interrupt_tasks, require_settled, retire_tasks
from wayfarer.simulation.gurps_equipment import DamageType
from wayfarer.simulation.hit_locations import (
    crippling_threshold,
    effective_dr,
    knockdown_penalty,
    location_special_effects,
    missing_location,
    part,
    require_location,
    select_location,
    wound_factor,
)
from wayfarer.simulation.resources import (
    Command,
    Pool,
    Receipt,
    Record,
    ResourceEvent,
    ResourceState,
)


class Wound(Command):
    kind: Literal["wound"] = "wound"
    basic_damage: int = Field(ge=0)
    resistance: int = Field(ge=0)
    damage_type: DamageType
    location: HitLocation | None = None
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    tight_beam: bool = False
    from_behind: bool = False
    critical_eye: bool = False
    injury_source: Literal["attack", "area", "internal"] = "attack"


class ResolveCrippling(Command):
    kind: Literal["resolve-crippling"] = "resolve-crippling"
    injury_id: str
    physician_tl: int | None = Field(default=None, ge=0, le=12)


class DisableLocation(Command):
    kind: Literal["disable-location"] = "disable-location"
    location: Literal["left-arm", "right-arm"]
    duration_seconds: int = Field(ge=1)


class InjuryTurn(Command):
    kind: Literal["injury-turn"] = "injury-turn"
    turn: int = Field(ge=1)
    phase: Literal["start", "end"]
    do_nothing: bool = False
    attempts_defense: bool = False


class InjuryCheck(Record):
    reason: Literal[
        "death",
        "major-wound",
        "consciousness",
        "stun-recovery",
        "crippling-duration",
        "grip-retention",
    ]
    threshold: int | None = None
    check: CheckTrace


class InjuryResult(Record):
    penetration: int = 0
    injury: int = 0
    checks: tuple[InjuryCheck, ...] = ()
    dropped_ready_items: tuple[str, ...] = ()
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    duration_dice: tuple[int, ...] = ()
    uncapped_injury: int = 0
    effective_resistance: int = 0
    lasting_injury_ids: tuple[str, ...] = ()


_FACTORS = {
    "cr": (1, 1),
    "cut": (3, 2),
    "imp": (2, 1),
    "pi-": (1, 2),
    "pi": (1, 1),
    "pi+": (3, 2),
    "pi++": (2, 1),
    "burn": (1, 1),
    "cor": (1, 1),
    "tox": (1, 1),
}
_LITE_TYPES = frozenset({"cr", "cut", "imp", "pi-", "pi", "pi+"})


def apply_injury(
    state: ResourceState,
    command: Wound | InjuryTurn | ResolveCrippling | DisableLocation,
    *,
    ht: int,
    rng: RandomSource,
    system: bool = False,
    held_item_ids: tuple[str, ...] = (),
    force_major_wound: bool = False,
    double_shock: bool = False,
    held_item_locations: tuple[tuple[str, HumanLocation], ...] = (),
    shield_item_ids: tuple[str, ...] = (),
    dx: int | None = None,
    funny_bone: bool = False,
    halve_dr: Literal["up", "down"] | None = None,
    ignore_dr: bool = False,
) -> tuple[ResourceState, InjuryResult]:
    """Pure reducer; persist atomically via the existing commit_turn/CAS boundary.

    HP and injury facts share one Pool, so recovery/rebuild cannot forget wounds.
    Receipts use the resource ledger. Replays never draw more dice.
    """
    if not system:
        raise ValidationError("Injury resolution requires server authority")
    if type(ht) is not int or ht < 1:
        raise ValidationError("HT must come from a valid compiled character")
    ResourceState.model_validate(state)
    encoded = command.model_dump_json(
        exclude={"injury_source"}
        if isinstance(command, Wound) and command.injury_source == "attack"
        else None
    )
    if force_major_wound or double_shock:
        encoded += f":critical:{force_major_wound}:{double_shock}"
    if funny_bone or halve_dr or ignore_dr:
        encoded += f":location-critical:{funny_bone}:{halve_dr}:{ignore_dr}"
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    previous = next((r for r in state.receipts if r.command_id == command.id), None)
    if previous:
        if previous.digest != digest:
            raise ConflictError("Command ID reused with a different payload")
        event = next(e for e in state.events if e.id == f"injury:{command.id}")
        return state, InjuryResult.model_validate_json(event.kind)
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    require_settled(state.recovery_tasks, frozenset({command.actor_id}), state.game_time)
    pool = next((p for p in state.pools if p.id == f"hp:{command.actor_id}"), None)
    if pool is None or pool.injury is None:
        raise ValidationError("Explicit GURPS HP pool required; prototype pools are unchanged")
    if len(set(held_item_ids)) != len(held_item_ids) or not set(held_item_ids) <= {
        i.id for i in state.items if i.owner_id == command.actor_id and i.ready
    }:
        raise ValidationError("Held items must be unique, ready and owned by the injured actor")
    if not set(shield_item_ids) <= set(held_item_ids):
        raise ValidationError("Shields must be authoritative held items")
    if len(set(held_item_locations)) != len(held_item_locations) or any(
        i not in held_item_ids or part(h) != "hand" for i, h in held_item_locations
    ):
        raise ValidationError("Held location bindings must identify unique authoritative hands")
    status = pool.injury
    if (
        status.mortal_wound
        and not status.dead
        and (status.mortal_wound_due is None or state.game_time >= status.mortal_wound_due)
    ):
        raise ConflictError("Settle the mortal-wound survival check before further injury")
    current = pool.current
    checks: list[InjuryCheck] = []
    dropped: tuple[str, ...] = ()
    penetration = injury = 0
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    duration_dice: tuple[int, ...] = ()
    uncapped = resistance = 0
    lasting_ids: list[str] = []

    def check(
        reason: Literal[
            "death", "major-wound", "consciousness", "stun-recovery", "crippling-duration"
        ],
        penalty: int = 0,
        threshold: int | None = None,
    ) -> CheckTrace:
        trace = success_roll(status.profile_id, ht + penalty, rng=rng)
        checks.append(InjuryCheck(reason=reason, threshold=threshold, check=trace))
        return trace

    if isinstance(command, Wound):
        if command.damage_type not in _FACTORS or (
            status.profile_id == "gurps-lite-4e-2004" and command.damage_type not in _LITE_TYPES
        ):
            raise ValidationError("Damage type requires an unsupported mechanic")
        require_location(status, command.location)
        if command.injury_source != "attack" and command.location is not None:
            raise ValidationError("Area/internal injury cannot select a body part")
        if command.armor_divisor != 1 and status.profile_id != "gurps-basic-set-4e-2004":
            raise ValidationError("Armor divisors require Basic Set")
        if command.location is not None:
            location, location_dice = select_location(
                command.location, rng=rng, from_behind=command.from_behind
            )
            if command.location == "random" and missing_location(status, location):
                location = "torso"
        numerator, denominator = (
            (4, 1)
            if command.critical_eye
            and location is not None
            and part(location) == "eye"
            and command.damage_type != "tox"
            and location_special_effects(status, location)
            else wound_factor(
                location or "torso",
                command.damage_type,
                tight_beam=command.tight_beam,
                tolerance=status.tolerance if command.injury_source != "internal" else None,
            )
        )
        resistance = effective_dr(
            command.resistance,
            command.armor_divisor,
            location=location or "torso",
            damage_type=command.damage_type,
        )
        if halve_dr:
            resistance = (resistance + int(halve_dr == "up")) // 2
        if ignore_dr:
            resistance = 0
        penetration = max(0, command.basic_damage - resistance)
        injury = max(1, penetration * numerator // denominator) if penetration else 0
        if (
            status.tolerance is not None
            and status.tolerance.structure == "diffuse"
            and command.injury_source == "attack"
        ):
            injury = min(
                injury,
                1 if command.damage_type.startswith("pi") or command.damage_type == "imp" else 2,
            )
        uncapped = injury
        threshold = crippling_threshold(location, pool.maximum) if location else None
        crippled = threshold is not None and injury >= threshold
        funny = funny_bone and threshold is not None and injury > 0 and not crippled
        if crippled or funny:
            assert threshold is not None and location is not None
            destroyed = injury >= threshold * 2
            wound = LastingInjury(
                id=command.id + ":" + location,
                location=location,
                kind="severed"
                if destroyed and command.damage_type == "cut"
                else "destroyed"
                if destroyed
                else "crippled",
                duration="permanent" if destroyed else "timed" if funny else "pending",
                inflicted_at=state.game_time,
                injury=uncapped,
                recovery_at=state.game_time + max(2, 16 - ht) if funny else None,
            )
            lasting_ids.append(wound.id)
            status = status.model_copy(
                update={"lasting_injuries": status.lasting_injuries + (wound,)}
            )
            if part(location) != "eye":
                injury = min(injury, threshold)
            if part(location) in ("leg", "foot"):
                status = status.model_copy(update={"prone": True})
            affected = tuple(
                dict.fromkeys(
                    item_id
                    for item_id, hand in held_item_locations
                    if (
                        hand == location
                        or part(location) == "arm"
                        and hand == location.replace("arm", "hand")
                    )
                    and (
                        item_id not in shield_item_ids
                        or wound.kind == "severed"
                        and part(location) == "arm"
                    )
                )
            )
            drops: list[str] = []
            for item_id in affected:
                if sum(i == item_id for i, _ in held_item_locations) > 1:
                    if dx is None:
                        raise ValidationError("Two-handed grip loss requires compiled DX")
                    grip = success_roll(status.profile_id, dx, rng=rng)
                    checks.append(InjuryCheck(reason="grip-retention", check=grip))
                    if grip.outcome.succeeded:
                        continue
                drops.append(item_id)
            dropped = tuple(drops)
            if not set(dropped) <= set(held_item_ids):
                raise ValidationError("Location-held items must be authoritative ready items")
        if (
            location == "face"
            and command.damage_type == "cor"
            and injury * 2 > pool.maximum
            and not (status.tolerance and (status.tolerance.no_eyes or status.tolerance.no_head))
        ):
            eye_die = rng.randbelow(6) + 1 if injury <= pool.maximum else 0
            eyes: tuple[HumanLocation, ...] = (
                ("left-eye", "right-eye")
                if injury > pool.maximum
                else ("right-eye" if eye_die <= 3 else "left-eye",)
            )
            location_dice += (eye_die,) if eye_die else ()
            for eye in eyes:
                wound = LastingInjury(
                    id=command.id + ":" + eye,
                    location=eye,
                    kind="crippled",
                    duration="pending",
                    inflicted_at=state.game_time,
                    injury=injury,
                )
                status = status.model_copy(
                    update={"lasting_injuries": status.lasting_injuries + (wound,)}
                )
                lasting_ids.append(wound.id)
        if injury:
            state = state.model_copy(
                update={
                    "recovery_tasks": interrupt_tasks(
                        state.recovery_tasks, frozenset({command.actor_id}), state.game_time
                    )
                }
            )
        current -= injury
        if injury and not status.dead:
            shock = injury // max(1, pool.maximum // 10)
            double_shock = double_shock or (
                location == "groin"
                and status.male_groin
                and command.damage_type == "cr"
                and location_special_effects(status, location)
            )
            status = status.model_copy(
                update={
                    "shock": max(status.shock, min(8, shock * 2))
                    if double_shock
                    else min(8 if status.shock > 4 else 4, status.shock + shock),
                    "shock_expires": status.turn + 1,
                }
            )
            if current <= -5 * pool.maximum:
                status = status.model_copy(update={"dead": True})
            else:
                for multiple in range(1, 5):
                    threshold = -multiple * pool.maximum
                    if current <= threshold < pool.current:
                        trace = check("death", threshold=threshold)
                        if not trace.outcome.succeeded:
                            mortal = (
                                not status.mortal_wound
                                and trace.margin in (-1, -2)
                                and trace.outcome is not Outcome.CRITICAL_FAILURE
                            )
                            status = status.model_copy(
                                update={
                                    "mortal_wound": mortal,
                                    "mortal_wound_due": state.game_time + 1800 if mortal else None,
                                    "mortal_wound_started": state.game_time,
                                    "dead": not mortal,
                                }
                            )
                            if status.dead:
                                break
                major = force_major_wound or crippled or injury * 2 > pool.maximum
                head_shock = (
                    location is not None
                    and (location in ("skull", "face", "vitals") or part(location) == "eye")
                    and shock > 0
                    and command.damage_type != "tox"
                    and location_special_effects(status, location)
                )
                if (force_major_wound or major or head_shock) and not status.incapacitated:
                    penalty = (
                        knockdown_penalty(location, major=major, male_groin=status.male_groin)
                        if location
                        and command.damage_type != "tox"
                        and location_special_effects(status, location)
                        else 0
                    )
                    trace = check("major-wound", penalty)
                    if not trace.outcome.succeeded:
                        status = status.model_copy(
                            update={
                                "stunned": True,
                                "prone": True,
                                "unconscious": trace.margin <= -5
                                or trace.outcome is Outcome.CRITICAL_FAILURE,
                            }
                        )
                        dropped = tuple(i.id for i in state.items if i.id in held_item_ids)
    elif isinstance(command, ResolveCrippling):
        require_location(status, "torso")
        pending_wound = next(
            (w for w in status.lasting_injuries if w.id == command.injury_id), None
        )
        if pending_wound is None or pending_wound.duration != "pending":
            raise ValidationError("Pending crippling injury required")
        wound = pending_wound
        trace = check("crippling-duration")
        if trace.outcome.succeeded:
            updated_wound = wound.model_copy(update={"duration": "temporary"})
        elif trace.outcome is Outcome.CRITICAL_FAILURE:
            updated_wound = wound.model_copy(update={"duration": "permanent"})
        else:
            die = rng.randbelow(6) + 1
            duration_dice = (die,)
            reduction = min(3, max(0, (command.physician_tl or 0) - 4))
            updated_wound = wound.model_copy(
                update={
                    "duration": "lasting",
                    "recovery_at": state.game_time + max(1, die - reduction) * 30 * 86400,
                }
            )
        lasting_ids.append(wound.id)
        status = status.model_copy(
            update={
                "lasting_injuries": tuple(
                    updated_wound if w.id == wound.id else w for w in status.lasting_injuries
                )
            }
        )
    elif isinstance(command, DisableLocation):
        require_location(status, command.location)
        wound = LastingInjury(
            id=command.id,
            location=command.location,
            kind="disabled",
            duration="timed",
            inflicted_at=state.game_time,
            injury=0,
            recovery_at=state.game_time + command.duration_seconds,
        )
        status = status.model_copy(update={"lasting_injuries": status.lasting_injuries + (wound,)})
        lasting_ids.append(wound.id)
        # B557 shoulder strain prevents use, but does not require dropping the weapon.
    else:
        if command.phase == "start":
            if status.phase != "between" or command.turn != status.turn + 1:
                raise ValidationError("Injury turns must start once, in order")
            if status.stunned and not command.do_nothing:
                raise ValidationError("Stunned actors must Do Nothing")
            status = status.model_copy(update={"phase": "acting", "turn": command.turn})
            if (
                current <= 0
                and not status.incapacitated
                and (not command.do_nothing or command.attempts_defense)
            ):
                trace = check("consciousness", -(max(0, -current) // pool.maximum))
                if not trace.outcome.succeeded:
                    status = status.model_copy(update={"unconscious": True})
        else:
            if status.phase != "acting" or command.turn != status.turn:
                raise ValidationError("Injury turns must end once, after starting")
            if status.stunned and not status.incapacitated:
                if not command.do_nothing:
                    raise ValidationError("Stun recovery requires Do Nothing")
                if check("stun-recovery").outcome.succeeded:
                    status = status.model_copy(update={"stunned": False})
            status = status.model_copy(
                update={
                    "phase": "between",
                    "shock": 0 if status.shock_expires <= status.turn else status.shock,
                }
            )
    updated_pool = Pool(id=pool.id, current=current, maximum=pool.maximum, injury=status)
    if status.dead:
        state = state.model_copy(
            update={
                "recovery_tasks": retire_tasks(
                    state.recovery_tasks, frozenset({command.actor_id}), state.game_time
                )
            }
        )
    result = InjuryResult(
        penetration=penetration,
        injury=injury,
        checks=tuple(checks),
        dropped_ready_items=dropped,
        location=location,
        location_dice=location_dice,
        duration_dice=duration_dice,
        uncapped_injury=uncapped,
        effective_resistance=resistance,
        lasting_injury_ids=tuple(lasting_ids),
    )
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "pools": tuple(updated_pool if p.id == pool.id else p for p in state.pools),
            "items": tuple(
                i.model_copy(update={"ready": False, "equipped": False}) if i.id in dropped else i
                for i in state.items
            ),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=f"injury:{command.id}",
                    at=state.game_time,
                    kind=result.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )
    if injury:
        from wayfarer.simulation.spell_effects import break_daze

        updated = break_daze(updated, command.actor_id, command.id)
    return ResourceState.model_validate(updated), result


def impaired_movement(pool: Pool, value: int) -> int:
    """Less than one-third HP halves Move and Dodge, rounding upward."""
    if pool.injury is None or value < 0:
        raise ValidationError("Requires a profile HP pool and nonnegative movement")
    return (value + 1) // 2 if pool.current * 3 < pool.maximum else value


def apply_location_effect(
    state: ResourceState,
    command: DisableLocation,
    *,
    system: bool = False,
    held_item_ids: tuple[str, ...] = (),
) -> tuple[ResourceState, InjuryResult]:
    """Persist a timed critical-miss shoulder effect without inventing HP damage."""

    class NoDice:
        def randbelow(self, upper: int) -> int:
            raise AssertionError("A timed location effect must not roll dice")

    return apply_injury(
        state, command, ht=1, rng=NoDice(), system=system, held_item_ids=held_item_ids
    )
