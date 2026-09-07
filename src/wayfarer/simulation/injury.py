"""GURPS torso injury on the existing resource checkpoint and receipt ledger.

Rules reconstructed from model knowledge: Lite 28-30; B378-381, B419-423.
Exact source/errata audit is pending. Server call sites supply damage, DR and HT;
this module is not an endpoint accepting player-authored wound commands.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.recovery_types import interrupt_tasks, require_settled, retire_tasks
from wayfarer.simulation.gurps_equipment import DamageType
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


class InjuryTurn(Command):
    kind: Literal["injury-turn"] = "injury-turn"
    turn: int = Field(ge=1)
    phase: Literal["start", "end"]
    do_nothing: bool = False
    attempts_defense: bool = False


class InjuryCheck(Record):
    reason: Literal["death", "major-wound", "consciousness", "stun-recovery"]
    threshold: int | None = None
    check: CheckTrace


class InjuryResult(Record):
    penetration: int = 0
    injury: int = 0
    checks: tuple[InjuryCheck, ...] = ()
    dropped_ready_items: tuple[str, ...] = ()


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
    command: Wound | InjuryTurn,
    *,
    ht: int,
    rng: RandomSource,
    system: bool = False,
    held_item_ids: tuple[str, ...] = (),
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
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
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
    status = pool.injury
    current = pool.current
    checks: list[InjuryCheck] = []
    dropped: tuple[str, ...] = ()
    penetration = injury = 0

    def check(
        reason: Literal["death", "major-wound", "consciousness", "stun-recovery"],
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
        penetration = max(0, command.basic_damage - command.resistance)
        numerator, denominator = _FACTORS[command.damage_type]
        injury = max(1, penetration * numerator // denominator) if penetration else 0
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
            status = status.model_copy(
                update={"shock": min(4, status.shock + shock), "shock_expires": status.turn + 1}
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
                                update={"mortal_wound": mortal, "dead": not mortal}
                            )
                            if status.dead:
                                break
                if injury * 2 > pool.maximum and not status.incapacitated:
                    trace = check("major-wound")
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
        penetration=penetration, injury=injury, checks=tuple(checks), dropped_ready_items=dropped
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
    return ResourceState.model_validate(updated), result


def impaired_movement(pool: Pool, value: int) -> int:
    """Less than one-third HP halves Move and Dodge, rounding upward."""
    if pool.injury is None or value < 0:
        raise ValidationError("Requires a profile HP pool and nonnegative movement")
    return (value + 1) // 2 if pool.current * 3 < pool.maximum else value
