"""Five consecutive seconds of actual concentration, with B236 interruption."""

from contextvars import ContextVar

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.magic.detect_magic_admission import (
    digest,
    ready,
    require_current,
    truth,
)
from wayfarer.engine.simulation.magic.detect_magic_state import (
    DetectCast,
    DetectObservation,
    DetectResult,
    WorkDetectMagic,
    append,
    casts,
    interrupt_casts,
    observations,
)
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError

_WORK: ContextVar[str | None] = ContextVar("detect_magic_work", default=None)


def observation(state: PlayState, cast: DetectCast) -> DetectObservation:
    values = tuple(o for o in observations(state.resources) if o.subject.id == cast.subject_id)
    if len(values) != 1:
        raise ValidationError("Detect Magic requires its immutable authenticated observation")
    return values[0]


def current(runtime: RulesContext, state: PlayState, cast: DetectCast) -> None:
    if ready(runtime, state, cast.actor_id) != cast.skill:
        raise ConflictError("Detect Magic accepted skill changed")
    value = observation(state, cast)
    require_current(runtime, state, value)
    actor = next(a for a in state.actors if a.actor_id == cast.actor_id)
    if digest(actor.proposal.model_dump_json()) != cast.proposal_digest:
        raise ConflictError("Detect Magic approved training changed")
    if truth(state, value.subject, value.physical_digest).identity != cast.magic_identity:
        raise ConflictError("Detect Magic supported magic identity changed")
    if cast.last_at != state.resources.game_time:
        raise ConflictError("Detect Magic requires consecutive committed concentration")


def focus(
    runtime: RulesContext, state: PlayState, cast: DetectCast, command_id: str
) -> tuple[PlayState, DetectCast]:
    if not cast.distracted:
        return state, cast
    compiled = build(runtime, state, cast.actor_id)
    assert compiled.statistics is not None
    check = success_roll(
        "gurps-basic-set-4e-2004",
        compiled.statistics.will - 3,
        check_modifiers(state.resources, cast.actor_id, "will"),
        rng=runtime.rng,
    )
    state = state.model_copy(
        update={
            "resources": append(
                state.resources,
                "focus",
                command_id,
                cast.actor_id,
                DetectResult(command_id=command_id, outcome="working", check=check),
            )
        }
    )
    cast = cast.model_copy(
        update={
            "distracted": False,
            "status": cast.status if check.outcome.succeeded else "cancelled",
        }
    )
    return state.model_copy(
        update={
            "resources": append(state.resources, "cast", command_id + ":focus", cast.actor_id, cast)
        }
    ), cast


def work(
    runtime: RulesContext, state: PlayState, command: WorkDetectMagic, cast: DetectCast
) -> PlayState:
    if cast.status != "casting" or cast.seconds + command.seconds > 5:
        raise ConflictError("Detect Magic work exceeds its five-second casting interval")
    current(runtime, state, cast)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + cast.actor_id)
    if hp.injury is not None and hp.injury.shock:
        raise ConflictError("Bounded Detect Magic cannot advance casting work during active shock")
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
        raise ConflictError("Bounded Detect Magic does not admit timed hazard carriers")
    state, cast = focus(runtime, state, cast, command.id)
    if cast.status == "cancelled":
        return state
    token = _WORK.set(cast.cast_id)
    try:
        advanced = runtime.advance(
            state,
            Advance(
                id=command.id + ":clock",
                actor_id=cast.actor_id,
                expected_revision=state.resources.revision,
                to=state.resources.game_time + command.seconds,
            ),
        )
    finally:
        _WORK.reset(token)
    if advanced.party != state.party:
        raise ConflictError("Detect Magic cannot settle newly scheduled party activity")
    if advanced.party.groups:
        advanced = advanced.model_copy(
            update={
                "party": advanced.party.model_copy(
                    update={
                        "groups": (
                            advanced.party.groups[0].model_copy(
                                update={"ready_through": advanced.resources.game_time}
                            ),
                        )
                    }
                )
            }
        )
    actor = next(a for a in advanced.actors if a.actor_id == cast.actor_id)
    latest = casts(advanced.resources)[cast.cast_id]
    if latest.status == "cancelled":
        return advanced
    if actor.available_at > advanced.resources.game_time or actor.conditions or latest.distracted:
        raise ConflictError("Detect Magic caster became unavailable during concentration")
    advanced = advanced.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"available_at": advanced.resources.game_time})
                if a.actor_id == cast.actor_id
                else a
                for a in advanced.actors
            )
        }
    )
    updated = cast.model_copy(
        update={
            "seconds": cast.seconds + command.seconds,
            "last_at": advanced.resources.game_time,
            "status": "ready" if cast.seconds + command.seconds == 5 else "casting",
        }
    )
    current(runtime, advanced, updated)
    return advanced.model_copy(
        update={"resources": append(advanced.resources, "cast", command.id, cast.actor_id, updated)}
    )


def checkpoint(runtime: RulesContext, state: PlayState, before: PlayState) -> PlayState:
    resources = state.resources
    for cast in casts(resources).values():
        if cast.status not in ("casting", "ready"):
            continue
        actor = next(a for a in state.actors if a.actor_id == cast.actor_id)
        hp = next(p for p in resources.pools if p.id == "hp:" + cast.actor_id)
        previous = next(p for p in before.resources.pools if p.id == hp.id)
        changed = bool(actor.conditions) or (
            resources.game_time != cast.last_at and _WORK.get() != cast.cast_id
        )
        try:
            value = observation(state, cast)
            require_current(runtime, state, value)
            if truth(state, value.subject, value.physical_digest).identity != cast.magic_identity:
                changed = True
        except ConflictError, ValidationError:
            changed = True
        if hp.current < previous.current and not changed:
            resources = interrupt_casts(
                resources,
                cast.actor_id,
                "injury:" + str(state.revision) + ":" + str(resources.game_time),
                distraction=True,
            )
        if changed:
            resources = interrupt_casts(
                resources,
                cast.actor_id,
                "checkpoint:" + str(state.revision) + ":" + str(resources.game_time),
            )
    return state.model_copy(update={"resources": resources})
