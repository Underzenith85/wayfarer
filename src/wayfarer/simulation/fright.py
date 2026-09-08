"""Timed B360-361 consequences in the authoritative resource event ledger.

Campaigns, Fourth Edition, fourth printing. Choice-bearing consequences remain
visible adjudication requirements and never edit approved character purchases.
"""

import hashlib

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RandomSource
from wayfarer.rules.fright import FrightEffect
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.simulation.fatigue import FatigueCost, apply_fatigue
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.resources import Record, ResourceEvent, ResourceState

PREFIX = "fright-runtime:"


class TimedFright(Record):
    id: str
    actor_id: str
    trigger_id: str
    effect: FrightEffect
    started: int
    due: int | None
    active: bool
    recovery_target: int
    aftermath_until: int | None = None


def effects(state: ResourceState) -> tuple[TimedFright, ...]:
    latest: dict[str, TimedFright] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            item = TimedFright.model_validate_json(event.kind)
            latest[item.id] = item
    return tuple(latest.values())


def save(state: ResourceState, item: TimedFright, command_id: str) -> ResourceState:
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


def blocked(state: ResourceState, actor_id: str) -> bool:
    return any(item.actor_id == actor_id and item.active for item in effects(state))


def requires_adjudication(state: ResourceState, actor_id: str) -> bool:
    return any(
        item.actor_id == actor_id
        and (
            item.effect.permanent_ht_loss
            or item.effect.permanent_iq_loss
            or (item.aftermath_until is not None and state.game_time < item.aftermath_until)
        )
        for item in effects(state)
    )


def validate_subject(state: ResourceState, actor_id: str, profile_id: str) -> None:
    hp = next((p for p in state.pools if p.id == "hp:" + actor_id), None)
    fp = next((p for p in state.pools if p.id == "fp:" + actor_id), None)
    if (
        hp is None
        or hp.injury is None
        or hp.injury.profile_id != profile_id
        or fp is None
        or fp.fatigue is None
        or fp.fatigue.profile_id != profile_id
    ):
        raise ValidationError("Fright requires matching authoritative HP and FP pools")


def apply_effect(
    state: ResourceState,
    effect: FrightEffect,
    *,
    actor_id: str,
    trigger_id: str,
    command_id: str,
    ht: int,
    will: int,
    modified_will: int,
    rng: RandomSource,
) -> ResourceState:
    if effect.hp_loss:
        state, _ = apply_injury(
            state,
            Wound(
                id="fright-hp:" + command_id,
                actor_id=actor_id,
                expected_revision=state.revision,
                basic_damage=effect.hp_loss,
                resistance=0,
                damage_type="cr",
                injury_source="internal",
            ),
            ht=ht,
            rng=rng,
            system=True,
        )
    if effect.fp_loss:
        state, _ = apply_fatigue(
            state,
            FatigueCost(
                id="fright-fp:" + command_id,
                actor_id=actor_id,
                expected_revision=state.revision,
                amount=effect.fp_loss,
            ),
            ht=ht,
            rng=rng,
            system=True,
        )
    target = {"ht": ht, "will": will, "modified-will": modified_will, "none": 0}[
        effect.recovery_attribute
    ]
    item = TimedFright(
        id=command_id,
        actor_id=actor_id,
        trigger_id=trigger_id,
        effect=effect,
        started=state.game_time,
        active=effect.condition != "none",
        due=state.game_time + min(effect.duration_seconds, 86400)
        if effect.neglect_progression
        else state.game_time + effect.duration_seconds
        if effect.duration_seconds or effect.recovery_interval_seconds
        else None,
        recovery_target=target,
    )
    return save(state, item, command_id)


def recover(
    state: ResourceState,
    *,
    actor_id: str,
    trigger_id: str,
    command_id: str,
    rng: RandomSource,
) -> tuple[ResourceState, bool]:
    item = next(
        (
            i
            for i in effects(state)
            if i.actor_id == actor_id and i.trigger_id == trigger_id and i.active
        ),
        None,
    )
    if item is None or item.due is None or state.game_time != item.due:
        raise ConflictError("Fright recovery requires its exact scheduled deadline")
    effect = item.effect
    if effect.neglect_progression:
        raise ValidationError("Catatonia recovery requires medical-care adjudication")
    passed = (
        effect.recovery_attribute == "none"
        or success_roll(
            "gurps-basic-set-4e-2004",
            item.recovery_target,
            rng=rng,
        ).outcome.succeeded
    )
    interval = effect.recovery_interval_seconds
    if not passed and effect.repeat_duration_dice:
        interval = sum(rng.randbelow(6) + 1 for _ in range(effect.repeat_duration_dice))
        interval *= effect.repeat_duration_unit
    item = item.model_copy(
        update={
            "active": not passed,
            "due": None if passed else state.game_time + interval,
            "aftermath_until": state.game_time + effect.aftermath_seconds
            if passed and effect.aftermath_seconds
            else None,
        }
    )
    return save(state, item, command_id), passed
