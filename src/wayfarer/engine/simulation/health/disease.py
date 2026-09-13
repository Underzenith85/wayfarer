"""Disease, infection and aging reducers for the Basic Set profile (#519)."""

from __future__ import annotations

import hashlib
from fractions import Fraction
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.disease import (
    CONTACT_MODIFIERS,
    YEAR_SECONDS,
    AgingRules,
    AgingSchedule,
    ContactExposure,
    DiseaseEpisode,
    DiseaseProfile,
    PermanentAttributeLoss,
    PermanentChange,
    WoundInfectionRisk,
)
from wayfarer.engine.rules.types.hazard import RecoveryRestriction
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

DISEASE_PREFIX = "disease-runtime:"
INFECTION_PREFIX = "infection-risk-runtime:"
AGING_PREFIX = "aging-runtime:"
PERMANENT_PREFIX = "permanent-health-change:"
RESULT_PREFIX = "disease-aging-result:"


class ExposeDisease(Command):
    kind: Literal["expose-disease"] = "expose-disease"
    relationship_id: str


class ResolveDisease(Command):
    kind: Literal["resolve-disease"] = "resolve-disease"
    episode_id: str


class TreatDisease(Command):
    kind: Literal["treat-disease"] = "treat-disease"
    episode_id: str
    care_id: str


class DiagnoseDisease(Command):
    kind: Literal["diagnose-disease"] = "diagnose-disease"
    episode_id: str


class RecordInfectionRisk(Command):
    kind: Literal["record-infection-risk"] = "record-infection-risk"
    risk_id: str


class ResolveInfectionRisk(Command):
    kind: Literal["resolve-infection-risk"] = "resolve-infection-risk"
    risk_id: str


class EnrollAging(Command):
    kind: Literal["enroll-aging"] = "enroll-aging"
    schedule_id: str


class ResolveAging(Command):
    kind: Literal["resolve-aging"] = "resolve-aging"
    schedule_id: str


class ApprovePermanentChange(Command):
    kind: Literal["approve-permanent-health-change"] = "approve-permanent-health-change"
    change_id: str
    losses: PermanentAttributeLoss
    approved_build_revision: str = Field(min_length=1)


HealthCommand = (
    ExposeDisease
    | ResolveDisease
    | TreatDisease
    | DiagnoseDisease
    | RecordInfectionRisk
    | ResolveInfectionRisk
    | EnrollAging
    | ResolveAging
    | ApprovePermanentChange
)


class DiseaseCare(Record):
    id: str
    recovery_bonus: int = Field(ge=0, le=20)
    technology_level: int = Field(ge=0, le=12)
    antibiotic: bool = False


class HealthResult(Record):
    subject_id: str
    active: bool = False
    due: int | None = None
    hp_lost: int = 0
    checks: tuple[CheckTrace, ...] = ()
    infected: bool = False
    symptomatic: bool = False
    permanent_change_ids: tuple[str, ...] = ()


def _latest(state: ResourceState, prefix: str, model: type[Record]) -> tuple[Record, ...]:
    values: dict[str, Record] = {}
    for event in state.events:
        if event.id.startswith(prefix):
            item = model.model_validate_json(event.kind)
            values[str(item.model_dump()["id"])] = item
    return tuple(values.values())


def disease_episodes(state: ResourceState) -> tuple[DiseaseEpisode, ...]:
    return tuple(
        DiseaseEpisode.model_validate(v) for v in _latest(state, DISEASE_PREFIX, DiseaseEpisode)
    )


def infection_risks(state: ResourceState) -> tuple[WoundInfectionRisk, ...]:
    return tuple(
        WoundInfectionRisk.model_validate(v)
        for v in _latest(state, INFECTION_PREFIX, WoundInfectionRisk)
    )


def aging_schedules(state: ResourceState) -> tuple[AgingSchedule, ...]:
    return tuple(
        AgingSchedule.model_validate(v) for v in _latest(state, AGING_PREFIX, AgingSchedule)
    )


def permanent_changes(state: ResourceState) -> tuple[PermanentChange, ...]:
    return tuple(
        PermanentChange.model_validate(v) for v in _latest(state, PERMANENT_PREFIX, PermanentChange)
    )


def permanent_attribute_losses(state: ResourceState, actor_id: str) -> PermanentAttributeLoss:
    result = PermanentAttributeLoss()
    for change in permanent_changes(state):
        if change.actor_id == actor_id and change.status == "applied":
            result = result.plus(change.losses)
    return result


def due_health_effects(
    state: ResourceState, actor_ids: frozenset[str], at: int
) -> tuple[tuple[int, str], ...]:
    """Return stable deadline keys for shared-clock ordering and reconnect catch-up."""
    due = [
        (item.due, item.id)
        for item in disease_episodes(state)
        if item.active and item.actor_id in actor_ids and item.due <= at
    ]
    due.extend(
        (item.due, item.id)
        for item in infection_risks(state)
        if not item.settled and item.actor_id in actor_ids and item.due <= at
    )
    due.extend(
        (item.due, item.id)
        for item in aging_schedules(state)
        if item.actor_id in actor_ids and item.due <= at
    )
    return tuple(sorted(due))


def require_health_settled(state: ResourceState, actor_ids: frozenset[str], at: int) -> None:
    if due_health_effects(state, actor_ids, at):
        raise ConflictError("Resolve due disease, infection, or aging effects first")


def require_no_health_deadline_before(
    state: ResourceState, actor_ids: frozenset[str], at: int
) -> None:
    if due_health_effects(state, actor_ids, at - 1):
        raise ConflictError("Advance to the disease, infection, or aging deadline and resolve it")


def disease_projection(
    state: ResourceState, actor_ids: tuple[str, ...], *, director: bool = False
) -> tuple[dict[str, object], ...]:
    """Hide carrier, check and disease identity until symptoms/diagnosis justify knowledge."""
    result: list[dict[str, object]] = []
    for item in disease_episodes(state):
        if not director and item.actor_id not in actor_ids:
            continue
        if not director and not item.symptomatic:
            continue
        value: dict[str, object] = {
            "id": hashlib.sha256(item.id.encode()).hexdigest(),
            "actor_id": item.actor_id,
            "active": item.active,
            "symptomatic": item.symptomatic,
            "diagnosis": item.profile.title if item.diagnosed else None,
        }
        if director:
            value.update(
                {
                    "episode_id": item.id,
                    "disease_id": item.profile.id,
                    "carrier_id": item.private_carrier_id,
                    "stage": item.stage,
                    "due": item.due,
                    "checks": item.checks,
                }
            )
        result.append(value)
    return tuple(result)


def _event(prefix: str, command_id: str, item: Record, at: int, actor_id: str) -> ResourceEvent:
    item_id = str(item.model_dump()["id"])
    return ResourceEvent(
        id=f"{prefix}{item_id}:{command_id}", at=at, target_id=actor_id, kind=item.model_dump_json()
    )


def _digest(command: HealthCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _replay(state: ResourceState, command: HealthCommand) -> HealthResult | None:
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Health command ID reused")
    event = next(e for e in state.events if e.id == RESULT_PREFIX + command.id)
    return HealthResult.model_validate_json(event.kind)


def _finish(
    state: ResourceState,
    command: HealthCommand,
    result: HealthResult,
    events: tuple[ResourceEvent, ...],
) -> tuple[ResourceState, HealthResult]:
    updated = state.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": state.events
            + events
            + (
                ResourceEvent(
                    id=RESULT_PREFIX + command.id,
                    at=state.game_time,
                    target_id=command.actor_id,
                    kind=result.model_dump_json(),
                ),
            ),
        }
    )
    return ResourceState.model_validate(updated), result


def _require_actor_hp(state: ResourceState, actor_id: str) -> tuple[int, int]:
    hp = next((p for p in state.pools if p.id == "hp:" + actor_id), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Disease and aging require an exact Basic Set HP pool")
    if hp.injury.dead:
        raise ValidationError("Dead actors cannot begin health schedules")
    return hp.current, hp.maximum


def _new_change(actor_id: str, cause_id: str, losses: PermanentAttributeLoss) -> PermanentChange:
    return PermanentChange(
        id="health-change:" + hashlib.sha256(f"{actor_id}:{cause_id}".encode()).hexdigest(),
        actor_id=actor_id,
        cause_id=cause_id,
        losses=losses,
        source_ref="B442-444" if cause_id.startswith("disease:") else "B20-21",
    )


def _apply_disease_damage(
    state: ResourceState, command: ResolveDisease, damage: int, ht: int, rng: RandomSource
) -> ResourceState:
    if not damage:
        return state
    state, _ = apply_injury(
        state,
        Wound(
            id="disease-hp:" + hashlib.sha256(command.id.encode()).hexdigest(),
            actor_id=command.actor_id,
            expected_revision=state.revision,
            basic_damage=damage,
            resistance=0,
            damage_type="tox",
            injury_source="internal",
        ),
        ht=ht,
        rng=rng,
        system=True,
    )
    return state


def _settle_episode(
    episode: DiseaseEpisode, at: int, rng: RandomSource
) -> tuple[DiseaseEpisode, int]:
    hp_lost = 0
    checks = list(episode.checks)
    while episode.active and episode.due <= at:
        exposure = episode.stage == "exposure"
        target = max(
            1,
            episode.ht
            + episode.profile.resistance_modifier
            + (episode.contact_modifier + episode.protection_bonus if exposure else 0)
            + (0 if exposure else episode.treatment_bonus),
        )
        check = success_roll(episode.profile.profile_id, target, rng=rng)
        checks.append(check)
        if exposure:
            if check.outcome.succeeded:
                return (
                    episode.model_copy(
                        update={
                            "stage": "resisted",
                            "active": False,
                            "immune": sum(check.dice) <= 4,
                            "checks": tuple(checks),
                        }
                    ),
                    hp_lost,
                )
            episode = episode.model_copy(
                update={
                    "stage": "incubating",
                    "due": episode.due + episode.profile.incubation_seconds,
                    "checks": tuple(checks),
                }
            )
            continue
        if check.outcome.succeeded:
            return (
                episode.model_copy(
                    update={
                        "stage": "recovered",
                        "active": False,
                        "immune": episode.profile.acquired_immunity,
                        "checks": tuple(checks),
                    }
                ),
                hp_lost,
            )
        damage = rng.randbelow(6) + 1 if episode.profile.damage_dice else episode.profile.damage_add
        hp_lost += damage
        total = episode.total_damage + damage
        remaining = episode.remaining - 1
        episode = episode.model_copy(
            update={
                "total_damage": total,
                "remaining": remaining,
                "symptomatic": total * episode.profile.symptom_hp_denominator
                >= episode.full_hp * episode.profile.symptom_hp_numerator,
                "active": remaining > 0,
                "stage": "cycles" if remaining else "recovered",
                "due": episode.due + episode.profile.cycle_seconds,
                "immune": episode.profile.acquired_immunity if remaining == 0 else False,
                "checks": tuple(checks),
            }
        )
    return episode, hp_lost


def _record_disease_damage(
    state: ResourceState,
    command: ResolveDisease,
    episode: DiseaseEpisode,
    hp_lost: int,
    rng: RandomSource,
) -> tuple[ResourceState, DiseaseEpisode, PermanentChange | None]:
    state = _apply_disease_damage(state, command, hp_lost, episode.ht, rng)
    old = next((i for i in state.illnesses if i.id == episode.id), None)
    restriction = RecoveryRestriction(
        id=episode.id,
        actor_id=episode.actor_id,
        active=episode.active,
        hp_debt=(old.hp_debt if old else 0) + hp_lost,
        blocks_natural_healing=True,
    )
    state = state.model_copy(
        update={
            "illnesses": tuple(i for i in state.illnesses if i.id != episode.id) + (restriction,)
        }
    )
    symptoms = set(episode.profile.symptom_effect_ids)
    active_effects = set(state.active_effect_ids)
    if episode.symptomatic and episode.active:
        active_effects.update(symptoms)
    elif not episode.active:
        active_effects.difference_update(symptoms)
    state = state.model_copy(update={"active_effect_ids": tuple(sorted(active_effects))})
    threshold = episode.profile.lasting_after_damage
    if (
        threshold is None
        or episode.total_damage < threshold
        or episode.permanent_change_id is not None
    ):
        return state, episode, None
    change = _new_change(episode.actor_id, episode.id, episode.profile.lasting_loss)
    return state, episode.model_copy(update={"permanent_change_id": change.id}), change


def apply_disease(
    state: ResourceState,
    command: ExposeDisease | ResolveDisease | TreatDisease | DiagnoseDisease,
    *,
    rng: RandomSource,
    system: bool = False,
    profile: DiseaseProfile | None = None,
    relationship: ContactExposure | None = None,
    care: DiseaseCare | None = None,
    ht: int | None = None,
) -> tuple[ResourceState, HealthResult]:
    if not system:
        raise ValidationError("Disease requires authoritative authored context")
    prior = _replay(state, command)
    if prior is not None:
        return state, prior
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    _require_actor_hp(state, command.actor_id)
    episodes = disease_episodes(state)
    events: list[ResourceEvent] = []
    changes: list[PermanentChange] = []
    hp_lost = 0

    if isinstance(command, ExposeDisease):
        if profile is None or relationship is None or ht is None or ht < 1:
            raise ValidationError("Exposure requires an authored disease and contact relationship")
        if (
            relationship.id != command.relationship_id
            or relationship.actor_id != command.actor_id
            or relationship.disease_id != profile.id
            or relationship.vector != profile.vector
            or relationship.occurred_at > state.game_time
        ):
            raise ValidationError("Disease contact does not match its authored relationship")
        episode_id = (
            "disease:"
            + hashlib.sha256(
                f"{relationship.actor_id}:{relationship.id}:{profile.id}".encode()
            ).hexdigest()
        )
        if any(e.id == episode_id for e in episodes):
            raise ConflictError("Contact relationship already recorded")
        immune = any(
            e.actor_id == command.actor_id
            and e.profile.id == profile.id
            and e.immune
            and not e.active
            for e in episodes
        )
        episode = DiseaseEpisode(
            id=episode_id,
            actor_id=command.actor_id,
            relationship_id=relationship.id,
            profile=profile,
            started=relationship.occurred_at,
            due=relationship.occurred_at + relationship.check_after_seconds,
            stage="resisted" if immune else "exposure",
            active=not immune,
            remaining=profile.cycles,
            ht=ht,
            full_hp=next(p.maximum for p in state.pools if p.id == "hp:" + command.actor_id),
            contact_modifier=CONTACT_MODIFIERS[relationship.contact],
            protection_bonus=relationship.protection_bonus,
            immune=immune,
            private_carrier_id=relationship.carrier_id,
        )
    else:
        found_episode = next((e for e in episodes if e.id == command.episode_id), None)
        if found_episode is None or found_episode.actor_id != command.actor_id:
            raise ValidationError("Unknown or unauthorized disease episode")
        episode = found_episode
        if isinstance(command, DiagnoseDisease):
            if not episode.symptomatic:
                raise ValidationError("Diagnosis requires observable symptoms")
            episode = episode.model_copy(update={"diagnosed": True})
        elif isinstance(command, TreatDisease):
            if care is None or care.id != command.care_id or not episode.active:
                raise ValidationError(
                    "Treatment requires matching authored care and active disease"
                )
            if care.antibiotic and (
                care.technology_level < 6
                or not episode.profile.bacterial
                or episode.profile.drug_resistant
            ):
                raise ValidationError("Antibiotics do not apply to this disease profile")
            episode = episode.model_copy(
                update={"treatment_bonus": max(episode.treatment_bonus, care.recovery_bonus)}
            )
        else:
            if not episode.active:
                raise ConflictError("Disease episode is not active")
            if state.game_time < episode.due:
                raise ConflictError("Disease check is not due")
            original_check_count = len(episode.checks)
            episode, hp_lost = _settle_episode(episode, state.game_time, rng)
            state, episode, change = _record_disease_damage(state, command, episode, hp_lost, rng)
            changes.extend((change,) if change is not None else ())

    events.append(_event(DISEASE_PREFIX, command.id, episode, state.game_time, command.actor_id))
    events.extend(
        _event(PERMANENT_PREFIX, command.id, change, state.game_time, command.actor_id)
        for change in changes
    )
    result_checks = (
        episode.checks[original_check_count:] if isinstance(command, ResolveDisease) else ()
    )
    result = HealthResult(
        subject_id=episode.id,
        active=episode.active,
        due=episode.due if episode.active else None,
        hp_lost=hp_lost,
        checks=result_checks,
        infected=episode.stage not in ("exposure", "resisted"),
        symptomatic=episode.symptomatic,
        permanent_change_ids=tuple(c.id for c in changes),
    )
    return _finish(state, command, result, tuple(events))


def apply_infection_risk(
    state: ResourceState,
    command: RecordInfectionRisk | ResolveInfectionRisk,
    *,
    rng: RandomSource,
    system: bool = False,
    risk: WoundInfectionRisk | None = None,
    infection: DiseaseProfile | None = None,
    ht: int | None = None,
) -> tuple[ResourceState, HealthResult]:
    if not system:
        raise ValidationError("Wound infection requires authoritative injury context")
    prior = _replay(state, command)
    if prior is not None:
        return state, prior
    if command.expected_revision != state.revision:
        raise ConflictError("Resource revision changed")
    _require_actor_hp(state, command.actor_id)
    events: list[ResourceEvent] = []
    if isinstance(command, RecordInfectionRisk):
        if risk is None or risk.id != command.risk_id or risk.actor_id != command.actor_id:
            raise ValidationError("Infection risk does not match its authored wound context")
        wound = next(
            (
                e
                for e in state.events
                if e.id == "injury:" + risk.wound_event_id and e.target_id == command.actor_id
            ),
            None,
        )
        if wound is None or risk.opened_at > state.game_time:
            raise ValidationError("Infection risk requires a recorded open wound")
        if any(r.id == risk.id for r in infection_risks(state)):
            raise ConflictError("Wound infection risk already recorded")
        current = risk
        result = HealthResult(subject_id=risk.id, active=True, due=risk.due)
    else:
        found_risk = next((r for r in infection_risks(state) if r.id == command.risk_id), None)
        if found_risk is None or found_risk.actor_id != command.actor_id:
            raise ValidationError("Unknown or unauthorized infection risk")
        current = found_risk
        if current.settled:
            raise ConflictError("Wound infection risk is already settled")
        if state.game_time < current.due:
            raise ConflictError("Wound infection check is not due")
        if infection is None or ht is None or infection.profile_id != "gurps-basic-set-4e-2004":
            raise ValidationError("Infection resolution requires its authored disease and HT")
        check = None
        infected = False
        if not current.antibiotics or current.treatment_critical_failure:
            check = success_roll(
                infection.profile_id,
                max(1, ht + 3 + current.contamination_modifier),
                rng=rng,
            )
            infected = not check.outcome.succeeded
        episode_id = None
        if infected:
            episode_id = "disease:" + hashlib.sha256(f"infection:{current.id}".encode()).hexdigest()
            episode = DiseaseEpisode(
                id=episode_id,
                actor_id=current.actor_id,
                relationship_id=current.id,
                profile=infection,
                started=current.opened_at,
                due=state.game_time + infection.cycle_seconds,
                stage="cycles",
                remaining=infection.cycles,
                ht=ht,
                full_hp=next(p.maximum for p in state.pools if p.id == "hp:" + current.actor_id),
                contact_modifier=current.contamination_modifier,
            )
            events.append(
                _event(DISEASE_PREFIX, command.id, episode, state.game_time, command.actor_id)
            )
        current = current.model_copy(update={"settled": True, "infection_episode_id": episode_id})
        result = HealthResult(
            subject_id=current.id,
            active=False,
            checks=(check,) if check else (),
            infected=infected,
        )
    events.insert(
        0, _event(INFECTION_PREFIX, command.id, current, state.game_time, command.actor_id)
    )
    return _finish(state, command, result, tuple(events))


def _aging_scale(rules: AgingRules) -> Fraction:
    return Fraction(2**rules.extended_lifespan_levels, 2**rules.short_lifespan_levels)


def _age_at(schedule: AgingSchedule, at: int) -> Fraction:
    return Fraction(schedule.age_seconds_at_start + at - schedule.started, YEAR_SECONDS)


def _aging_interval(rules: AgingRules, age: Fraction) -> int:
    scale = _aging_scale(rules)
    if age >= 90 * scale:
        base = Fraction(YEAR_SECONDS, 4)
    elif age >= 70 * scale:
        base = Fraction(YEAR_SECONDS, 2)
    else:
        base = Fraction(YEAR_SECONDS)
    return max(1, int(base * scale))


def apply_aging(
    state: ResourceState,
    command: EnrollAging | ResolveAging,
    *,
    rng: RandomSource,
    rules: AgingRules | None = None,
    age_seconds: int | None = None,
    ht: int | None = None,
    fitness_modifier: int = 0,
    system: bool = False,
) -> tuple[ResourceState, HealthResult]:
    if not system:
        raise ValidationError("Aging requires authoritative campaign context")
    prior = _replay(state, command)
    if prior is not None:
        return state, prior
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    _require_actor_hp(state, command.actor_id)
    changes: list[PermanentChange] = []
    if isinstance(command, EnrollAging):
        if rules is None or not rules.enabled or rules.unaging or age_seconds is None or ht is None:
            raise ValidationError(
                "Aging must be enabled for an aging actor in the selected profile"
            )
        if age_seconds < 0 or not -2 <= fitness_modifier <= 2:
            raise ValidationError("Invalid chronological age or fitness modifier")
        if any(s.id == command.schedule_id for s in aging_schedules(state)):
            raise ConflictError("Aging schedule already exists")
        scale = _aging_scale(rules)
        first = int(50 * YEAR_SECONDS * scale)
        due = state.game_time + max(0, first - age_seconds)
        schedule = AgingSchedule(
            id=command.schedule_id,
            actor_id=command.actor_id,
            started=state.game_time,
            age_seconds_at_start=age_seconds,
            due=due,
            rules=rules,
            ht=ht,
            fitness_modifier=fitness_modifier,
        )
        result = HealthResult(subject_id=schedule.id, active=True, due=schedule.due)
    else:
        found_schedule = next(
            (s for s in aging_schedules(state) if s.id == command.schedule_id), None
        )
        if found_schedule is None or found_schedule.actor_id != command.actor_id:
            raise ValidationError("Unknown or unauthorized aging schedule")
        schedule = found_schedule
        if state.game_time < schedule.due:
            raise ConflictError("Aging check is not due")
        target = schedule.ht + schedule.rules.technology_level - 3 + schedule.fitness_modifier
        losses: dict[str, int] = {}
        checks: list[CheckTrace] = []
        for attribute in ("st", "dx", "iq", "ht"):
            check = success_roll(schedule.rules.profile_id, max(1, target), rng=rng)
            checks.append(check)
            total = sum(check.dice)
            succeeded = check.outcome.succeeded
            if schedule.rules.longevity:
                succeeded = total <= (17 if target >= 17 else 16)
            losses[attribute] = 0 if succeeded else 1
            if (
                not schedule.rules.longevity
                and not succeeded
                and (check.outcome is Outcome.CRITICAL_FAILURE or total >= 17)
            ):
                losses[attribute] = 2
        loss = PermanentAttributeLoss(**losses)
        pending = schedule.pending_change_ids
        if not loss.empty:
            change = _new_change(command.actor_id, f"aging:{schedule.id}:{schedule.due}", loss)
            changes.append(change)
            pending += (change.id,)
        interval = _aging_interval(schedule.rules, _age_at(schedule, schedule.due))
        schedule = schedule.model_copy(
            update={
                "due": schedule.due + interval,
                "checks": schedule.checks + tuple(checks),
                "pending_change_ids": pending,
            }
        )
        result = HealthResult(
            subject_id=schedule.id,
            active=True,
            due=schedule.due,
            checks=tuple(checks),
            permanent_change_ids=tuple(c.id for c in changes),
        )
    events = [_event(AGING_PREFIX, command.id, schedule, state.game_time, command.actor_id)]
    events.extend(
        _event(PERMANENT_PREFIX, command.id, change, state.game_time, command.actor_id)
        for change in changes
    )
    return _finish(state, command, result, tuple(events))


def approve_permanent_change(
    state: ResourceState, command: ApprovePermanentChange, *, system: bool = False
) -> tuple[ResourceState, HealthResult]:
    """Record that the exact loss passed the external character-build approval boundary."""
    if not system:
        raise ValidationError("Permanent health changes require approved character mutation")
    prior = _replay(state, command)
    if prior is not None:
        return state, prior
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    change = next((c for c in permanent_changes(state) if c.id == command.change_id), None)
    if change is None or change.actor_id != command.actor_id:
        raise ValidationError("Unknown or unauthorized permanent health change")
    if change.status != "pending":
        raise ConflictError("Permanent health change is already applied")
    if command.losses != change.losses:
        raise ValidationError("Approved build must carry the exact permanent attribute loss")
    change = change.model_copy(
        update={"status": "applied", "approved_build_revision": command.approved_build_revision}
    )
    result = HealthResult(subject_id=change.id, permanent_change_ids=(change.id,))
    return _finish(
        state,
        command,
        result,
        (_event(PERMANENT_PREFIX, command.id, change, state.game_time, command.actor_id),),
    )
