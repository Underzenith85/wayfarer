"""Authoritative toxin, alcohol, overdose, and withdrawal reducers.

Rules are paraphrased from Campaigns, fourth printing, B437-B441.  Profiles and
delivery evidence are trusted scenario inputs.  Player commands contain only IDs.
"""

from __future__ import annotations

import hashlib
import math
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.hazard import RecoveryRestriction
from wayfarer.engine.rules.types.toxin import (
    DeliveryEvidence,
    DrugDependency,
    Intoxication,
    ToxinExposure,
    ToxinProfile,
    ToxinView,
)
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class ToxinCommand(Command):
    kind: Literal["expose", "resolve", "treat", "discover", "halt"]
    exposure_id: str


class ToxinResult(Record):
    exposure_id: str
    active: bool
    due: int
    resisted: bool = False
    hp_lost: int = 0
    fp_lost: int = 0
    check: CheckTrace | None = None


class DrinkCommand(Command):
    kind: Literal["drink", "stop-drinking", "sober"]
    drinks: int = Field(default=0, ge=0, le=100)


class IntoxicationResult(Record):
    level: Literal["sober", "tipsy", "drunk", "unconscious", "coma"]
    check: CheckTrace | None = None
    hallucinating: bool = False
    retching: bool = False
    hangover_until: int = 0


class WithdrawalCommand(Command):
    kind: Literal["begin-withdrawal", "resolve-withdrawal", "abandon-withdrawal"]
    dependency_id: str


class WithdrawalResult(Record):
    active: bool
    successes: int
    hp_lost: int = 0
    quirks: int = 0
    check: CheckTrace | None = None
    took_dose: bool = False


def _digest(command: Command) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _replay(
    state: ResourceState, command: Command, prefix: str, cls: type[Record]
) -> Record | None:
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Command ID reused")
    event = next(e for e in state.events if e.id == prefix + command.id)
    return cls.model_validate_json(event.kind)


def _commit(state: ResourceState, command: Command, prefix: str, result: Record) -> ResourceState:
    return ResourceState.model_validate(
        state.model_copy(
            update={
                "revision": command.expected_revision + 1,
                "receipts": state.receipts
                + (Receipt(command_id=command.id, digest=_digest(command)),),
                "events": state.events
                + (
                    ResourceEvent(
                        id=prefix + command.id,
                        at=state.game_time,
                        target_id=command.actor_id,
                        kind=result.model_dump_json(),
                    ),
                ),
            }
        )
    )


def _require_revision(state: ResourceState, command: Command) -> None:
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")


def _validate_delivery(vector: str, evidence: DeliveryEvidence) -> None:
    delivered = {
        "contact": evidence.touched_skin and not evidence.skin_covered and not evidence.sealed,
        "blood": evidence.mucous_or_open_wound and not evidence.sealed,
        "digestive": evidence.swallowed,
        "respiratory": evidence.inhaled and not evidence.sealed and not evidence.filter_lungs,
        "sense": evidence.relevant_sense and not evidence.protected_sense,
        "follow-up": evidence.penetrated_damage,
    }[vector]
    if not delivered:
        raise ValidationError("The recorded attack or exposure did not deliver this toxin")


def _dose_steps(dose: int) -> int:
    if dose & (dose - 1):
        raise ValidationError("Basic Set dosage scaling requires a power-of-two dose")
    return int(math.log2(dose))


def project_toxin(exposure: ToxinExposure, *, authorized_actor_id: str) -> ToxinView:
    """Redact identity until an authorized discovery procedure records the observer."""

    known = authorized_actor_id in exposure.discovered_by
    return ToxinView(
        id=exposure.id,
        actor_id=exposure.actor_id,
        substance_id=exposure.profile.id if known else None,
        active=exposure.active,
        due=exposure.due,
        symptoms_visible=bool(exposure.hp_lost or exposure.fp_lost or exposure.condition_until),
    )


def _apply_toxin_damage(
    state: ResourceState,
    command: ToxinCommand,
    exposure: ToxinExposure,
    profile: ToxinProfile,
    *,
    rng: RandomSource,
) -> tuple[ResourceState, int, int]:
    hp_damage = exposure.dose * (
        sum(rng.randbelow(6) + 1 for _ in range(profile.hp_dice)) + profile.hp_add
    )
    fp_damage = exposure.dose * (
        sum(rng.randbelow(6) + 1 for _ in range(profile.fp_dice)) + profile.fp_add
    )
    internal = hashlib.sha256(command.id.encode()).hexdigest()
    hp_lost = fp_lost = 0
    if hp_damage:
        state, injury = apply_injury(
            state,
            Wound(
                id="toxin-hp:" + internal,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                basic_damage=hp_damage,
                resistance=0,
                damage_type="tox",
                injury_source="internal",
            ),
            ht=exposure.ht,
            rng=rng,
            system=True,
        )
        hp_lost = injury.injury
    if fp_damage:
        state, fatigue = apply_fatigue(
            state,
            FatigueCost(
                id="toxin-fp:" + internal,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                amount=fp_damage,
            ),
            ht=exposure.ht,
            rng=rng,
            system=True,
        )
        fp_lost = fatigue.fp_lost
    return state, hp_lost, fp_lost


def _apply_overdose(
    state: ResourceState,
    exposure: ToxinExposure,
    check: CheckTrace | None,
) -> tuple[ResourceState, int]:
    overdose = (
        exposure.profile.depressant
        and exposure.dose >= 2
        and check is not None
        and check.outcome is Outcome.CRITICAL_FAILURE
    )
    if not overdose or check is None:
        return state, 0
    hp = next(p for p in state.pools if p.id == "hp:" + exposure.actor_id)
    assert hp.injury is not None
    hp = hp.model_copy(update={"injury": hp.injury.model_copy(update={"unconscious": True})})
    return state.model_copy(
        update={"pools": tuple(hp if p.id == hp.id else p for p in state.pools)}
    ), state.game_time + max(1, -check.margin) * 3600


def apply_toxin(
    state: ResourceState,
    command: ToxinCommand,
    *,
    rng: RandomSource,
    system: bool = False,
    profile: ToxinProfile | None = None,
    evidence: DeliveryEvidence | None = None,
    ht: int | None = None,
    dose: int = 1,
    treatment_bonus: int = 0,
    discoverer_id: str | None = None,
) -> tuple[ResourceState, ToxinResult]:
    if not system:
        raise ValidationError("Toxin resolution requires authoritative scenario context")
    replay = _replay(state, command, "toxin:", ToxinResult)
    if replay is not None:
        return state, ToxinResult.model_validate(replay)
    _require_revision(state, command)
    exposure = next((t for t in state.toxins if t.id == command.exposure_id), None)
    if command.kind == "expose":
        if exposure is not None:
            raise ConflictError("Toxin exposure ID already exists")
        if profile is None or evidence is None or ht is None:
            raise ValidationError("Exposure requires a bound profile, delivery evidence, and HT")
        _validate_delivery(profile.vector, evidence)
        steps = _dose_steps(dose)
        exposure = ToxinExposure(
            id=command.exposure_id,
            actor_id=command.actor_id,
            profile=profile,
            identity_digest=hashlib.sha256(profile.id.encode()).hexdigest(),
            started=state.game_time,
            due=state.game_time + max(0, profile.delay // dose),
            remaining=profile.cycles,
            ht=ht,
            dose=dose,
            discovered_by=(),
        )
        # Record each dose independently; this avoids merging away a resisted or
        # delayed dose and makes repeated exposure ordering explicit.
        state = state.model_copy(update={"toxins": state.toxins + (exposure,)})
    else:
        if exposure is None or exposure.actor_id != command.actor_id:
            raise ValidationError("Unknown toxin exposure")
        if command.kind == "discover":
            if discoverer_id is None or not (
                exposure.hp_lost or exposure.fp_lost or exposure.condition_until
            ):
                raise ValidationError(
                    "Discovery requires an authorized procedure and observable symptoms"
                )
            exposure = exposure.model_copy(
                update={
                    "discovered_by": tuple(sorted(set(exposure.discovered_by) | {discoverer_id}))
                }
            )
        elif command.kind == "treat":
            if exposure.profile.treatment_owner == "none" or treatment_bonus < 1:
                raise ValidationError("Treatment does not match this toxin profile")
            exposure = exposure.model_copy(update={"treatment_bonus": treatment_bonus})
        elif command.kind == "halt":
            if exposure.profile.treatment_owner != "antidote" or discoverer_id is None:
                raise ValidationError("Only the bound antidote procedure can halt this toxin")
            exposure = exposure.model_copy(update={"active": False, "halted_by": discoverer_id})
        else:
            if not exposure.active or state.game_time != exposure.due:
                raise ConflictError("Resolve toxin at its recorded deadline")
            profile = exposure.profile
            steps = _dose_steps(exposure.dose)
            check = (
                success_roll(
                    profile.profile_id,
                    max(
                        1,
                        exposure.ht
                        + profile.resistance_modifier
                        - 2 * steps
                        + exposure.treatment_bonus,
                    ),
                    rng=rng,
                )
                if profile.resistible
                else None
            )
            resisted = check is not None and check.outcome.succeeded
            hp_lost = fp_lost = 0
            if not resisted:
                state, hp_lost, fp_lost = _apply_toxin_damage(
                    state, command, exposure, profile, rng=rng
                )
            remaining = 0 if resisted else exposure.remaining - 1
            duration = profile.condition_seconds
            if check is not None and not resisted:
                duration += profile.duration_per_margin * max(1, -check.margin)
            state, overdose_until = _apply_overdose(state, exposure, check)
            exposure = exposure.model_copy(
                update={
                    "active": remaining > 0,
                    "remaining": remaining,
                    "cycle": exposure.cycle + 1,
                    "due": exposure.due + max(1, profile.interval // exposure.dose),
                    "hp_lost": exposure.hp_lost + hp_lost,
                    "fp_lost": exposure.fp_lost + fp_lost,
                    "condition_until": max(exposure.condition_until, state.game_time + duration),
                    "overdose_until": max(exposure.overdose_until, overdose_until),
                }
            )
            result = ToxinResult(
                exposure_id=exposure.id,
                active=exposure.active,
                due=exposure.due,
                resisted=resisted,
                hp_lost=hp_lost,
                fp_lost=fp_lost,
                check=check,
            )
            state = state.model_copy(
                update={
                    "toxins": tuple(exposure if t.id == exposure.id else t for t in state.toxins)
                }
            )
            state = _commit(state, command, "toxin:", result)
            return state, result
        state = state.model_copy(
            update={"toxins": tuple(exposure if t.id == exposure.id else t for t in state.toxins)}
        )
    result = ToxinResult(exposure_id=exposure.id, active=exposure.active, due=exposure.due)
    state = _commit(state, command, "toxin:", result)
    return state, result


type IntoxicationLevel = Literal["sober", "tipsy", "drunk", "unconscious", "coma"]
_LEVELS: tuple[IntoxicationLevel, ...] = (
    "sober",
    "tipsy",
    "drunk",
    "unconscious",
    "coma",
)


def _failed_drinking_check(
    item: Intoxication,
    check: CheckTrace,
    *,
    ht: int,
    rng: RandomSource,
) -> tuple[IntoxicationLevel, bool, bool]:
    drop = 2 if check.outcome is Outcome.CRITICAL_FAILURE else 1
    level = _LEVELS[min(4, _LEVELS.index(item.level) + drop)]
    hallucinating = retching = False
    if level == "drunk":
        pink = success_roll("gurps-basic-set-4e-2004", ht + 4, rng=rng)
        hallucinating = not pink.outcome.succeeded
    if level in ("unconscious", "coma"):
        purge = success_roll("gurps-basic-set-4e-2004", ht, rng=rng)
        if purge.outcome.succeeded:
            retching, level = True, item.level
        elif purge.outcome is Outcome.CRITICAL_FAILURE:
            retching = True
    return level, hallucinating, retching


def apply_drinking(
    state: ResourceState,
    command: DrinkCommand,
    *,
    rng: RandomSource,
    system: bool = False,
    st: int,
    ht: int,
    carousing: int = 0,
    recently_ate: bool = False,
    empty_stomach: bool = False,
    tolerance: int = 0,
) -> tuple[ResourceState, IntoxicationResult]:
    if not system:
        raise ValidationError("Intoxication requires authoritative character context")
    replay = _replay(state, command, "intoxication:", IntoxicationResult)
    if replay is not None:
        return state, IntoxicationResult.model_validate(replay)
    _require_revision(state, command)
    item = next((i for i in state.intoxications if i.actor_id == command.actor_id), None)
    item = item or Intoxication(actor_id=command.actor_id, window_started=state.game_time)
    check = None
    hallucinating = retching = False
    if command.kind == "drink":
        if command.drinks < 1 or item.stopped_at is not None:
            raise ValidationError("A drinking command needs drinks in an active session")
        if state.game_time >= item.window_started + 3600:
            item = item.model_copy(update={"window_started": state.game_time, "drinks": 0})
        drinks = item.drinks + command.drinks
        total = item.total_session_drinks + command.drinks
        level = item.level
        if drinks > st // 4:
            target = (
                max(ht, carousing)
                - (drinks - st // 4)
                + (1 if recently_ate else 0)
                - (2 if empty_stomach else 0)
                + tolerance
            )
            check = success_roll("gurps-basic-set-4e-2004", max(1, target), rng=rng)
            if not check.outcome.succeeded:
                level, hallucinating, retching = _failed_drinking_check(item, check, ht=ht, rng=rng)
        item = item.model_copy(
            update={"drinks": drinks, "total_session_drinks": total, "level": level}
        )
    elif command.kind == "stop-drinking":
        if item.stopped_at is not None or item.total_session_drinks == 0:
            raise ConflictError("Drinking session already stopped or empty")
        hours = max(1, item.total_session_drinks // 2)
        penalty = -4 if item.level == "unconscious" else -2 if item.level == "drunk" else 0
        hangover = success_roll("gurps-basic-set-4e-2004", max(1, ht + penalty), rng=rng)
        hangover_due = (
            state.game_time + sum(rng.randbelow(6) + 1 for _ in range(1)) * 3600
            if not hangover.outcome.succeeded
            else None
        )
        item = item.model_copy(
            update={
                "stopped_at": state.game_time,
                "sober_due": state.game_time + hours * 3600,
                "hangover_due": hangover_due,
            }
        )
    else:
        if item.sober_due is None or state.game_time != item.sober_due:
            raise ConflictError("Sobering requires its recorded deadline")
        if item.level == "coma":
            raise ValidationError("Recovery from an alcohol coma requires medical care")
        check = success_roll("gurps-basic-set-4e-2004", ht, rng=rng)
        level = _LEVELS[max(0, _LEVELS.index(item.level) - int(check.outcome.succeeded))]
        hours = max(1, item.total_session_drinks // 2)
        item = item.model_copy(
            update={
                "level": level,
                "sober_due": None if level == "sober" else state.game_time + hours * 3600,
            }
        )
    if item.hangover_due is not None and state.game_time >= item.hangover_due:
        margin = max(1, -(check.margin if check is not None else -1))
        item = item.model_copy(
            update={"hangover_until": state.game_time + margin * 3600, "hangover_due": None}
        )
    state = state.model_copy(
        update={
            "intoxications": tuple(i for i in state.intoxications if i.actor_id != item.actor_id)
            + (item,)
        }
    )
    result = IntoxicationResult(
        level=item.level,
        check=check,
        hallucinating=hallucinating,
        retching=retching,
        hangover_until=item.hangover_until,
    )
    return _commit(state, command, "intoxication:", result), result


def apply_withdrawal(
    state: ResourceState,
    command: WithdrawalCommand,
    *,
    rng: RandomSource,
    system: bool = False,
    substance_id: str | None = None,
    dependency_kind: Literal["physiological", "psychological"] | None = None,
    ht: int,
    will: int,
    available: bool = False,
) -> tuple[ResourceState, WithdrawalResult]:
    if not system:
        raise ValidationError("Withdrawal requires authoritative dependency context")
    replay = _replay(state, command, "withdrawal:", WithdrawalResult)
    if replay is not None:
        return state, WithdrawalResult.model_validate(replay)
    _require_revision(state, command)
    dependency = next((d for d in state.dependencies if d.id == command.dependency_id), None)
    check = None
    hp_lost = 0
    took_dose = False
    if command.kind == "begin-withdrawal":
        if dependency is not None or substance_id is None or dependency_kind is None:
            raise ValidationError("Withdrawal requires a new bound dependency")
        dependency = DrugDependency(
            id=command.dependency_id,
            actor_id=command.actor_id,
            identity_digest=hashlib.sha256(substance_id.encode()).hexdigest(),
            kind=dependency_kind,
            started=state.game_time,
            due=state.game_time + 86400,
        )
        state = state.model_copy(update={"dependencies": state.dependencies + (dependency,)})
    else:
        if dependency is None or dependency.actor_id != command.actor_id or not dependency.active:
            raise ValidationError("Unknown active withdrawal")
        if command.kind == "abandon-withdrawal":
            dependency = dependency.model_copy(update={"active": False})
            state = state.model_copy(
                update={
                    "illnesses": tuple(
                        i.model_copy(update={"active": False}) if i.id == dependency.id else i
                        for i in state.illnesses
                    )
                }
            )
        else:
            if state.game_time != dependency.due:
                raise ConflictError("Withdrawal rolls occur at the recorded daily deadline")
            target = min(13, ht if dependency.kind == "physiological" else will)
            check = success_roll("gurps-basic-set-4e-2004", target, rng=rng)
            successes = dependency.successes
            quirks = dependency.quirks
            if check.outcome.succeeded:
                successes += 1
            elif available:
                successes, took_dose = 0, True
            elif dependency.kind == "physiological":
                state, injury = apply_injury(
                    state,
                    Wound(
                        id="withdrawal-hp:" + hashlib.sha256(command.id.encode()).hexdigest(),
                        actor_id=command.actor_id,
                        expected_revision=state.revision,
                        basic_damage=1,
                        resistance=0,
                        damage_type="tox",
                        injury_source="internal",
                    ),
                    ht=ht,
                    rng=rng,
                    system=True,
                )
                hp_lost = injury.injury
                old = next((i for i in state.illnesses if i.id == dependency.id), None)
                restriction = RecoveryRestriction(
                    id=dependency.id,
                    actor_id=command.actor_id,
                    hp_debt=(old.hp_debt if old else 0) + hp_lost,
                    blocks_natural_healing=True,
                )
                state = state.model_copy(
                    update={
                        "illnesses": tuple(i for i in state.illnesses if i.id != dependency.id)
                        + (restriction,)
                    }
                )
            else:
                quirks += 1
            completed = successes >= 14
            dependency = dependency.model_copy(
                update={
                    "successes": successes,
                    "quirks": quirks,
                    "active": not completed,
                    "due": state.game_time + 86400,
                }
            )
            if completed:
                state = state.model_copy(
                    update={
                        "illnesses": tuple(
                            i.model_copy(update={"active": False}) if i.id == dependency.id else i
                            for i in state.illnesses
                        )
                    }
                )
        state = state.model_copy(
            update={
                "dependencies": tuple(
                    dependency if d.id == dependency.id else d for d in state.dependencies
                )
            }
        )
    result = WithdrawalResult(
        active=dependency.active,
        successes=dependency.successes,
        hp_lost=hp_lost,
        quirks=dependency.quirks,
        check=check,
        took_dose=took_dose,
    )
    return _commit(state, command, "withdrawal:", result), result


def overdose_profile(
    id: str, *, vector: Literal["digestive", "follow-up"] = "digestive"
) -> ToxinProfile:
    """B441's fixed 24-cycle toxic consequence; the triggering drug supplies HT modifier."""

    return ToxinProfile(
        id=id,
        vector=vector,
        interval=900,
        cycles=24,
        hp_add=1,
        depressant=True,
        treatment_owner="physician",
        reference="B441",
    )
