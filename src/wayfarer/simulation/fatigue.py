"""GURPS FP costs and continued exertion, using the injury/resource ledger.

Numeric expectations: Basic Set Campaigns, fourth printing, B426-427.
No time advances here; a trusted action adapter supplies the exertion cost.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.recovery_types import FatigueCause, interrupt_tasks, require_settled
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.injury import InjuryResult, Wound, apply_injury
from wayfarer.simulation.resources import (
    Command,
    Pool,
    Receipt,
    Record,
    ResourceEvent,
    ResourceState,
)


class FatigueCost(Command):
    kind: Literal["fatigue-cost"] = "fatigue-cost"
    amount: int = Field(ge=0, le=100000)
    cause: FatigueCause = "ordinary"


class ContinueExertion(Command):
    kind: Literal["continue-exertion"] = "continue-exertion"


class FatigueResult(Record):
    fp_lost: int = 0
    hp_lost: int = 0
    allowed: bool = True
    checks: tuple[CheckTrace, ...] = ()
    injury: InjuryResult | None = None


def apply_fatigue(
    state: ResourceState,
    command: FatigueCost | ContinueExertion,
    *,
    ht: int,
    rng: RandomSource,
    will: int | None = None,
    system: bool = False,
) -> tuple[ResourceState, FatigueResult]:
    """Costs are forced effects; voluntary actions use ContinueExertion first.

    Continued exertion at nonpositive FP requires compiled Will. The caller must
    persist a failed exertion roll too, before rejecting the proposed action.
    """
    if not system:
        raise ValidationError("Fatigue requires authoritative action context")
    ResourceState.model_validate(state)
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    previous = next((r for r in state.receipts if r.command_id == command.id), None)
    if previous:
        if previous.digest != digest:
            raise ConflictError("Fatigue command ID reused")
        event = next(e for e in state.events if e.id == f"fatigue:{command.id}")
        return state, FatigueResult.model_validate_json(event.kind)
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    require_settled(state.recovery_tasks, frozenset({command.actor_id}), state.game_time)
    pool = next((p for p in state.pools if p.id == f"fp:{command.actor_id}"), None)
    hp = next((p for p in state.pools if p.id == f"hp:{command.actor_id}"), None)
    if pool is None or pool.fatigue is None or hp is None or hp.injury is None:
        raise ValidationError("Explicit GURPS FP and HP pools required")
    status = pool.fatigue
    if hp.injury.profile_id != status.profile_id or type(ht) is not int or ht < 1:
        raise ValidationError("Fatigue requires matching profile and compiled HT")
    current = pool.current
    checks: list[CheckTrace] = []
    fp_lost = hp_lost = 0
    allowed = True
    injury = None
    if isinstance(command, ContinueExertion):
        state = state.model_copy(
            update={
                "recovery_tasks": interrupt_tasks(
                    state.recovery_tasks, frozenset({command.actor_id}), state.game_time
                )
            }
        )
        allowed = not (
            status.collapsed
            or status.unconscious
            or status.heart_attack
            or hp.injury.incapacitated
            or current <= -pool.maximum
        )
        if allowed and current <= 0:
            if type(will) is not int or will < 1:
                raise ValidationError("Continued exertion requires compiled Will")
            check = success_roll(
                status.profile_id, will, check_modifiers(state, command.actor_id, "will"), rng=rng
            )
            checks.append(check)
            allowed = check.outcome.succeeded
            if not allowed:
                updates: dict[str, object] = {"collapsed": True}
                if check.outcome is Outcome.CRITICAL_FAILURE:
                    heart = success_roll(
                        status.profile_id,
                        ht,
                        check_modifiers(state, command.actor_id, "ht"),
                        rng=rng,
                    )
                    checks.append(heart)
                    updates["heart_attack"] = not heart.outcome.succeeded
                    if not heart.outcome.succeeded:
                        fp_lost = current + pool.maximum
                        current = -pool.maximum
                        updates.update(
                            unconscious=True, heart_attack_deadline=state.game_time + 20 * ht
                        )
                status = status.model_copy(update=updates)
    else:
        if command.amount:
            state = state.model_copy(
                update={
                    "recovery_tasks": interrupt_tasks(
                        state.recovery_tasks, frozenset({command.actor_id}), state.game_time
                    )
                }
            )
        fp_lost = min(command.amount, current + pool.maximum)
        hp_lost = max(0, command.amount - max(0, current))
        current -= fp_lost
        changes: dict[str, object] = {"unconscious": status.unconscious or current <= -pool.maximum}
        if command.cause != "ordinary":
            changes[command.cause] = getattr(status, command.cause) + fp_lost
        status = status.model_copy(update=changes)
        if hp_lost:
            state, injury = apply_injury(
                state,
                Wound(
                    id="fp-hp:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    actor_id=command.actor_id,
                    expected_revision=state.revision,
                    basic_damage=hp_lost,
                    resistance=0,
                    damage_type="cr",
                    injury_source="internal",
                ),
                ht=ht,
                rng=rng,
                system=True,
            )
    updated_pool = pool.model_copy(update={"current": current, "fatigue": status})
    result = FatigueResult(
        fp_lost=fp_lost, hp_lost=hp_lost, allowed=allowed, checks=tuple(checks), injury=injury
    )
    updated = state.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "pools": tuple(updated_pool if p.id == pool.id else p for p in state.pools),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=f"fatigue:{command.id}",
                    at=state.game_time,
                    target_id=command.actor_id,
                    kind=result.model_dump_json(),
                ),
            ),
        }
    )
    return ResourceState.model_validate(updated), result


def fatigue_value(pool: Pool, value: int) -> int:
    """Halve current ST/Move/Dodge below 1/3 FP; never base HP or damage."""
    if pool.fatigue is None or value < 0:
        raise ValidationError("Requires profile FP and a nonnegative value")
    return (value + 1) // 2 if pool.current * 3 < pool.maximum else value


def exertion_cost(
    kind: Literal["battle", "hiking", "overexertion"],
    *,
    seconds: int,
    encumbrance: int = 0,
    hot: bool = False,
    heavy_clothing: bool = False,
    participated: bool = True,
) -> int:
    if seconds < 0 or not 0 <= encumbrance <= 4:
        raise ValidationError("Invalid exertion duration or encumbrance")
    if kind == "overexertion":
        return seconds
    cost = 1 + encumbrance + ((2 if heavy_clothing else 1) if hot else 0)
    return (
        cost * (seconds // 3600)
        if kind == "hiking"
        else cost
        if seconds > 10 and participated
        else 0
    )
