"""B244 actual ST resistance, retained-roll escape and canonical time/energy."""

from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, Outcome
from wayfarer.engine.rules.gurps_checks import Contestant, resolve_quick_contest, success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.magic.backfires import apply_backfire
from wayfarer.engine.simulation.magic.rooted_feet_admission import (
    current_subject,
    observation,
    ready,
    target_strength,
)
from wayfarer.engine.simulation.magic.rooted_feet_state import (
    CastRootedFeet,
    ObserveRootedFeetSubject,
    RootedFeetCommand,
    RootedFeetEffect,
    RootedFeetEscape,
    RootedFeetReceipt,
    TryRootedFeetEscape,
    active_effect,
    append,
    effects,
    escapes,
    expire,
    observations,
)
from wayfarer.engine.simulation.magic.spells import _casting_modifiers
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError

PROFILE = "gurps-basic-set-4e-2004"


def _resisted(original: CheckTrace, resistance: CheckTrace) -> bool:
    a, b = original.dice, resistance.dice
    contest = resolve_quick_contest(
        PROFILE,
        Contestant("caster", min(original.effective_target, max(16, resistance.effective_target))),
        Contestant("subject", resistance.effective_target),
        first_dice=(a[0], a[1], a[2]),
        second_dice=(b[0], b[1], b[2]),
    )
    return contest.winner != "caster"


def _cast(
    runtime: RulesContext, state: PlayState, command: CastRootedFeet
) -> tuple[PlayState, RootedFeetReceipt]:
    if command.cast_id in effects(state.resources):
        raise ConflictError("Rooted Feet cast identity is immutable")
    skill = ready(runtime, state, command.actor_id)
    subject = current_subject(runtime, state, command.subject_id, command.actor_id)
    advanced = runtime.advance(
        state,
        Advance(
            id=command.id + ":casting-second",
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            to=state.resources.game_time + 1,
        ),
    )
    if advanced.party != state.party:
        raise ConflictError("Rooted Feet cannot settle changed party activity")
    subject = current_subject(runtime, advanced, command.subject_id, command.actor_id)
    actor = next(a for a in advanced.actors if a.actor_id == command.actor_id)
    if actor.available_at > advanced.resources.game_time or actor.conditions:
        raise ConflictError("Rooted Feet caster became busy")
    advanced = advanced.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"available_at": advanced.resources.game_time})
                if a.actor_id == command.actor_id
                else a
                for a in advanced.actors
            ),
            "party": advanced.party.model_copy(
                update={
                    "groups": tuple(
                        g.model_copy(update={"ready_through": advanced.resources.game_time})
                        for g in advanced.party.groups
                    )
                }
            ),
        }
    )
    if ready(runtime, advanced, command.actor_id) != skill:
        raise ConflictError("Rooted Feet accepted skill changed")
    check = success_roll(
        PROFILE,
        skill,
        _casting_modifiers(advanced.resources, command.actor_id, check_symptoms=True),
        rng=runtime.rng,
    )
    resistance = None
    status: Literal["active", "resisted", "failed"] = (
        "active" if check.outcome.succeeded else "failed"
    )
    if check.outcome == Outcome.SUCCESS:
        target = subject.subject.target_id
        resistance = success_roll(
            PROFILE,
            target_strength(runtime, advanced, target),
            check_modifiers(advanced.resources, target, "st"),
            rng=runtime.rng,
        )
        if _resisted(check, resistance):
            status = "resisted"
    cost = (
        0
        if check.outcome == Outcome.CRITICAL_SUCCESS
        else 1
        if check.outcome == Outcome.FAILURE
        else 3
    )
    compiled = build(runtime, advanced, command.actor_id)
    assert compiled.statistics is not None
    resources = advanced.resources
    if cost:
        resources, paid = apply_fatigue(
            resources,
            FatigueCost(
                id=command.id + ":energy",
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                amount=cost,
                power=True,
            ),
            ht=compiled.statistics.ht,
            rng=runtime.rng,
            system=True,
        )
        if paid.fp_lost != cost or paid.hp_lost:
            raise ConflictError("Rooted Feet FP changed")
    if check.outcome == Outcome.CRITICAL_FAILURE:
        resources = apply_backfire(
            resources,
            command_id=command.id,
            actor_id=command.actor_id,
            cast_id=command.cast_id,
            spell_id="rooted-feet",
            ht=compiled.statistics.ht,
            severity="normal",
            rng=runtime.rng,
        )
    effect = RootedFeetEffect(
        id=command.cast_id,
        command_id=command.id,
        caster_id=command.actor_id,
        target_id=subject.subject.target_id,
        original_check=check,
        initial_resistance=resistance,
        started_at=resources.game_time,
        expires_at=resources.game_time + 60,
        status=status,
    )
    resources = append(resources, "effect", command.id, subject.subject.target_id, effect)
    return advanced.model_copy(update={"resources": resources}), RootedFeetReceipt(
        command_id=command.id, outcome=status, energy_spent=cost
    )


def _escape(
    runtime: RulesContext, state: PlayState, command: TryRootedFeetEscape
) -> tuple[PlayState, RootedFeetReceipt]:
    effect = active_effect(state.resources, command.actor_id)
    if effect is None or effect.id != command.effect_id:
        raise ConflictError("Rooted Feet active effect is unavailable to subject")
    encounter = next(
        (e for e in state.encounters if e.id == command.encounter_id and e.status == "active"), None
    )
    if (
        encounter is None
        or encounter.current_actor_id != command.actor_id
        or encounter.pending_defense is not None
        or encounter.wait_interrupt is not None
        or encounter.blocked_reason
    ):
        raise ConflictError("Rooted Feet escape requires current settled subject turn")
    fp = next(p for p in state.resources.pools if p.id == "fp:" + command.actor_id)
    if fp.current <= 0:
        raise ConflictError("Rooted Feet escape does not yet admit nonpositive FP exertion")
    participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if participant.posture != "standing" or encounter.maneuver_budget is not None:
        raise ConflictError(
            "Rooted Feet escape does not admit altered posture or extra-turn carriers"
        )
    if any(
        (e.effect_id, e.encounter_id, e.round, e.turn_index)
        == (effect.id, encounter.id, encounter.round, encounter.turn_index)
        for e in escapes(state.resources)
    ):
        raise ConflictError("Rooted Feet resistance already attempted this turn")
    check = success_roll(
        PROFILE,
        target_strength(runtime, state, command.actor_id) - 5,
        check_modifiers(state.resources, command.actor_id, "st"),
        rng=runtime.rng,
    )
    escaped = _resisted(effect.original_check, check)
    attempt = RootedFeetEscape(
        command_id=command.id,
        effect_id=effect.id,
        actor_id=command.actor_id,
        encounter_id=encounter.id,
        round=encounter.round,
        turn_index=encounter.turn_index,
        check=check,
        escaped=escaped,
    )
    resources = append(state.resources, "escape", command.id, command.actor_id, attempt)
    if escaped:
        resources = append(
            resources,
            "effect",
            command.id,
            command.actor_id,
            effect.model_copy(update={"status": "escaped"}),
        )
    return state.model_copy(update={"resources": resources}), RootedFeetReceipt(
        command_id=command.id, outcome="escaped" if escaped else "retained"
    )


def apply(
    runtime: RulesContext, state: PlayState, command: RootedFeetCommand
) -> tuple[PlayState, RootedFeetReceipt]:
    state = state.model_copy(
        update={"resources": expire(state.resources, state.resources.game_time)}
    )
    if isinstance(command, ObserveRootedFeetSubject):
        if any(o.subject.id == command.subject.id for o in observations(state.resources)):
            raise ConflictError("Rooted Feet observation identity is immutable")
        value = observation(runtime, state, command.subject, command.id)
        state = state.model_copy(
            update={
                "resources": append(
                    state.resources, "subject", command.id, command.subject.caster_id, value
                )
            }
        )
        receipt = RootedFeetReceipt(command_id=command.id, outcome="accepted")
    elif isinstance(command, CastRootedFeet):
        state, receipt = _cast(runtime, state, command)
    else:
        state, receipt = _escape(runtime, state, command)
    state = state.model_copy(
        update={
            "resources": append(state.resources, "receipt", command.id, command.actor_id, receipt)
        }
    )
    return state, receipt
