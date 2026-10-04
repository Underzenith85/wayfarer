"""One explicit second of Information casting, secret resolution and apparent reports."""

from wayfarer.engine.rules.checks import Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.magic.aura_admission import (
    current_subject,
    observation,
    ready,
)
from wayfarer.engine.simulation.magic.aura_state import (
    AuraCommand,
    AuraFinding,
    AuraReport,
    AuraResult,
    CastAura,
    ObserveAuraSubject,
    ReportAura,
    SecretAura,
    append,
    observations,
    reports,
    secret_result,
)
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.spells import _casting_modifiers
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.mental_control import MentalControl
from wayfarer.errors import ConflictError, ValidationError


def _untimed(state: PlayState, runtime: RulesContext) -> None:
    r = state.resources
    if (
        r.scheduled
        or r.hazards
        or r.cyclic_attacks
        or r.cyclic_exposures
        or r.toxins
        or r.dependencies
        or r.survival_tasks
        or r.illnesses
        or r.recovery_tasks
        or runtime.rules.npcs is not None
    ):
        raise ConflictError("Bounded Aura does not admit timed hazard carriers")


def _cast(
    runtime: RulesContext,
    state: PlayState,
    command: CastAura,
) -> PlayState:
    if secret_result(state.resources, command.cast_id) is not None:
        raise ConflictError("Aura cast identity is immutable")
    skill = ready(runtime, state, command.actor_id)
    subject = current_subject(runtime, state, command.subject_id, command.actor_id)
    _untimed(state, runtime)
    require_idle_concentration(state.resources, command.actor_id)
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
        raise ConflictError("Aura cannot settle changed party activity")
    subject = current_subject(runtime, advanced, command.subject_id, command.actor_id)
    actor = next(a for a in advanced.actors if a.actor_id == command.actor_id)
    if actor.available_at > advanced.resources.game_time or actor.conditions:
        raise ConflictError("Aura caster became unavailable")
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
        raise ConflictError("Aura accepted skill changed")
    check = success_roll(
        "gurps-basic-set-4e-2004",
        skill,
        _casting_modifiers(advanced.resources, command.actor_id, check_symptoms=True),
        rng=runtime.rng,
    )
    compiled = build(runtime, advanced, command.actor_id)
    assert compiled.statistics is not None
    resources, cost = apply_fatigue(
        advanced.resources,
        FatigueCost(
            id=command.id + ":information-energy",
            actor_id=command.actor_id,
            expected_revision=advanced.resources.revision,
            amount=3,
            power=True,
        ),
        ht=compiled.statistics.ht,
        rng=runtime.rng,
        system=True,
    )
    if cost.fp_lost != 3 or cost.hp_lost:
        raise ConflictError("Aura could not pay its full selected energy")
    secret = SecretAura(
        command_id=command.id,
        cast_id=command.cast_id,
        actor_id=command.actor_id,
        subject_id=subject.subject.subject_id,
        at=resources.game_time,
        check=check,
        observation=subject,
    )
    return advanced.model_copy(
        update={"resources": append(resources, "secret", command.id, command.actor_id, secret)}
    )


def _true_finding(secret: SecretAura, personality: str) -> AuraFinding:
    value = secret.observation
    possessed = any(
        MentalControl.model_validate_json(g).kind == "possession" for g in value.control_json
    )
    return AuraFinding(
        personality=personality,
        mage=value.magery >= 0,
        mage_power=value.subject.mage_power,
        controlled=bool(value.control_json),
        possessed=possessed,
        violent_emotion=value.subject.violent_emotion,
        secret_traits=tuple(f.description for f in value.subject.secret_traits)
        if secret.check.outcome == Outcome.CRITICAL_SUCCESS
        else (),
    )


def _report(state: PlayState, command: ReportAura) -> PlayState:
    secret = secret_result(state.resources, command.cast_id)
    if secret is None or any(r.cast_id == command.cast_id for r in reports(state.resources)):
        raise ConflictError("Aura report is unavailable or already published")
    if secret.check.outcome.succeeded:
        if command.false_finding is not None or command.personality is None:
            raise ValidationError(
                "Successful Aura requires qualitative personality adjudication only"
            )
        finding = _true_finding(secret, command.personality)
        truthful = True
    elif secret.check.outcome == Outcome.CRITICAL_FAILURE:
        if command.personality is not None or command.false_finding is None:
            raise ValidationError("Critical Aura requires an actual false finding")
        finding = command.false_finding
        if finding == _true_finding(secret, finding.personality):
            raise ValidationError("Critical Aura report must differ from actual source facts")
        truthful = False
    else:
        if command.personality is not None or command.false_finding is not None:
            raise ValidationError("Ordinary failed Aura publishes no finding")
        finding, truthful = None, False
    report = AuraReport(
        command_id=command.id,
        cast_id=command.cast_id,
        actor_id=secret.actor_id,
        subject_id=secret.subject_id,
        finding=finding,
        truthful=truthful,
    )
    return state.model_copy(
        update={"resources": append(state.resources, "report", command.id, secret.actor_id, report)}
    )


def apply(
    runtime: RulesContext,
    state: PlayState,
    command: AuraCommand,
) -> tuple[PlayState, AuraResult]:
    if isinstance(command, ObserveAuraSubject):
        if any(o.subject.id == command.subject.id for o in observations(state.resources)):
            raise ConflictError("Aura physical observation identity is immutable")
        value = observation(runtime, state, command.subject, command.id)
        state = state.model_copy(
            update={
                "resources": append(
                    state.resources, "subject", command.id, command.subject.caster_id, value
                )
            }
        )
        result = AuraResult(command_id=command.id, outcome="accepted")
    elif isinstance(command, CastAura):
        state = _cast(runtime, state, command)
        result = AuraResult(command_id=command.id, outcome="finished")
    else:
        state = _report(state, command)
        result = AuraResult(command_id=command.id, outcome="reported")
    return state.model_copy(
        update={
            "resources": append(state.resources, "receipt", command.id, command.actor_id, result)
        }
    ), result
