"""B235-236 backfires and B235 mana refunds in the existing resource ledger.

Characters, Fourth Edition, third printing (February 2008), inspected directly.
Rows needing a setting-specific target, reverse effect or creature remain pending
until an authoritative adapter supplies that choice; they never silently vanish.
"""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.simulation.resources import Id, Record, ResourceEvent, ResourceState

PREFIX = "spell-backfire:"
REFUND = "mana-refund:"
WEEK = 7 * 86400


class Backfire(Record):
    id: Id
    cast_id: Id
    actor_id: Id
    spell_id: str
    at: int
    dice: tuple[int, ...] = Field(default=(), max_length=3)
    row: int = Field(default=0, ge=0, le=18)
    severity: Literal["normal", "mild", "disaster"] = "normal"
    pending: bool = False
    stunned: bool = False
    stun_due_at: int | None = None
    stun_roll: tuple[int, ...] = ()
    forgotten: bool = False
    remember_at: int | None = None
    flavor: Literal["noise", "shadow", "illusion"] | None = None
    resolution_id: Id | None = None
    target_id: Id | None = None


class ManaRefund(Record):
    id: Id
    actor_id: Id
    amount: int = Field(ge=0)
    due_at: int
    due_turn: int | None = None
    settled: bool = False
    granted: int = 0


def backfires(state: ResourceState) -> tuple[Backfire, ...]:
    found = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            item = Backfire.model_validate_json(event.kind)
            found[item.id] = item
    return tuple(found.values())


def save(state: ResourceState, item: Backfire, command_id: str) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=item.actor_id,
                    kind=item.model_dump_json(),
                ),
            )
        }
    )


def require_settled(state: ResourceState, actor_id: str) -> None:
    if any(b.actor_id == actor_id and b.pending for b in backfires(state)):
        raise ConflictError("Resolve the recorded spell backfire before acting")


def forgotten(state: ResourceState, actor_id: str, spell_id: str) -> bool:
    return any(
        b.actor_id == actor_id and b.spell_id == spell_id and b.forgotten for b in backfires(state)
    )


def mental_stun(state: ResourceState, actor_id: str) -> bool:
    return any(b.actor_id == actor_id and b.stunned for b in backfires(state))


def clear_stun(state: ResourceState, actor_id: str, command_id: str) -> ResourceState:
    for item in backfires(state):
        if item.actor_id == actor_id and item.stunned:
            state = save(
                state, item.model_copy(update={"stunned": False}), command_id + ":" + item.id
            )
    return state


def remember(
    state: ResourceState, actor_id: str, cast_id: str, command_id: str, iq: int, rng: RandomSource
) -> tuple[ResourceState, CheckTrace]:
    item = next(
        (
            b
            for b in backfires(state)
            if b.actor_id == actor_id and b.cast_id == cast_id and b.forgotten
        ),
        None,
    )
    if item is None or item.remember_at is None or state.game_time < item.remember_at:
        raise ConflictError("Forgotten spell is not due for its weekly IQ check")
    trace = success_roll("gurps-basic-set-4e-2004", iq, rng=rng)
    item = item.model_copy(
        update={
            "forgotten": not trace.outcome.succeeded,
            "remember_at": None if trace.outcome.succeeded else state.game_time + WEEK,
        }
    )
    return save(state, item, command_id), trace


def apply_backfire(
    state: ResourceState,
    *,
    command_id: str,
    actor_id: str,
    cast_id: str,
    spell_id: str,
    ht: int,
    severity: Literal["normal", "mild", "disaster"],
    rng: RandomSource,
) -> ResourceState:
    from wayfarer.simulation.injury import Wound, apply_injury

    identifier = hashlib.sha256(command_id.encode()).hexdigest()
    if severity != "normal":
        # Low mana explicitly permits no consequence; spectacular disasters need
        # a setting-specific consequence, never an invented damage multiplier.
        item = Backfire(
            id=identifier,
            cast_id=cast_id,
            actor_id=actor_id,
            spell_id=spell_id,
            at=state.game_time,
            severity=severity,
            pending=severity == "disaster",
        )
        return save(state, item, command_id)
    dice = tuple(rng.randbelow(6) + 1 for _ in range(3))
    row = sum(dice)
    item = Backfire(
        id=identifier,
        cast_id=cast_id,
        actor_id=actor_id,
        spell_id=spell_id,
        at=state.game_time,
        dice=dice,
        row=row,
        pending=row in (4, 5, 6, 7, 13, 15, 16, 18),
        stunned=row == 9,
        stun_due_at=state.game_time + 1 if row == 9 else None,
        forgotten=row == 17,
        remember_at=state.game_time + WEEK if row == 17 else None,
        flavor="noise"
        if row in (10, 11)
        else "shadow"
        if row == 12
        else "illusion"
        if row == 14
        else None,
    )
    if row in (3, 8):
        amount = rng.randbelow(6) + 1 if row == 3 else 1
        state, _ = apply_injury(
            state,
            Wound(
                id="backfire:" + identifier,
                actor_id=actor_id,
                expected_revision=state.revision,
                basic_damage=amount,
                resistance=0,
                damage_type="cr",
                injury_source="internal",
            ),
            ht=ht,
            rng=rng,
            system=True,
        )
    if row == 9:
        pool = next(p for p in state.pools if p.id == "hp:" + actor_id)
        if pool.injury is None:
            raise ValidationError("Backfire requires authoritative injury state")
        pool = pool.model_copy(update={"injury": pool.injury.model_copy(update={"stunned": True})})
        state = state.model_copy(
            update={"pools": tuple(pool if p.id == pool.id else p for p in state.pools)}
        )
    return save(state, item, command_id)


def refund_later(
    state: ResourceState,
    actor_id: str,
    command_id: str,
    amount: int,
    *,
    combat: bool,
    before_turn: bool = False,
) -> ResourceState:
    pool = next(p for p in state.pools if p.id == "hp:" + actor_id)
    assert pool.injury
    item = ManaRefund(
        id=hashlib.sha256(command_id.encode()).hexdigest(),
        actor_id=actor_id,
        amount=amount,
        due_at=state.game_time + 1,
        due_turn=pool.injury.turn + 1 + int(before_turn) if combat else None,
    )
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=REFUND + item.id,
                    at=state.game_time,
                    target_id=actor_id,
                    kind=item.model_dump_json(),
                ),
            )
        }
    )


def refund_due(state: ResourceState, actor_id: str, *, turn: int | None = None) -> ResourceState:
    found = {}
    for event in state.events:
        if event.id.startswith(REFUND):
            item = ManaRefund.model_validate_json(event.kind)
            found[item.id] = item
    for item in found.values():
        if item.actor_id != actor_id or item.settled:
            continue
        if (item.due_turn is not None and (turn is None or turn < item.due_turn)) or (
            item.due_turn is None and state.game_time < item.due_at
        ):
            continue
        pool = next(p for p in state.pools if p.id == "fp:" + actor_id)
        assert pool.fatigue
        status = pool.fatigue
        ceiling = pool.maximum - status.starvation - status.dehydration - status.sleep
        granted = min(item.amount, max(0, ceiling - pool.current))
        current = pool.current + granted
        pool = pool.model_copy(
            update={
                "current": current,
                "fatigue": status.model_copy(
                    update={
                        "collapsed": status.collapsed and current <= 0,
                        "unconscious": status.unconscious and current <= 0,
                    }
                ),
            }
        )
        item = item.model_copy(update={"settled": True, "granted": granted})
        state = state.model_copy(
            update={
                "pools": tuple(pool if p.id == pool.id else p for p in state.pools),
                "events": state.events
                + (
                    ResourceEvent(
                        id=REFUND + item.id + ":settled",
                        at=state.game_time,
                        target_id=actor_id,
                        kind=item.model_dump_json(),
                    ),
                ),
            }
        )
    return state
