"""Explicit continuous casting intervals and canonical interruption checkpoints."""

from contextvars import ContextVar

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.magic.analyze_magic_admission import (
    digest,
    energy,
    ready,
    require_current,
)
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    AnalysisCast,
    AnalysisConcentrationReceipt,
    WorkAnalyzeMagic,
    append,
    casts,
    interrupt_casts,
)
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError

_WORK: ContextVar[str | None] = ContextVar("analyze_magic_work", default=None)


def current(runtime: RulesContext, state: PlayState, cast: AnalysisCast) -> None:
    if ready(runtime, state, cast.actor_id) != cast.skill:
        raise ConflictError("Accepted Analyze Magic skill changed during concentration")
    require_current(runtime, state, cast)
    actor = next(a for a in state.actors if a.actor_id == cast.actor_id)
    if digest(actor.proposal.model_dump_json()) != cast.proposal_digest:
        raise ConflictError("Accepted Analyze Magic training changed")
    if cast.last_at != state.resources.game_time:
        raise ConflictError("Analyze Magic requires consecutive committed concentration")


def resolve_focus(
    runtime: RulesContext, state: PlayState, cast: AnalysisCast, command_id: str
) -> tuple[PlayState, AnalysisCast]:
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
    resources = append(
        state.resources,
        "distraction",
        command_id,
        cast.actor_id,
        AnalysisConcentrationReceipt(
            command_id=command_id, cast_id=cast.cast_id, actor_id=cast.actor_id, check=check
        ),
    )
    cast = cast.model_copy(
        update={
            "distracted": False,
            "status": cast.status if check.outcome.succeeded else "cancelled",
        }
    )
    resources = append(resources, "cast", command_id + ":focus", cast.actor_id, cast)
    return state.model_copy(update={"resources": resources}), cast


def work(
    runtime: RulesContext, state: PlayState, command: WorkAnalyzeMagic, cast: AnalysisCast
) -> PlayState:
    if cast.status != "casting" or cast.seconds + command.seconds > 3600:
        raise ConflictError("Analyze Magic work does not fit the remaining casting interval")
    current(runtime, state, cast)
    resources = state.resources
    hp = next(pool for pool in resources.pools if pool.id == "hp:" + cast.actor_id)
    if hp.injury is not None and hp.injury.shock > 0:
        raise ConflictError("Bounded analysis work does not admit temporal injury shock")
    if (
        resources.scheduled
        or resources.hazards
        or resources.cyclic_attacks
        or resources.cyclic_exposures
        or resources.toxins
        or resources.dependencies
        or resources.survival_tasks
        or resources.illnesses
        or resources.recovery_tasks
        or runtime.rules.npcs is not None
    ):
        raise ConflictError("Bounded uninterrupted analysis does not admit timed hazard carriers")
    energy(state, cast.actor_id)
    state, cast = resolve_focus(runtime, state, cast, command.id)
    if cast.status == "cancelled":
        return state
    token = _WORK.set(cast.cast_id)
    try:
        advanced = runtime.advance(
            state,
            Advance(
                id=command.id + ":casting-clock",
                actor_id=command.actor_id,
                expected_revision=state.resources.revision,
                to=state.resources.game_time + command.seconds,
            ),
        )
    finally:
        _WORK.reset(token)
    if advanced.party != state.party:
        raise ConflictError("Analysis cannot settle newly scheduled party activity")
    if advanced.party.groups:
        group = advanced.party.groups[0]
        advanced = advanced.model_copy(
            update={
                "party": advanced.party.model_copy(
                    update={
                        "groups": (
                            group.model_copy(
                                update={"ready_through": advanced.resources.game_time}
                            ),
                        ),
                    }
                )
            }
        )
    latest_cast = casts(advanced.resources)[cast.cast_id]
    advanced_actor = next(a for a in advanced.actors if a.actor_id == cast.actor_id)
    if latest_cast.status == "cancelled":
        return advanced
    if advanced_actor.available_at > advanced.resources.game_time or advanced_actor.conditions:
        raise ConflictError("Analysis actor became unavailable during work")
    if latest_cast.distracted:
        raise ConflictError("Analysis encountered an unsupported midinterval distraction")
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
            "status": "ready" if cast.seconds + command.seconds == 3600 else "casting",
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
        previous_hp = next(p for p in before.resources.pools if p.id == hp.id)
        injury = hp.current < previous_hp.current
        changed = bool(actor.conditions)
        if resources.game_time != cast.last_at and _WORK.get() != cast.cast_id:
            changed = True
        try:
            require_current(runtime, state, cast)
        except ConflictError, ValidationError:
            changed = True
        if injury and not changed:
            resources = interrupt_casts(
                resources,
                cast.actor_id,
                "injury-checkpoint:" + str(state.revision) + ":" + str(resources.game_time),
                distraction=True,
            )
        if changed:
            resources = interrupt_casts(
                resources,
                cast.actor_id,
                "checkpoint:" + str(state.revision) + ":" + str(resources.game_time),
            )
    return state.model_copy(update={"resources": resources})
