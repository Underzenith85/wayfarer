"""Regular Detect Magic outcomes, derived findings and authenticated backfire."""

from wayfarer.engine.rules.checks import Modifier, Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.detect_magic_admission import digest, ready, subject, truth
from wayfarer.engine.simulation.magic.detect_magic_state import (
    CancelDetectMagic,
    CompleteDetectMagic,
    DetectCast,
    DetectFinding,
    DetectMagicCommand,
    DetectResult,
    ObserveDetectMagicSubject,
    StartDetectMagic,
    WorkDetectMagic,
    append,
    casts,
    findings,
    observations,
    pending_actor_ids,
)
from wayfarer.engine.simulation.magic.detect_magic_work import current, focus, observation, work
from wayfarer.engine.simulation.magic.spells import _casting_modifiers
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def _observe(
    runtime: RulesContext, state: PlayState, command: ObserveDetectMagicSubject
) -> tuple[PlayState, DetectResult]:
    if any(o.subject.id == command.subject.id for o in observations(state.resources)):
        raise ConflictError("Detect Magic observation identity is immutable")
    value = subject(runtime, state, command.subject).model_copy(update={"command_id": command.id})
    state = state.model_copy(
        update={
            "resources": append(
                state.resources, "observation", command.id, command.subject.caster_id, value
            )
        }
    )
    result = DetectResult(command_id=command.id, outcome="accepted")
    return state, result


def _start(
    runtime: RulesContext, state: PlayState, command: StartDetectMagic
) -> tuple[PlayState, DetectResult]:
    if command.cast_id in casts(state.resources) or command.actor_id in pending_actor_ids(
        state.resources
    ):
        raise ConflictError("Detect Magic cast identity or concentration already committed")
    skill = ready(runtime, state, command.actor_id, starting=True)
    values = tuple(o for o in observations(state.resources) if o.subject.id == command.subject_id)
    if len(values) != 1 or values[0].subject.caster_id != command.actor_id:
        raise ValidationError("Detect Magic requires its caster's authenticated observation")
    value = values[0]
    now = subject(runtime, state, value.subject)
    if (now.physical_digest, now.location_id) != (value.physical_digest, value.location_id):
        raise ConflictError("Detect Magic subject changed before casting")
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    cast = DetectCast(
        cast_id=command.cast_id,
        actor_id=command.actor_id,
        subject_id=command.subject_id,
        physical_digest=value.physical_digest,
        magic_identity=truth(state, value.subject, value.physical_digest).identity,
        location_id=value.location_id,
        proposal_digest=digest(actor.proposal.model_dump_json()),
        skill=skill,
        started_at=state.resources.game_time,
        last_at=state.resources.game_time,
    )
    state = state.model_copy(
        update={"resources": append(state.resources, "cast", command.id, command.actor_id, cast)}
    )
    result = DetectResult(command_id=command.id, outcome="accepted")
    return state, result


def _complete(
    runtime: RulesContext, state: PlayState, command: CompleteDetectMagic, cast: DetectCast
) -> tuple[PlayState, DetectResult]:
    if cast.status != "ready" or cast.seconds != 5:
        raise ConflictError("Detect Magic requires its five consecutive casting seconds")
    current(runtime, state, cast)
    state, cast = focus(runtime, state, cast, command.id)
    if cast.status == "cancelled":
        result = DetectResult(command_id=command.id, outcome="cancelled")
    else:
        modifiers = _casting_modifiers(state.resources, cast.actor_id, check_symptoms=True)
        hp = next(p for p in state.resources.pools if p.id == "hp:" + cast.actor_id)
        if hp.injury is not None and hp.injury.shock:
            modifiers += (Modifier(-hp.injury.shock, "Shock", "B236", "characters-third"),)
        check = success_roll("gurps-basic-set-4e-2004", cast.skill, modifiers, rng=runtime.rng)
        energy = (
            0
            if check.outcome == Outcome.CRITICAL_SUCCESS
            else 2
            if check.outcome.succeeded or check.outcome == Outcome.CRITICAL_FAILURE
            else 1
        )
        compiled = build(runtime, state, cast.actor_id)
        assert compiled.statistics is not None
        resources = state.resources
        if energy:
            resources, cost = apply_fatigue(
                resources,
                FatigueCost(
                    id=command.id + ":energy",
                    actor_id=cast.actor_id,
                    expected_revision=resources.revision,
                    amount=energy,
                    power=True,
                ),
                ht=compiled.statistics.ht,
                rng=runtime.rng,
                system=True,
            )
            if cost.fp_lost != energy or cost.hp_lost:
                raise ConflictError("Detect Magic could not pay its captured FP cost")
        finding = None
        if check.outcome.succeeded:
            value = observation(state, cast)
            canonical = truth(state, value.subject, value.physical_digest)
            first = any(
                f.actor_id == cast.actor_id
                and f.target_id == canonical.target_id
                and f.identity == canonical.identity
                and f.magical
                for f in findings(resources)
            )
            critical = check.outcome == Outcome.CRITICAL_SUCCESS
            finding = canonical.model_copy(
                update={
                    "permanence": canonical.permanence if first or critical else None,
                    "spell_id": canonical.spell_id if critical else None,
                    "power": canonical.power if critical else None,
                    "binding_id": canonical.binding_id if critical else None,
                    "project_id": canonical.project_id if critical else None,
                }
            )
            resources = append(resources, "finding", command.id, cast.actor_id, finding)
        if check.outcome == Outcome.CRITICAL_FAILURE:
            # The immutable authenticated subject explicitly selects this GM
            # improvisation. This is not a random-table result or Information lie.
            value = observation(state, cast)
            if value.subject.backfire != "injury-one":
                raise ValidationError("Unsupported Detect Magic improvised backfire")
            resources, _ = apply_injury(
                resources,
                Wound(
                    id=command.id + ":backfire",
                    actor_id=cast.actor_id,
                    expected_revision=resources.revision,
                    basic_damage=1,
                    resistance=0,
                    damage_type="cr",
                    injury_source="internal",
                ),
                ht=compiled.statistics.ht,
                rng=runtime.rng,
                system=True,
            )
        cast = cast.model_copy(update={"status": "rolled"})
        resources = append(resources, "cast", command.id, cast.actor_id, cast)
        state = state.model_copy(update={"resources": resources})
        result = DetectResult(
            command_id=command.id,
            outcome="finished",
            check=check,
            energy_spent=energy,
            finding=DetectFinding.model_validate(
                finding.model_dump(include=set(DetectFinding.model_fields))
            )
            if finding
            else None,
        )
    return state, result


def apply(
    runtime: RulesContext, state: PlayState, command: DetectMagicCommand
) -> tuple[PlayState, DetectResult]:
    if isinstance(command, ObserveDetectMagicSubject):
        state, result = _observe(runtime, state, command)
    elif isinstance(command, StartDetectMagic):
        state, result = _start(runtime, state, command)
    else:
        found_cast = casts(state.resources).get(command.cast_id)
        if found_cast is None or found_cast.actor_id != command.actor_id:
            raise ValidationError("Detect Magic cast belongs to another caster")
        cast = found_cast
        if isinstance(command, CancelDetectMagic):
            if cast.status not in ("casting", "ready"):
                raise ConflictError("Detect Magic casting is already settled")
            cast = cast.model_copy(update={"status": "cancelled", "distracted": False})
            state = state.model_copy(
                update={
                    "resources": append(state.resources, "cast", command.id, cast.actor_id, cast)
                }
            )
            result = DetectResult(command_id=command.id, outcome="cancelled")
        elif isinstance(command, WorkDetectMagic):
            state = work(runtime, state, command, cast)
            result = DetectResult(
                command_id=command.id,
                outcome="cancelled"
                if casts(state.resources)[cast.cast_id].status == "cancelled"
                else "working",
            )
        else:
            state, result = _complete(runtime, state, command, cast)
    state = state.model_copy(
        update={
            "resources": append(state.resources, "receipt", command.id, command.actor_id, result)
        }
    )
    return state, result
