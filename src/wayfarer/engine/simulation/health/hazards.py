"""Authoritative scheduled hazards on the existing resource clock and ledger."""

from __future__ import annotations

import hashlib
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.hazard import CombatHazardTurn, HazardSchedule, RecoveryRestriction
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.fright import apply_effect
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.physical_traits import physical_traits
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class HazardCommand(Command):
    kind: Literal["enter", "resolve", "leave"]
    hazard_id: str


class HazardResult(Record):
    schedule_id: str
    active: bool
    due: int
    hp_lost: int = 0
    fp_lost: int = 0
    check: CheckTrace | None = None
    consciousness: CheckTrace | None = None
    conditions: tuple[str, ...] = ()
    radiation_dose: int = 0


def _protected(schedule: HazardSchedule) -> bool:
    spec, protection = schedule.spec, schedule.spec.protection
    if protection is None:
        return False
    if spec.kind == "cold" and spec.variant == "thermal-shock":
        return protection.sealed
    if spec.kind == "heat" and spec.variant == "intense-heat":
        return protection.insulated
    if spec.kind == "acid" or (spec.kind == "atmosphere" and spec.damage_type == "cor"):
        return protection.sealed
    if spec.kind == "atmosphere":
        return protection.breathing_supply
    if spec.kind == "electricity":
        return protection.insulated
    if spec.kind == "pressure":
        return protection.pressure_support >= (3 if spec.variant == "crushing" else 2)
    if spec.kind == "vacuum":
        return protection.vacuum_support or (
            spec.variant == "suffocating" and protection.breathing_supply
        )
    if spec.kind == "seasickness":
        return protection.motion_stabilized
    return False


def _radiation_modifier(dose: int) -> int:
    if dose <= 40:
        return 0
    if dose <= 80:
        return -1
    if dose <= 160:
        return -3
    if dose <= 800:
        return -4
    return -5


def _radiation_effect(dose: int, outcome: Outcome) -> str:
    rows = (
        (10, ("none", "none", "A", "B")),
        (20, ("none", "A", "B", "C")),
        (40, ("A", "B", "C", "D")),
        (80, ("A", "B", "C", "D")),
        (160, ("A", "B", "C", "D")),
        (800, ("A", "B", "C", "D")),
        (4000, ("C", "D", "E", "E")),
        (1000000000, ("D", "E", "E", "E")),
    )
    index = {
        Outcome.CRITICAL_SUCCESS: 0,
        Outcome.SUCCESS: 1,
        Outcome.FAILURE: 2,
        Outcome.CRITICAL_FAILURE: 3,
    }[outcome]
    return next(effects[index] for ceiling, effects in rows if dose <= ceiling)


def _set_injury_condition(
    state: ResourceState, actor_id: str, *, ht: int, stunned: bool = False, unconscious: bool = False,
    heart_attack: bool = False, dead: bool = False,
) -> ResourceState:
    hp = next(p for p in state.pools if p.id == "hp:" + actor_id)
    fp = next(p for p in state.pools if p.id == "fp:" + actor_id)
    assert hp.injury is not None and fp.fatigue is not None
    hp = hp.model_copy(
        update={
            "injury": hp.injury.model_copy(
                update={
                    "stunned": hp.injury.stunned or stunned,
                    "unconscious": hp.injury.unconscious or unconscious or dead,
                    "dead": hp.injury.dead or dead,
                }
            )
        }
    )
    fp = fp.model_copy(
        update={
            "fatigue": fp.fatigue.model_copy(
                update={
                    "unconscious": fp.fatigue.unconscious or unconscious or heart_attack,
                    "heart_attack": fp.fatigue.heart_attack or heart_attack,
                    "heart_attack_deadline": (
                        state.game_time + max(60, 60 * (ht // 3))
                        if heart_attack and fp.fatigue.heart_attack_deadline is None
                        else fp.fatigue.heart_attack_deadline
                    ),
                }
            )
        }
    )
    return state.model_copy(
        update={"pools": tuple(hp if p.id == hp.id else fp if p.id == fp.id else p for p in state.pools)}
    )


def _hazard_damage(
    schedule: HazardSchedule,
    check: CheckTrace | None,
    rng: RandomSource,
    *,
    checking: bool,
    protected: bool,
) -> int:
    spec = schedule.spec
    damage = 0
    if checking and not protected and (check is None or not check.outcome.succeeded):
        damage = max(
            0, sum(rng.randbelow(6) + 1 for _ in range(spec.damage_dice)) + spec.damage_add
        )
        if spec.damage_from_margin and check is not None:
            damage = max(0, -check.margin)
        if spec.kind == "heat" and check is not None and check.outcome is Outcome.CRITICAL_FAILURE:
            damage = rng.randbelow(6) + 1
    if spec.kind == "cold" and spec.variant == "thermal-shock" and check is not None:
        damage = 1 if check.outcome.succeeded else max(1, -check.margin)
    if protected:
        return 0
    if spec.kind == "vacuum" and schedule.cycle:
        return 1
    return damage


def _damage_type(schedule: HazardSchedule) -> Literal["burn", "cor", "cr", "tox"]:
    if schedule.spec.damage_type is not None:
        return schedule.spec.damage_type
    if schedule.spec.kind == "fire":
        return "burn"
    if schedule.spec.kind == "pressure":
        return "cr"
    return "tox"


def _extended_conditions(
    state: ResourceState,
    schedule: HazardSchedule,
    check: CheckTrace | None,
    hp_lost: int,
) -> tuple[ResourceState, HazardSchedule, list[str]]:
    spec, conditions = schedule.spec, []
    if check is not None and not check.outcome.succeeded:
        if spec.kind == "electricity":
            bodywide = spec.variant == "lethal"
            conditions.append("unconscious" if bodywide else "stunned")
            heart_attack = spec.critical_effect == "heart-attack" and (
                check.outcome is Outcome.CRITICAL_FAILURE or check.margin <= -5
            )
            state = _set_injury_condition(
                state,
                schedule.actor_id,
                ht=schedule.ht,
                stunned=not bodywide,
                unconscious=bodywide,
                heart_attack=heart_attack,
            )
            if heart_attack:
                conditions.append("heart-attack")
        elif spec.kind == "acceleration" and check.outcome is Outcome.CRITICAL_FAILURE:
            state = _set_injury_condition(
                state, schedule.actor_id, ht=schedule.ht, unconscious=True
            )
            conditions.append("unconscious")
        elif (
            spec.kind == "pressure"
            and spec.critical_effect == "death"
            and check.outcome is Outcome.CRITICAL_FAILURE
        ):
            state = _set_injury_condition(state, schedule.actor_id, ht=schedule.ht, dead=True)
            conditions.append("dead")
    if spec.kind == "radiation":
        pf = spec.protection.radiation_pf if spec.protection else 1
        effective = spec.radiation_rads // pf
        dose = schedule.radiation_dose + effective
        effect = _radiation_effect(dose, check.outcome) if check is not None else "none"
        if effect != "none":
            conditions.append("radiation-" + effect.lower())
        schedule = schedule.model_copy(
            update={
                "radiation_dose": dose,
                "radiation_original": schedule.radiation_original + effective,
                "radiation_received_at": schedule.radiation_received_at
                if schedule.radiation_received_at is not None
                else state.game_time,
                "radiation_checked_at": state.game_time
                if check is not None
                else schedule.radiation_checked_at,
            }
        )
    if spec.kind == "atmosphere" and spec.damage_type == "cor" and hp_lost:
        accumulated = schedule.symptoms + hp_lost
        atmospheric = set(schedule.conditions)
        if accumulated * 3 >= schedule.full_hp:
            atmospheric.add("coughing")
        if accumulated * 3 >= schedule.full_hp * 2:
            atmospheric.add("blindness")
        conditions.extend(sorted(atmospheric - set(schedule.conditions)))
        schedule = schedule.model_copy(update={"conditions": tuple(sorted(atmospheric))})
    return state, schedule, conditions


def _validate_deadline(
    state: ResourceState,
    schedule: HazardSchedule,
    combat_turn: CombatHazardTurn | None,
) -> None:
    if schedule.combat_turn is not None:
        if combat_turn != schedule.combat_turn or state.game_time < schedule.due:
            raise ConflictError("Resolve suffocation on its recorded combat turn")
        return
    if state.game_time != schedule.due:
        raise ConflictError("Resolve hazards at their shared-clock deadline")


def apply_hazard(
    state: ResourceState,
    command: HazardCommand,
    schedule: HazardSchedule,
    *,
    rng: RandomSource,
    system: bool = False,
    combat_turn: CombatHazardTurn | None = None,
) -> tuple[ResourceState, HazardResult]:
    if not system:
        raise ValidationError("Hazards require authoritative scenario context")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt:
        if receipt.digest != digest:
            raise ConflictError("Hazard command ID reused")
        event = next(e for e in state.events if e.id == "hazard:" + command.id)
        return state, HazardResult.model_validate_json(event.kind)
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    if schedule.actor_id != command.actor_id or schedule.spec.id != command.hazard_id:
        raise ValidationError("Hazard command does not match the bound exposure")
    hp = next(p for p in state.pools if p.id == "hp:" + command.actor_id)
    fp = next(p for p in state.pools if p.id == "fp:" + command.actor_id)
    if (
        hp.injury is None
        or fp.fatigue is None
        or hp.injury.profile_id != schedule.spec.profile_id
        or fp.fatigue.profile_id != schedule.spec.profile_id
    ):
        raise ValidationError("Hazard requires matching explicit Basic Set pools")
    existing = next((h for h in state.hazards if h.id == schedule.id), None)
    check = None
    consciousness = None
    hp_lost = fp_lost = 0
    conditions: list[str] = []
    if command.kind == "enter":
        if existing is not None:
            raise ConflictError("Exposure already recorded; resume its schedule")
        if schedule.spec.cycles_dice:
            schedule = schedule.model_copy(
                update={
                    "remaining": sum(
                        rng.randbelow(6) + 1 for _ in range(schedule.spec.cycles_dice)
                    )
                }
            )
    else:
        if existing is None or (not existing.active and command.kind != "leave"):
            raise ConflictError("Exposure is not active")
        schedule = existing
        if command.kind == "leave":
            if schedule.combat_turn is None and schedule.due <= state.game_time:
                raise ConflictError("Resolve due exposure before leaving")
            if schedule.spec.kind in ("poison", "disease", "drowning"):
                raise ValidationError("Ending this condition requires its treatment procedure")
            schedule = schedule.model_copy(update={"active": False})
            state = state.model_copy(
                update={
                    "illnesses": tuple(
                        i.model_copy(update={"active": False}) if i.id == schedule.id else i
                        for i in state.illnesses
                    )
                }
            )
        else:
            _validate_deadline(state, schedule, combat_turn)
            spec = schedule.spec
            protected = _protected(schedule)
            checking = (
                spec.kind != "drowning"
                or fp.fatigue.unconscious
                or schedule.next_check_at is None
                or state.game_time >= schedule.next_check_at
            ) and (
                spec.kind != "radiation"
                or schedule.radiation_checked_at is None
                or state.game_time >= schedule.radiation_checked_at + 86400
            )
            if (
                checking
                and spec.resistible
                and not protected
                and schedule.stage != "rescued"
                and not (spec.kind == "drowning" and fp.fatigue.unconscious)
            ):
                check = (
                    success_roll(
                        spec.profile_id,
                        schedule.swimming
                        if spec.kind == "drowning"
                        else schedule.ht + physical_traits(state, schedule.actor_id).fitness,
                        modifiers=check_modifiers(state, schedule.actor_id, "ht"),
                        rng=rng,
                    )
                    if spec.kind == "drowning"
                    else success_roll(
                        spec.profile_id,
                        max(
                            1,
                            max(
                                schedule.ht
                                + physical_traits(state, schedule.actor_id).fitness
                                + (
                                    _radiation_modifier(
                                        schedule.radiation_dose
                                        + max(
                                            0,
                                            spec.radiation_rads
                                            // (spec.protection.radiation_pf if spec.protection else 1),
                                        )
                                    )
                                    if spec.kind == "radiation"
                                    else 0
                                ),
                                schedule.survival or 0,
                            )
                            + spec.resistance_modifier
                            + schedule.treatment_bonus
                            + (
                                schedule.resistance_bonus
                                if schedule.stage == "exposure" or spec.kind in ("heat", "cold")
                                else 0
                            ),
                        ),
                        check_modifiers(state, schedule.actor_id, "ht"),
                        rng=rng,
                    )
                )
            damage = _hazard_damage(
                schedule, check, rng, checking=checking, protected=protected
            )
            initial_disease = spec.kind == "disease" and schedule.stage == "exposure"
            if initial_disease or schedule.stage == "rescued":
                damage = 0
            internal = hashlib.sha256(command.id.encode()).hexdigest()
            if damage and spec.kind in (
                "cold",
                "heat",
                "suffocation",
                "drowning",
                "acceleration",
                "vacuum",
            ) and not (spec.kind == "vacuum" and spec.damage_dice and schedule.cycle == 0):
                state, fatigue = apply_fatigue(
                    state,
                    FatigueCost(
                        id="hazard-fp:" + internal,
                        actor_id=command.actor_id,
                        expected_revision=state.revision,
                        amount=damage,
                    ),
                    ht=schedule.ht,
                    rng=rng,
                    system=True,
                )
                fp_lost, hp_lost = fatigue.fp_lost, fatigue.hp_lost
            elif damage:
                state, injury = apply_injury(
                    state,
                    Wound(
                        id="hazard-hp:" + internal,
                        actor_id=command.actor_id,
                        expected_revision=state.revision,
                        basic_damage=damage,
                        resistance=schedule.resistance if spec.kind == "fire" else 0,
                        damage_type=_damage_type(schedule),
                        injury_source="area" if spec.kind in ("fire", "pressure") else "internal",
                    ),
                    ht=schedule.ht,
                    rng=rng,
                    system=True,
                )
                hp_lost = injury.injury
            if spec.kind == "electricity" and spec.damage_dice and check is None:
                check = success_roll(
                    spec.profile_id,
                    max(1, schedule.ht - hp_lost // 2),
                    check_modifiers(state, schedule.actor_id, "ht"),
                    rng=rng,
                )
            state, schedule, extended = _extended_conditions(state, schedule, check, hp_lost)
            conditions.extend(extended)
            if spec.kind in ("heat", "cold", "disease") and (hp_lost or fp_lost):
                old = next((i for i in state.illnesses if i.id == schedule.id), None)
                restriction = RecoveryRestriction(
                    id=schedule.id,
                    actor_id=command.actor_id,
                    hp_debt=hp_lost + (old.hp_debt if old else 0),
                    fp_debt=fp_lost + (old.fp_debt if old else 0),
                    blocks_rest=True,
                    blocks_physician_healing=spec.kind != "disease",
                )
                state = state.model_copy(
                    update={
                        "illnesses": tuple(i for i in state.illnesses if i.id != schedule.id)
                        + (restriction,)
                    }
                )
            if checking and not initial_disease and (check is None or not check.outcome.succeeded):
                duration = spec.affliction_seconds + spec.duration_per_margin * (
                    max(1, -check.margin) if check else 1
                )
                schedule = schedule.model_copy(
                    update={
                        "symptoms": schedule.symptoms + hp_lost,
                        "affliction_until": max(
                            schedule.affliction_until, state.game_time + duration
                        )
                        if spec.affliction != "none"
                        else schedule.affliction_until,
                    }
                )
                if spec.affliction in ("retching", "seizure") and duration:
                    conditions.append(spec.affliction)
                    state = apply_effect(
                        state,
                        FrightEffect(
                            table_total=4, condition=spec.affliction, duration_seconds=duration
                        ),
                        actor_id=schedule.actor_id,
                        trigger_id=schedule.id,
                        command_id="toxin:" + internal,
                        ht=schedule.ht,
                        will=schedule.will,
                        modified_will=schedule.will,
                        rng=rng,
                    )
            remaining = 0 if protected else schedule.remaining - (0 if initial_disease else 1)
            successes = schedule.successes
            if spec.kind in ("poison", "disease") and check is not None and check.outcome.succeeded:
                successes += 1
                if initial_disease or successes >= spec.recovery_successes:
                    remaining = 0
                    state = state.model_copy(
                        update={
                            "illnesses": tuple(
                                i.model_copy(update={"active": False}) if i.id == schedule.id else i
                                for i in state.illnesses
                            )
                        }
                    )
            if spec.kind in ("suffocation", "drowning", "vacuum"):
                latest_fp = next(p for p in state.pools if p.id == fp.id)
                if (
                    latest_fp.current <= 0
                    and latest_fp.fatigue is not None
                    and schedule.stage != "rescued"
                ):
                    consciousness = success_roll(
                        spec.profile_id,
                        schedule.will,
                        check_modifiers(state, schedule.actor_id, "will", defensive=True),
                        rng=rng,
                    )
                    if not consciousness.outcome.succeeded:
                        latest_fp = latest_fp.model_copy(
                            update={
                                "fatigue": latest_fp.fatigue.model_copy(
                                    update={"unconscious": True}
                                )
                            }
                        )
                        state = state.model_copy(
                            update={
                                "pools": tuple(
                                    latest_fp if p.id == fp.id else p for p in state.pools
                                )
                            }
                        )
                if (
                    schedule.no_air_since is not None
                    and state.game_time >= schedule.no_air_since + 240
                ):
                    latest_hp = next(p for p in state.pools if p.id == hp.id)
                    assert latest_hp.injury is not None
                    latest_hp = latest_hp.model_copy(
                        update={
                            "injury": latest_hp.injury.model_copy(
                                update={"dead": True, "unconscious": True}
                            )
                        }
                    )
                    state = state.model_copy(
                        update={
                            "pools": tuple(latest_hp if p.id == hp.id else p for p in state.pools)
                        }
                    )
                    remaining = 0
            interval = max(1, spec.delay) if initial_disease else spec.interval
            stage = "cycles" if initial_disease else schedule.stage
            if remaining == 0 and spec.kind == "disease":
                state = state.model_copy(
                    update={
                        "illnesses": tuple(
                            i.model_copy(update={"active": False}) if i.id == schedule.id else i
                            for i in state.illnesses
                        )
                    }
                )
            if initial_disease and check is not None and sum(check.dice) <= 4:
                schedule = schedule.model_copy(update={"immune": True})
            latest_fp = next(p for p in state.pools if p.id == fp.id)
            if (
                spec.kind == "drowning"
                and latest_fp.fatigue is not None
                and latest_fp.fatigue.unconscious
            ):
                interval = 1
                schedule = schedule.model_copy(
                    update={
                        "no_air_since": schedule.no_air_since
                        if schedule.no_air_since is not None
                        else state.game_time
                    }
                )
            if spec.kind == "drowning" and check is not None:
                if check.outcome.succeeded:
                    stage = (
                        "swimming" if schedule.stage in ("recovering", "swimming") else "recovering"
                    )
                    interval = 300 if stage == "swimming" else 60
                else:
                    stage, interval = "struggling", 5
            next_check_at = schedule.due + interval if checking else schedule.next_check_at
            if spec.kind == "drowning" and (latest_fp.current <= 0 or stage == "rescued"):
                interval = 1
            elif not checking and next_check_at is not None:
                interval = next_check_at - state.game_time
            schedule = schedule.model_copy(
                update={
                    "remaining": remaining,
                    "cycle": schedule.cycle + 1,
                    "successes": successes,
                    "due": (state.game_time if schedule.combat_turn else schedule.due) + interval,
                    "combat_turn": schedule.combat_turn.model_copy(
                        update={"round": schedule.combat_turn.round + 1}
                    )
                    if schedule.combat_turn
                    else None,
                    "stage": stage,
                    "next_check_at": next_check_at,
                    "active": remaining > 0,
                }
            )
    result = HazardResult(
        schedule_id=schedule.id,
        active=schedule.active,
        due=schedule.due,
        hp_lost=hp_lost,
        fp_lost=fp_lost,
        check=check,
        consciousness=consciousness,
        conditions=tuple(conditions),
        radiation_dose=schedule.radiation_dose,
    )
    state = state.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "hazards": tuple(h for h in state.hazards if h.id != schedule.id) + (schedule,),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id="hazard:" + command.id,
                    at=state.game_time,
                    target_id=command.actor_id,
                    kind=result.model_dump_json(),
                ),
            ),
        }
    )
    return ResourceState.model_validate(state), result
