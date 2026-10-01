"""Characters third printing B248 Awaken on canonical patient conditions."""

import hashlib
import json
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import CheckTrace, Modifier, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.recovery import require_settled
from wayfarer.engine.simulation.combat.special_damage import active_afflictions
from wayfarer.engine.simulation.health.drug_state import drug_unconscious, drugged, wake_drugged
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.engine.simulation.health.medical.rest import accrue_rest
from wayfarer.engine.simulation.health.sleep_state import asleep, wake_sleep
from wayfarer.engine.simulation.magic.awaken_state import PREFIX, Alert
from wayfarer.engine.simulation.magic.awaken_state import alerts as alerts
from wayfarer.engine.simulation.magic.backfires import clear_stun
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState

PROFILE = "gurps-basic-set-4e-2004"


class AwakenSubject(Record):
    actor_id: str
    ht: int
    authored_unconscious: bool = False


class WakeFact(Record):
    actor_id: str
    command_id: str
    awake: bool


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
    if len({subject.actor_id for subject in subjects}) != len(subjects):
        raise ValidationError("Awaken area subjects must be unique")
    for subject in subjects:
        hp = next((p for p in state.pools if p.id == "hp:" + subject.actor_id), None)
        fp = next((p for p in state.pools if p.id == "fp:" + subject.actor_id), None)
        if hp is None or hp.injury is None or fp is None or fp.fatigue is None:
            raise ValidationError("Awaken subjects require canonical HP and FP")
        if hp.injury.profile_id != PROFILE or fp.fatigue.profile_id != PROFILE:
            raise ValidationError("Awaken requires the selected Basic Set profile")
        if fp.current <= 0 or hp.injury.dead:
            continue
        require_settled(state.recovery_tasks, frozenset({subject.actor_id}), state.game_time)
        if any(
            task.actor_id == subject.actor_id
            and task.kind == "sleep"
            and task.status == "pending"
            and not task.settled
            and task.due <= state.game_time
            for task in state.survival_tasks
        ):
            raise ConflictError("Settle the due sleep activity before Awaken")
        if hp.injury.unconscious and not (
            _injury_unconscious(state, subject.actor_id)
            or drugged(state, subject.actor_id)
            or asleep(state, subject.actor_id)
        ):
            raise ValidationError("Awaken requires recorded unconsciousness cause evidence")


def _injury_unconscious(state: ResourceState, actor_id: str) -> bool:
    for event in reversed(state.events):
        if event.target_id == actor_id and event.id.startswith("awaken-wake:"):
            if WakeFact.model_validate_json(event.kind).awake:
                return False
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


def mirror_waking(before: PlayState, after: PlayState) -> PlayState:
    """Project only this transition's committed waking facts onto legacy actor flags."""
    facts = {
        fact.actor_id: fact
        for event in after.resources.events[len(before.resources.events) :]
        if event.id.startswith("awaken-wake:")
        for fact in (WakeFact.model_validate_json(event.kind),)
    }
    return after.model_copy(
        update={
            "actors": tuple(
                actor.model_copy(
                    update={
                        "conditions": tuple(
                            condition
                            for condition in actor.conditions
                            if condition != "stunned"
                            and not (condition == "unconscious" and facts[actor.actor_id].awake)
                        )
                    }
                )
                if actor.actor_id in facts
                else actor
                for actor in after.actors
            )
        }
    )


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
            subject.authored_unconscious
            or asleep(state, subject.actor_id)
            or drug_unconscious(state, subject.actor_id)
            or hp.injury.unconscious
            or fp.fatigue.unconscious
            or any(e.condition in ("sleep", "unconsciousness") for e in effects)
        )
        wakes = True
        if unconscious:
            penalty = (
                3 if hp.injury.unconscious and _injury_unconscious(state, subject.actor_id) else 0
            )
            drug_penalty = 6 if drugged(state, subject.actor_id) else 0
            check = success_roll(
                PROFILE,
                subject.ht,
                (
                    Modifier(margin, "Caster margin", "spell:awaken", "B248"),
                    Modifier(-penalty, "Injury unconsciousness", "spell:awaken", "B248"),
                    Modifier(-drug_penalty, "Drugged", "spell:awaken", "B248"),
                ),
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
        marker = hashlib.sha256(json.dumps([command_id, subject.actor_id]).encode()).hexdigest()
        fact = WakeFact(actor_id=subject.actor_id, command_id=command_id, awake=wakes)
        state = state.model_copy(
            update={
                "events": state.events
                + (
                    ResourceEvent(
                        id="awaken-wake:" + marker,
                        at=state.game_time,
                        target_id=subject.actor_id,
                        kind=fact.model_dump_json(),
                    ),
                )
            }
        )
        if wakes:
            state = wake_drugged(state, subject.actor_id, command_id)
            state = wake_sleep(state, subject.actor_id)
        if wakes and fp.current * 3 < fp.maximum:
            state = _save(
                state,
                Alert(
                    id=marker,
                    actor_id=subject.actor_id,
                    ht=subject.ht,
                    due=state.game_time + 3600,
                ),
            )
    return state, tuple(traces)
