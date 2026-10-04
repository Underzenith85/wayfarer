"""Isolated real one-second selected-hand Deathtouch producer."""

import hashlib
from contextvars import ContextVar

from wayfarer.engine.rules.checks import Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.magic.backfires import apply_backfire
from wayfarer.engine.simulation.magic.hand_melee_spell_admission import mana_witness, ready
from wayfarer.engine.simulation.magic.hand_melee_spell_state import (
    CastHandDeathtouch,
    HandMeleeCast,
    HandMeleeSpellCommand,
    HandMeleeSpellReceipt,
    append,
    casts,
)
from wayfarer.engine.simulation.magic.spells import _casting_modifiers, cost_reduction
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError

_WORK: ContextVar[str | None] = ContextVar("hand_melee_spell_work", default=None)


def work(
    runtime: RulesContext, state: PlayState, command: CastHandDeathtouch, cast: HandMeleeCast
) -> tuple[PlayState, HandMeleeCast]:
    if cast.credited_seconds or state.resources.game_time != cast.started_at:
        raise ConflictError("Deathtouch requires one actual consecutive Concentrate second")
    if cast.distracted:
        raise ConflictError("Bounded Deathtouch does not admit casting distraction")
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
        raise ConflictError("Bounded Deathtouch does not admit timed hazard carriers")
    token = _WORK.set(cast.cast_id)
    try:
        advanced = runtime.advance(
            state,
            Advance(
                id=command.id + ":concentration-second",
                actor_id=command.actor_id,
                expected_revision=r.revision,
                to=cast.ready_at,
            ),
        )
    finally:
        _WORK.reset(token)
    if advanced.party != state.party:
        raise ConflictError("Deathtouch concentration cannot settle changed party activity")
    actor = next(a for a in advanced.actors if a.actor_id == command.actor_id)
    if actor.available_at > advanced.resources.game_time or actor.conditions:
        raise ConflictError("Deathtouch caster became unavailable")
    if ready(runtime, advanced, command) != (cast.skill, cast.build_revision, cast.carrier_digest):
        raise ConflictError("Deathtouch source changed during actual work")
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
    return advanced, cast.model_copy(update={"credited_seconds": 1})


def apply(
    runtime: RulesContext, state: PlayState, command: HandMeleeSpellCommand
) -> tuple[PlayState, HandMeleeSpellReceipt]:
    resources = state.resources
    existing = casts(resources).get(command.cast_id)
    if command.operation == "cancel":
        if (
            existing is None
            or existing.actor_id != command.actor_id
            or existing.status not in ("casting", "held")
        ):
            raise ConflictError("Melee cast is unavailable for cancellation")
        cast = existing.model_copy(update={"status": "cancelled"})
    elif command.operation == "start":
        if existing is not None:
            raise ConflictError("Melee cast identity is immutable")
        skill, revision, digest = ready(runtime, state, command)
        hp = next(p for p in resources.pools if p.id == "hp:" + command.actor_id)
        fp = next(p for p in resources.pools if p.id == "fp:" + command.actor_id)
        if fp.current < max(0, command.energy - cost_reduction(skill)):
            raise ConflictError("Deathtouch requires selected FP before casting")
        witness = mana_witness(state, command.actor_id)
        cast = HandMeleeCast(
            actor_id=command.actor_id,
            cast_id=command.cast_id,
            command_id=command.id,
            status="casting",
            carrier=command.carrier,
            carrier_digest=digest,
            mana_event_id=witness.id,
            mana_event_digest=hashlib.sha256(witness.kind.encode()).hexdigest(),
            build_revision=revision,
            skill=skill,
            energy=command.energy,
            started_at=resources.game_time,
            ready_at=resources.game_time + 1,
            hp_at_start=hp.current,
        )
    else:
        if (
            existing is None
            or existing.actor_id != command.actor_id
            or existing.status != "casting"
            or existing.energy != command.energy
            or existing.carrier != command.carrier
        ):
            raise ConflictError("Melee casting continuation does not match accepted cast")
        skill, revision, digest = ready(runtime, state, command)
        if (skill, revision, digest) != (
            existing.skill,
            existing.build_revision,
            existing.carrier_digest,
        ):
            raise ConflictError("Melee casting source or carrier changed")
        if command.operation == "concentrate":
            state, cast = work(runtime, state, command, existing)
            resources = state.resources
        else:
            if resources.game_time != existing.ready_at or existing.credited_seconds != 1:
                raise ConflictError(
                    "Melee completion requires a credited actual Concentrate second"
                )
            hp = next(p for p in resources.pools if p.id == "hp:" + command.actor_id)
            if existing.distracted or hp.current < existing.hp_at_start:
                raise ConflictError(
                    "Bounded Deathtouch does not admit unresolved casting distraction"
                )
            fp = next(p for p in resources.pools if p.id == "fp:" + command.actor_id)
            if fp.current < max(0, existing.energy - cost_reduction(existing.skill)):
                raise ConflictError("Deathtouch cannot pay selected energy")
            check = success_roll(
                "gurps-basic-set-4e-2004",
                existing.skill,
                _casting_modifiers(resources, command.actor_id, check_symptoms=True),
                rng=runtime.rng,
            )
            compiled = build(runtime, state, command.actor_id)
            assert compiled.statistics is not None
            critical = check.outcome is Outcome.CRITICAL_SUCCESS
            base_cost = max(0, existing.energy - cost_reduction(existing.skill))
            cost = (
                0
                if critical
                else min(1, base_cost)
                if check.outcome is Outcome.FAILURE
                else base_cost
            )
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
                    raise ConflictError("Melee spell energy payment changed")
            if check.outcome is Outcome.CRITICAL_FAILURE:
                resources = apply_backfire(
                    resources,
                    command_id=command.id,
                    actor_id=command.actor_id,
                    cast_id=command.cast_id,
                    spell_id="deathtouch",
                    ht=compiled.statistics.ht,
                    severity="normal",
                    rng=runtime.rng,
                )
            cast = existing.model_copy(
                update={
                    "status": "held" if check.outcome.succeeded else "failed",
                    "completed_at": resources.game_time,
                    "check": check,
                    "paid_fp": cost,
                }
            )
    resources = append(resources, "cast", command.id, command.actor_id, cast)
    receipt = HandMeleeSpellReceipt(
        command_id=command.id,
        cast_id=command.cast_id,
        outcome=cast.status,
        game_time=resources.game_time,
        energy_spent=cast.paid_fp if command.operation == "complete" else 0,
    )
    resources = append(resources, "receipt", command.id, command.actor_id, receipt)
    return state.model_copy(update={"resources": resources}), receipt


def checkpoint(
    runtime: RulesContext, state: PlayState, *, before: PlayState | None = None
) -> PlayState:
    del runtime, before
    resources = state.resources
    for cast in casts(resources).values():
        if cast.status != "casting":
            continue
        hp = next(p for p in resources.pools if p.id == "hp:" + cast.actor_id)
        outside_time = (
            resources.game_time != cast.started_at + cast.credited_seconds
            and _WORK.get() != cast.cast_id
        )
        hurt = hp.current < cast.hp_at_start
        if outside_time or (hurt and not cast.distracted):
            updated = cast.model_copy(
                update={"status": "cancelled"} if outside_time else {"distracted": True}
            )
            resources = append(
                resources,
                "cast",
                "checkpoint:"
                + str(state.revision)
                + ":"
                + str(resources.game_time)
                + ":"
                + cast.cast_id,
                cast.actor_id,
                updated,
            )
    return state.model_copy(update={"resources": resources})
