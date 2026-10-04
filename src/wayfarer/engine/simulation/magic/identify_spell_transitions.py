"""One explicit second of Information casting, secret resolution and apparent reports."""

from wayfarer.engine.rules.checks import Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.identify_spell_admission import (
    AcceptedSpellProducer,
    current_subject,
    observation,
    observed_spells,
    ready,
)
from wayfarer.engine.simulation.magic.identify_spell_state import (
    CastIdentifySpell,
    IdentificationReport,
    IdentifySpellCommand,
    IdentifySpellResult,
    ObserveIdentifySpellSubject,
    ReportIdentifySpell,
    SecretIdentification,
    append,
    observations,
    reports,
    secret_result,
)
from wayfarer.engine.simulation.magic.spells import _casting_modifiers
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
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
        raise ConflictError("Bounded Identify Spell does not admit timed hazard carriers")


def _cast(
    runtime: RulesContext,
    state: PlayState,
    command: CastIdentifySpell,
    producers: tuple[AcceptedSpellProducer, ...],
) -> PlayState:
    if secret_result(state.resources, command.cast_id) is not None:
        raise ConflictError("Identify Spell cast identity is immutable")
    skill = ready(runtime, state, command.actor_id)
    subject = current_subject(state, command.subject_id, command.actor_id)
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
        raise ConflictError("Identify Spell cannot settle changed party activity")
    subject = current_subject(advanced, command.subject_id, command.actor_id)
    actor = next(a for a in advanced.actors if a.actor_id == command.actor_id)
    if actor.available_at > advanced.resources.game_time or actor.conditions:
        raise ConflictError("Identify Spell caster became unavailable")
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
        raise ConflictError("Identify Spell accepted skill changed")
    spells = observed_spells(advanced, subject, producers)
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
            amount=2,
            power=True,
        ),
        ht=compiled.statistics.ht,
        rng=runtime.rng,
        system=True,
    )
    if cost.fp_lost != 2 or cost.hp_lost:
        raise ConflictError("Identify Spell could not pay its full selected energy")
    secret = SecretIdentification(
        command_id=command.id,
        cast_id=command.cast_id,
        actor_id=command.actor_id,
        subject_id=subject.subject.subject_id,
        at=resources.game_time,
        check=check,
        spells=spells,
        descriptions=tuple(sorted({s.description for s in spells})),
    )
    return advanced.model_copy(
        update={"resources": append(resources, "secret", command.id, command.actor_id, secret)}
    )


def _report(state: PlayState, command: ReportIdentifySpell) -> PlayState:
    secret = secret_result(state.resources, command.cast_id)
    if secret is None or any(r.cast_id == command.cast_id for r in reports(state.resources)):
        raise ConflictError("Identify Spell report is unavailable or already published")
    if secret.check.outcome.succeeded:
        if command.descriptions is not None:
            raise ValidationError("Successful identification derives its actual finding")
        descriptions = secret.descriptions
        truthful = True
    elif secret.check.outcome == Outcome.CRITICAL_FAILURE:
        if (
            command.descriptions is None
            or tuple(sorted(set(command.descriptions))) == secret.descriptions
        ):
            raise ValidationError("Critical identification requires an actual false report")
        if any(not d.strip() for d in command.descriptions):
            raise ValidationError("False finding descriptions must be nonempty")
        descriptions = command.descriptions
        truthful = False
    else:
        if command.descriptions is not None:
            raise ValidationError("Ordinary failed identification publishes no finding")
        descriptions = ()
        truthful = False
    report = IdentificationReport(
        command_id=command.id,
        cast_id=command.cast_id,
        actor_id=secret.actor_id,
        subject_id=secret.subject_id,
        descriptions=descriptions,
        truthful=truthful,
    )
    return state.model_copy(
        update={"resources": append(state.resources, "report", command.id, secret.actor_id, report)}
    )


def apply(
    runtime: RulesContext,
    state: PlayState,
    command: IdentifySpellCommand,
    producers: tuple[AcceptedSpellProducer, ...] = (),
) -> tuple[PlayState, IdentifySpellResult]:
    if isinstance(command, ObserveIdentifySpellSubject):
        if any(o.subject.id == command.subject.id for o in observations(state.resources)):
            raise ConflictError("Identify Spell physical observation identity is immutable")
        value = observation(state, command.subject, command.id)
        state = state.model_copy(
            update={
                "resources": append(
                    state.resources, "subject", command.id, command.subject.caster_id, value
                )
            }
        )
        result = IdentifySpellResult(command_id=command.id, outcome="accepted")
    elif isinstance(command, CastIdentifySpell):
        state = _cast(runtime, state, command, producers)
        result = IdentifySpellResult(command_id=command.id, outcome="finished")
    else:
        state = _report(state, command)
        result = IdentifySpellResult(command_id=command.id, outcome="reported")
    return state.model_copy(
        update={
            "resources": append(state.resources, "receipt", command.id, command.actor_id, result)
        }
    ), result
