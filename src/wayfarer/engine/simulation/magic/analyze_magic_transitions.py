"""Actual hour casting, full Information-spell energy and secret GM reports."""

from typing import Literal

from wayfarer.engine.rules.checks import Modifier, Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.magic.analyze_magic_admission import (
    daily,
    digest,
    energy,
    observed,
    ready,
    require_current,
    subject,
)
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    AnalysisCast,
    AnalysisReport,
    AnalyzeMagicCommand,
    AnalyzeMagicResult,
    CancelAnalyzeMagic,
    CompleteAnalyzeMagic,
    ObserveAnalyzeMagicSubject,
    ReportAnalyzeMagic,
    SecretAnalysis,
    StartAnalyzeMagic,
    WorkAnalyzeMagic,
    append,
    casts,
    observations,
    pending_actor_ids,
    reports,
    secret_result,
)
from wayfarer.engine.simulation.magic.analyze_magic_work import current, resolve_focus, work
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.spells import _casting_modifiers
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def _cast(state: PlayState, cast_id: str, actor_id: str) -> AnalysisCast:
    value = casts(state.resources).get(cast_id)
    if value is None or value.actor_id != actor_id:
        raise ValidationError("Analyze Magic cast belongs to a different observer")
    return value


def _start(runtime: RulesContext, state: PlayState, command: StartAnalyzeMagic) -> PlayState:
    if command.cast_id in casts(state.resources) or command.actor_id in pending_actor_ids(
        state.resources
    ):
        raise ConflictError("Analyze Magic cast identity or concentration is already committed")
    skill = ready(runtime, state, command.actor_id)
    daily(state, command.actor_id)
    energy(state, command.actor_id)
    require_idle_concentration(state.resources, command.actor_id)
    value = observed(state, command.subject_id)
    if value.subject.caster_id != command.actor_id:
        raise ValidationError("Subject observation belongs to a different caster")
    require_current(runtime, state, value)
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    cast = AnalysisCast(
        cast_id=command.cast_id,
        actor_id=command.actor_id,
        subject_id=command.subject_id,
        binding=value.binding,
        item_digest=value.item_digest,
        definition_digest=value.definition_digest,
        proposal_digest=digest(actor.proposal.model_dump_json()),
        location_id=value.location_id,
        skill=skill,
        hp=hp.current,
        started_at=state.resources.game_time,
        last_at=state.resources.game_time,
    )
    return state.model_copy(
        update={"resources": append(state.resources, "cast", command.id, command.actor_id, cast)}
    )


def _complete(
    runtime: RulesContext, state: PlayState, command: CompleteAnalyzeMagic, cast: AnalysisCast
) -> PlayState:
    if cast.status != "ready" or cast.seconds != 3600:
        raise ConflictError(
            "Analyze Magic requires its full uninterrupted hour before the secret roll"
        )
    current(runtime, state, cast)
    daily(state, command.actor_id)
    energy(state, command.actor_id)
    state, cast = resolve_focus(runtime, state, cast, command.id)
    if cast.status == "cancelled":
        return state
    modifiers = _casting_modifiers(state.resources, cast.actor_id, check_symptoms=True)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + cast.actor_id)
    if hp.injury is not None and hp.injury.shock:
        modifiers += (Modifier(-hp.injury.shock, "Shock", "B236", "characters-third"),)
    check = success_roll("gurps-basic-set-4e-2004", cast.skill, modifiers, rng=runtime.rng)
    compiled = build(runtime, state, command.actor_id)
    assert compiled.statistics is not None
    resources, fatigue = apply_fatigue(
        state.resources,
        FatigueCost(
            id=command.id + ":information-energy",
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            amount=8,
            power=True,
        ),
        ht=compiled.statistics.ht,
        rng=runtime.rng,
        system=True,
    )
    if fatigue.fp_lost != 8 or fatigue.hp_lost:
        raise ConflictError("Analyze Magic could not pay its selected full energy cost")
    secret = SecretAnalysis(
        command_id=command.id,
        cast_id=cast.cast_id,
        actor_id=cast.actor_id,
        item_id=cast.binding.item_id,
        binding=cast.binding,
        at=resources.game_time,
        check=check,
    )
    resources = append(resources, "secret", command.id, cast.actor_id, secret)
    resources = append(
        resources, "cast", command.id, cast.actor_id, cast.model_copy(update={"status": "rolled"})
    )
    return state.model_copy(update={"resources": resources})


def _report(state: PlayState, command: ReportAnalyzeMagic) -> PlayState:
    secret = secret_result(state.resources, command.cast_id)
    if secret is None or any(r.cast_id == command.cast_id for r in reports(state.resources)):
        raise ConflictError("Analyze Magic report requires one unreported secret outcome")
    claimed: int | None
    truthful = secret.check.outcome.succeeded
    if secret.check.outcome is Outcome.CRITICAL_FAILURE:
        if command.claimed_power is None or command.claimed_power == secret.binding.power:
            raise ValidationError(
                "Critical Information failure requires an authenticated false report"
            )
        claimed = command.claimed_power
    else:
        if command.claimed_power is not None:
            raise ValidationError("Only critical failure admits a GM false Power assertion")
        claimed = secret.binding.power if truthful else None
    value = AnalysisReport(
        command_id=command.id,
        cast_id=secret.cast_id,
        actor_id=secret.actor_id,
        item_id=secret.item_id,
        binding_id=secret.binding.id,
        project_id=secret.binding.project_id,
        spell_id=secret.binding.spell_id,
        actual_power=secret.binding.power,
        claimed_power=claimed,
        truthful=truthful,
    )
    return state.model_copy(
        update={"resources": append(state.resources, "report", command.id, secret.actor_id, value)}
    )


def apply(
    runtime: RulesContext, state: PlayState, command: AnalyzeMagicCommand
) -> tuple[PlayState, AnalyzeMagicResult]:
    outcome: Literal["accepted", "working", "finished", "cancelled", "reported"]
    if isinstance(command, ObserveAnalyzeMagicSubject):
        if any(o.subject.id == command.subject.id for o in observations(state.resources)):
            raise ConflictError("Analyze Magic observation identity is immutable")
        value = subject(runtime, state, command.subject).model_copy(
            update={"command_id": command.id}
        )
        state = state.model_copy(
            update={
                "resources": append(
                    state.resources, "subject", command.id, command.subject.caster_id, value
                )
            }
        )
        outcome = "accepted"
    elif isinstance(command, StartAnalyzeMagic):
        state = _start(runtime, state, command)
        outcome = "accepted"
    elif isinstance(command, ReportAnalyzeMagic):
        state = _report(state, command)
        outcome = "reported"
    else:
        cast = _cast(state, command.cast_id, command.actor_id)
        if isinstance(command, WorkAnalyzeMagic):
            state = work(runtime, state, command, cast)
            outcome = (
                "cancelled"
                if casts(state.resources)[cast.cast_id].status == "cancelled"
                else "working"
            )
        elif isinstance(command, CompleteAnalyzeMagic):
            state = _complete(runtime, state, command, cast)
            outcome = (
                "cancelled"
                if casts(state.resources)[cast.cast_id].status == "cancelled"
                else "finished"
            )
        else:
            assert isinstance(command, CancelAnalyzeMagic)
            if cast.status not in ("casting", "ready", "cancelled"):
                raise ConflictError("A completed secret analysis cannot be discarded or rerolled")
            state = state.model_copy(
                update={
                    "resources": append(
                        state.resources,
                        "cast",
                        command.id,
                        cast.actor_id,
                        cast.model_copy(update={"status": "cancelled"}),
                    )
                }
            )
            outcome = "cancelled"
    result = AnalyzeMagicResult(command_id=command.id, outcome=outcome)
    resources = append(state.resources, "receipt", command.id, command.actor_id, result)
    return state.model_copy(update={"resources": resources}), result
