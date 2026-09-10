"""Timed B360-361 consequences in the authoritative resource event ledger.

Campaigns, Fourth Edition, fourth printing. Choice-bearing consequences remain
visible adjudication requirements and never edit approved character purchases.
"""

import hashlib
import json

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace, Modifier, RandomSource
from wayfarer.rules.fright import FrightEffect
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.simulation.fatigue import FatigueCost, apply_fatigue
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.resources import (
    Advance,
    Record,
    ResourceEngine,
    ResourceEvent,
    ResourceState,
)

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
    recovery_due: int | None = None
    care: bool = False
    neglect_days: int = 0
    next_care_due: int | None = None
    panic_responses: tuple[str, ...] = ()
    recovery_checks: tuple[CheckTrace, ...] = ()


def effects(state: ResourceState) -> tuple[TimedFright, ...]:
    latest: dict[str, TimedFright] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            item = TimedFright.model_validate_json(event.kind)
            latest[item.id] = item
    return tuple(latest.values())


def public_id(item: TimedFright) -> str:
    """An opaque reference that cannot disclose an authored NPC occurrence ID."""
    return hashlib.sha256(item.id.encode()).hexdigest()


def projection(
    state: ResourceState, actor_ids: tuple[str, ...], *, director: bool = False
) -> tuple[dict[str, object], ...]:
    """Expose consequences and required decisions, never their hidden cause or rolls."""
    result: list[dict[str, object]] = []
    for item in effects(state):
        if not director and item.actor_id not in actor_ids:
            continue
        effect = item.effect
        choices: list[dict[str, object]] = []
        if effect.trait_choice != "none":
            choices.append({"kind": effect.trait_choice, "points": effect.trait_points})
        for attribute, loss in (("ht", effect.permanent_ht_loss), ("iq", effect.permanent_iq_loss)):
            if loss:
                choices.append(
                    {"kind": "permanent-attribute-loss", "attribute": attribute, "loss": loss}
                )
        aftermath = item.aftermath_until is not None and state.game_time < item.aftermath_until
        if not (item.active or choices or aftermath):
            continue
        value: dict[str, object] = {
            "id": public_id(item),
            "actor_id": item.actor_id,
            "condition": effect.condition if item.active else "none",
            "active": item.active,
            "choices": tuple(choices),
            "build_approval_required": bool(choices),
            "aftermath_until": item.aftermath_until if aftermath else None,
            "aftermath_penalty": effect.aftermath_penalty if aftermath else 0,
            "panic_response_required": item.active and effect.condition == "panic",
            "care_required": item.active and effect.neglect_progression,
        }
        if director:
            value.update(
                {
                    "care": item.care,
                    "panic_severity": effect.panic_severity,
                    "panic_responses": item.panic_responses,
                    "decision_kinds": ("care",)
                    if item.active and effect.neglect_progression
                    else ("panic-response",)
                    if item.active and effect.table_total == 33
                    else (),
                }
            )
        result.append(value)
    return tuple(result)


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


def stunned(state: ResourceState, actor_id: str) -> bool:
    return any(
        i.actor_id == actor_id and i.active and i.effect.condition == "stunned"
        for i in effects(state)
    )


def can_defend(state: ResourceState, actor_id: str) -> bool:
    return all(
        i.effect.condition == "stunned"
        for i in effects(state)
        if i.actor_id == actor_id and i.active
    )


def aftermath_penalty(state: ResourceState, actor_id: str) -> int:
    """B361: recovered coma/catatonia penalizes skill and attribute checks.

    This is a check modifier, not a reduction of purchased attributes, damage,
    resource maxima, reaction totals, self-control ratings or active defenses.
    Each independent episode retains its own expiration in the event ledger.
    """
    return sum(m.value for m in aftermath_modifiers(state, actor_id))


def aftermath_modifiers(state: ResourceState, actor_id: str) -> tuple[Modifier, ...]:
    return tuple(
        Modifier(
            item.effect.aftermath_penalty,
            "Fright aftermath",
            item.id,
            "Basic Set Campaigns 4e B361",
        )
        for item in effects(state)
        if item.actor_id == actor_id
        and not item.active
        and item.aftermath_until is not None
        and state.game_time < item.aftermath_until
    )


def requires_adjudication(
    state: ResourceState, actor_id: str, *, handles_aftermath: bool = False
) -> bool:
    return any(
        item.actor_id == actor_id
        and (
            item.effect.permanent_ht_loss
            or item.effect.permanent_iq_loss
            or (
                not handles_aftermath
                and item.aftermath_until is not None
                and state.game_time < item.aftermath_until
            )
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
            ht=ht + aftermath_penalty(state, actor_id),
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
            ht=ht + aftermath_penalty(state, actor_id),
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
        recovery_due=state.game_time + effect.duration_seconds,
        next_care_due=state.game_time + 86400 if effect.neglect_progression else None,
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
    hp = next((p for p in state.pools if p.id == "hp:" + actor_id), None)
    if hp is not None and hp.injury is not None and hp.injury.dead:
        return save(
            state, item.model_copy(update={"active": False, "due": None}), command_id
        ), False
    if effect.neglect_progression:
        if item.next_care_due is None:
            item = item.model_copy(update={"next_care_due": item.started + 86400})
        # Care is an explicit director decision; absence of care means neglect.
        if item.next_care_due is not None and state.game_time == item.next_care_due:
            days = item.neglect_days + int(not item.care)
            if not item.care:
                state, _ = apply_injury(
                    state,
                    Wound(
                        id="fright-neglect:" + command_id,
                        actor_id=actor_id,
                        expected_revision=state.revision,
                        basic_damage=days,
                        resistance=0,
                        damage_type="cr",
                        injury_source="internal",
                    ),
                    ht=item.recovery_target + aftermath_penalty(state, actor_id),
                    rng=rng,
                    system=True,
                )
                hp = next(p for p in state.pools if p.id == "hp:" + actor_id)
                if hp.injury is not None and hp.injury.dead:
                    return save(
                        state, item.model_copy(update={"active": False, "due": None}), command_id
                    ), False
            item = item.model_copy(
                update={
                    "neglect_days": days,
                    "next_care_due": state.game_time + 86400,
                }
            )
        recovery_due = item.recovery_due or item.started + effect.duration_seconds
        if state.game_time < recovery_due:
            item = item.model_copy(
                update={"due": min(recovery_due, item.next_care_due or recovery_due)}
            )
            return save(state, item, command_id), False
    from wayfarer.simulation.physical_traits import physical_traits

    traits = physical_traits(state, actor_id)
    bonus = traits.fitness if effect.recovery_attribute == "ht" else 0
    check = (
        None
        if effect.recovery_attribute == "none"
        else success_roll(
            "gurps-basic-set-4e-2004",
            item.recovery_target + bonus,
            aftermath_modifiers(state, actor_id),
            rng=rng,
        )
    )
    passed = check is None or check.outcome.succeeded
    if passed and effect.condition == "retching":
        # B428: the FP loss occurs when retching ends, not when it starts or
        # on each failed recovery check. The enclosing receipt makes it once-only.
        state, _ = apply_fatigue(
            state,
            FatigueCost(
                id="fright-retching:" + command_id,
                actor_id=actor_id,
                expected_revision=state.revision,
                amount=1,
            ),
            ht=item.recovery_target + aftermath_penalty(state, actor_id),
            rng=rng,
            system=True,
        )
    interval = effect.recovery_interval_seconds
    if not passed and effect.repeat_duration_dice:
        interval = sum(rng.randbelow(6) + 1 for _ in range(effect.repeat_duration_dice))
        interval *= effect.repeat_duration_unit
    item = item.model_copy(
        update={
            "active": not passed,
            "recovery_checks": item.recovery_checks + ((check,) if check is not None else ()),
            "due": None
            if passed
            else min(state.game_time + interval, item.next_care_due)
            if item.next_care_due is not None
            else state.game_time + interval,
            "recovery_due": None if passed else state.game_time + interval,
            "aftermath_until": state.game_time
            + (
                state.game_time - item.started
                if effect.neglect_progression
                else effect.aftermath_seconds
            )
            if passed and effect.aftermath_seconds
            else None,
        }
    )
    return save(state, item, command_id), passed


def advance(
    engine: ResourceEngine,
    state: ResourceState,
    command: Advance,
    *,
    rng: RandomSource,
) -> ResourceState:
    """Stop at every fright deadline inside the caller's existing transaction.

    Never step over other resource deadlines: ordinary Advance validation still
    runs for each segment. Failed checks may introduce more deadlines in the
    requested interval. The outer command retains one revision and receipt.
    """
    revision = state.revision
    for _ in range(10000):
        due = sorted(
            (i for i in effects(state) if i.active and i.due is not None and i.due <= command.to),
            key=lambda i: (i.due or 0, i.id),
        )
        if not due:
            break
        item = due[0]
        assert item.due is not None
        step_id = hashlib.sha256(json.dumps([command.id, item.id, item.due]).encode()).hexdigest()
        state = engine.apply(
            state,
            Advance(
                id="fright-clock:" + step_id,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                to=item.due,
            ),
            system=True,
        )
        state, _ = recover(
            state,
            actor_id=item.actor_id,
            trigger_id=item.trigger_id,
            command_id="fright-recover:" + step_id,
            rng=rng,
        )
    else:
        raise ValidationError("Fright recovery budget exceeded; advance a shorter interval")
    state = state.model_copy(update={"revision": revision})
    return engine.apply(state, command, system=True)
