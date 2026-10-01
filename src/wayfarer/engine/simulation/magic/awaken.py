"""Characters third printing B248 Awaken on canonical patient conditions."""

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.combat.special_damage import active_afflictions
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.engine.simulation.health.medical.rest import accrue_rest
from wayfarer.engine.simulation.magic.backfires import clear_stun
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PROFILE = "gurps-basic-set-4e-2004"
PREFIX = "awaken-alert:"


class AwakenSubject(Record):
    actor_id: str
    ht: int


class Alert(Record):
    id: str
    actor_id: str
    ht: int
    due: int
    paid: bool = False


def alerts(state: ResourceState) -> tuple[Alert, ...]:
    found: dict[str, Alert] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            alert = Alert.model_validate_json(event.kind)
            found[alert.id] = alert
    return tuple(found.values())


def _save(state: ResourceState, alert: Alert) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + alert.id + (":paid" if alert.paid else ""),
                    at=state.game_time,
                    target_id=alert.actor_id,
                    kind=alert.model_dump_json(),
                ),
            )
        }
    )


def require_alert_deadline(state: ResourceState, to: int) -> None:
    if any(not alert.paid and alert.due < to for alert in alerts(state)):
        raise ConflictError("Advance to the Awaken alert deadline before continuing")


def settle_alerts(state: ResourceState, *, rng: RandomSource | None) -> ResourceState:
    """Earn rest through this exact deadline before imposing the source FP charge."""
    due = tuple(a for a in alerts(state) if not a.paid and a.due == state.game_time)
    if not due:
        return state
    if rng is None:
        raise ValidationError("Awaken expiry requires authoritative fatigue randomness")
    state = accrue_rest(state, state.game_time)
    for alert in due:
        original_tasks = state.recovery_tasks
        state, _ = apply_fatigue(
            state,
            FatigueCost(
                id=PREFIX + alert.id,
                actor_id=alert.actor_id,
                expected_revision=state.revision,
                amount=1,
                power=True,
            ),
            ht=alert.ht,
            rng=rng,
            system=True,
        )
        # A forced spell consequence is not voluntary activity by the resting subject.
        state = state.model_copy(update={"recovery_tasks": original_tasks})
        state = _save(state, alert.model_copy(update={"paid": True}))
    return state


def validate_subjects(state: ResourceState, subjects: tuple[AwakenSubject, ...]) -> None:
    if not subjects:
        raise ValidationError("Awaken requires approved subjects inside the authored area")
    for subject in subjects:
        hp = next((p for p in state.pools if p.id == "hp:" + subject.actor_id), None)
        fp = next((p for p in state.pools if p.id == "fp:" + subject.actor_id), None)
        if hp is None or hp.injury is None or fp is None or fp.fatigue is None:
            raise ValidationError("Awaken subjects require canonical HP and FP")
        if hp.injury.profile_id != PROFILE or fp.fatigue.profile_id != PROFILE:
            raise ValidationError("Awaken requires the selected Basic Set profile")
        if fp.current <= 0:
            continue
        if fp.current * 3 < fp.maximum and any(
            not alert.paid and alert.actor_id == subject.actor_id for alert in alerts(state)
        ):
            raise ValidationError("Overlapping Awaken alert intervals require source review")
        if hp.injury.unconscious and not _injury_unconscious(state, subject.actor_id):
            raise ValidationError("Awaken requires recorded unconsciousness cause evidence")
        # Drugged wake-up cannot safely erase continuing intoxication or overwrite
        # a toxin's other conditions. Preserve this bounded variant until its
        # canonical condition consumer supports a distinct wake-up override.
        if any(
            t.actor_id == subject.actor_id
            and (
                t.overdose_until > state.game_time
                or (t.condition_until > state.game_time and t.profile.condition == "unconscious")
            )
            for t in state.toxins
        ) or any(
            i.actor_id == subject.actor_id and i.level in ("unconscious", "coma")
            for i in state.intoxications
        ):
            raise ValidationError("Drugged Awaken requires a supported consciousness override")


def _injury_unconscious(state: ResourceState, actor_id: str) -> bool:
    for event in reversed(state.events):
        if event.target_id != actor_id or not event.id.startswith("injury:"):
            continue
        result = InjuryResult.model_validate_json(event.kind)
        if any(
            not item.check.outcome.succeeded
            and (
                item.reason == "consciousness"
                or (
                    item.reason == "major-wound"
                    and (item.check.margin <= -5 or item.check.outcome is Outcome.CRITICAL_FAILURE)
                )
            )
            for item in result.checks
        ):
            return True
    return False


def awaken(
    state: ResourceState,
    subjects: tuple[AwakenSubject, ...],
    *,
    margin: int,
    command_id: str,
    rng: RandomSource,
) -> tuple[ResourceState, tuple[CheckTrace, ...]]:
    validate_subjects(state, subjects)
    traces: list[CheckTrace] = []
    for subject in subjects:
        hp = next(p for p in state.pools if p.id == "hp:" + subject.actor_id)
        fp = next(p for p in state.pools if p.id == "fp:" + subject.actor_id)
        assert hp.injury is not None and fp.fatigue is not None
        if fp.current <= 0 or hp.injury.dead:
            continue
        effects = active_afflictions(state, subject.actor_id)
        unconscious = (
            hp.injury.unconscious
            or fp.fatigue.unconscious
            or any(e.condition in ("sleep", "unconsciousness") for e in effects)
        )
        wakes = True
        if unconscious:
            penalty = 3 if hp.injury.unconscious else 0
            check = success_roll(
                PROFILE,
                subject.ht + margin - penalty,
                rng=rng,
            )
            traces.append(check)
            wakes = check.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)
        # Stunning is countered immediately, independent of an awakening roll.
        status = hp.injury.model_copy(
            update={
                "stunned": False,
                "electrical_stun": None,
                "surprise": None,
                "unconscious": hp.injury.unconscious and not wakes,
            }
        )
        fatigue = fp.fatigue.model_copy(
            update={
                "unconscious": fp.fatigue.unconscious and not wakes,
                "collapsed": fp.fatigue.collapsed and not wakes,
            }
        )
        hp = hp.model_copy(update={"injury": status})
        fp = fp.model_copy(update={"fatigue": fatigue})
        removed = {
            e.id
            for e in effects
            if e.condition == "stun" or (wakes and e.condition in ("sleep", "unconsciousness"))
        }
        state = state.model_copy(
            update={
                "pools": tuple(
                    hp if p.id == hp.id else fp if p.id == fp.id else p for p in state.pools
                ),
                "active_effect_ids": tuple(i for i in state.active_effect_ids if i not in removed),
            }
        )
        state = clear_stun(state, subject.actor_id, command_id + ":" + subject.actor_id)
        if wakes and fp.current * 3 < fp.maximum:
            state = _save(
                state,
                Alert(
                    id=command_id + ":" + subject.actor_id,
                    actor_id=subject.actor_id,
                    ht=subject.ht,
                    due=state.game_time + 3600,
                ),
            )
    return state, tuple(traces)
