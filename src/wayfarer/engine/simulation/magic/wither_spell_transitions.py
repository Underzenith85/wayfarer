"""Real one-second personal casting and separately settled B240 contact."""

import hashlib
from contextvars import ContextVar
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.location import Hand, HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.magic.backfires import apply_backfire
from wayfarer.engine.simulation.magic.melee_staff_carrier import carrier_digest
from wayfarer.engine.simulation.magic.spells import _casting_modifiers, cost_reduction
from wayfarer.engine.simulation.magic.wither_spell_admission import ready
from wayfarer.engine.simulation.magic.wither_spell_commands import CastWitherLimb
from wayfarer.engine.simulation.magic.wither_spell_effects import resolve_contact
from wayfarer.engine.simulation.magic.wither_spell_state import (
    WitherLimbCast,
    WitherLimbContact,
    WitherLimbContactResult,
    WitherLimbReceipt,
    append,
    cast_event,
    casts,
)
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError

_WORK: ContextVar[str | None] = ContextVar("wither_spell_work", default=None)


def work(
    runtime: RulesContext, state: PlayState, command: CastWitherLimb, cast: WitherLimbCast
) -> tuple[PlayState, WitherLimbCast]:
    if cast.credited_seconds or state.resources.game_time != cast.started_at:
        raise ConflictError("Wither Limb requires one actual consecutive Concentrate second")
    if cast.distracted:
        raise ConflictError("Bounded Wither Limb does not admit casting distraction")
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
        raise ConflictError("Bounded Wither Limb does not admit timed hazard carriers")
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
        raise ConflictError("Wither Limb concentration cannot settle changed party activity")
    actor = next(a for a in advanced.actors if a.actor_id == command.actor_id)
    if actor.available_at > advanced.resources.game_time or actor.conditions:
        raise ConflictError("Wither Limb caster became unavailable")
    if ready(runtime, advanced, command) != (cast.skill, cast.build_revision, cast.carrier_digest):
        raise ConflictError("Wither Limb source changed during actual work")
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
    runtime: RulesContext, state: PlayState, command: CastWitherLimb
) -> tuple[PlayState, WitherLimbReceipt]:
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
            raise ConflictError("Wither Limb requires selected FP before casting")
        cast = WitherLimbCast(
            actor_id=command.actor_id,
            cast_id=command.cast_id,
            command_id=command.id,
            status="casting",
            carrier=command.carrier,
            carrier_digest=digest,
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
                    "Bounded Wither Limb does not admit unresolved casting distraction"
                )
            fp = next(p for p in resources.pools if p.id == "fp:" + command.actor_id)
            if fp.current < max(0, existing.energy - cost_reduction(existing.skill)):
                raise ConflictError("Wither Limb cannot pay selected energy")
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
                    spell_id="wither-limb",
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
    receipt = WitherLimbReceipt(
        command_id=command.id,
        cast_id=command.cast_id,
        outcome=cast.status,
        game_time=resources.game_time,
        energy_spent=cast.paid_fp if command.operation == "complete" else 0,
    )
    resources = append(resources, "receipt", command.id, command.actor_id, receipt)
    return state.model_copy(update={"resources": resources}), receipt


def finish_contact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    contact: WitherLimbContact,
    *,
    ordinary_hit: bool,
    actual_defense: Defense,
    defense_check: CheckTrace | None,
    defense_implement_id: str | None,
    critical_row: int | None = None,
    resolved_location: HumanLocation | None = None,
    defense_hand: Hand | None = None,
) -> tuple[PlayState, Encounter, WitherLimbContactResult]:
    del critical_row, defense_hand
    charge = casts(state.resources).get(contact.cast_id)
    if charge is None or charge.status != "held" or charge.actor_id != contact.attacker_id:
        raise ConflictError("Wither contact charge is no longer held")
    event = cast_event(state.resources, charge.cast_id)
    if (
        event.id != contact.charge_event_id
        or hashlib.sha256(event.kind.encode()).hexdigest() != contact.charge_digest
        or encounter.id != contact.encounter_id
    ):
        raise ConflictError("Wither contact immutable source identity changed")
    defended = defense_check is not None and defense_check.outcome.succeeded
    if defended and (
        actual_defense == "block" or (actual_defense == "parry" and defense_implement_id is None)
    ):
        raise ConflictError("Wither armor-ignoring arc requires a qualified actual limb contact")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + contact.defender_id)
    result = WitherLimbContactResult(
        attacker_id=contact.attacker_id,
        hp_before=hp.current,
        hp_after=hp.current,
        pending_id=contact.pending_id,
        cast_id=contact.cast_id,
        defender_id=contact.defender_id,
        triggered=ordinary_hit
        and resolved_location in ("left-arm", "right-arm")
        and hp.injury is not None
        and not hp.injury.dead,
        status="spent" if ordinary_hit else "held",
        outcome="no-effect" if ordinary_hit else "held",
        location=resolved_location,
    )
    if ordinary_hit:
        state = state.model_copy(
            update={
                "resources": append(
                    state.resources,
                    "cast",
                    contact.pending_id + ":spent",
                    charge.actor_id,
                    charge.model_copy(update={"status": "spent"}),
                )
            }
        )
        if (
            resolved_location in ("left-arm", "right-arm")
            and hp.injury is not None
            and not hp.injury.dead
        ):
            state, encounter, result = _wither_contact(
                runtime, state, encounter, contact, charge, result, resolved_location
            )
    state = state.model_copy(
        update={
            "resources": append(
                state.resources, "result", contact.pending_id, contact.defender_id, result
            )
        }
    )
    return state, encounter, result


def _wither_contact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    contact: WitherLimbContact,
    charge: WitherLimbCast,
    result: WitherLimbContactResult,
    location: Literal["left-arm", "right-arm"],
) -> tuple[PlayState, Encounter, WitherLimbContactResult]:
    rolled = resolve_contact(
        runtime,
        state,
        caster_id=contact.attacker_id,
        target_id=contact.defender_id,
        skill=charge.skill,
    )
    result = result.model_copy(
        update={
            "outcome": rolled.outcome,
            "contact_check": rolled.contact_check,
            "resistance_check": rolled.resistance_check,
            "contest_winner": rolled.contest_winner,
        }
    )
    if rolled.contact_check.outcome is Outcome.CRITICAL_FAILURE:
        compiled = build(runtime, state, contact.attacker_id)
        assert compiled.statistics is not None
        resources = apply_backfire(
            state.resources,
            command_id=contact.pending_id + ":contact-backfire",
            actor_id=contact.attacker_id,
            cast_id=contact.cast_id,
            spell_id="wither-limb",
            ht=compiled.statistics.ht,
            severity="normal",
            rng=runtime.rng,
        )
        state = state.model_copy(update={"resources": resources})
    if rolled.outcome == "withered":
        # deferred: the independently owned canonical limb adapter joins this effect family.
        from wayfarer.engine.simulation.magic.wither_cripple_effects import apply_wither_arm

        state, encounter, cripple = apply_wither_arm(
            runtime,
            state,
            encounter,
            effect_id=contact.pending_id + ":wither-limb",
            actor_id=contact.defender_id,
            location=location,
        )
        result = result.model_copy(
            update={
                "lasting_id": cripple.lasting_id,
                "dice": cripple.dice,
                "injury": cripple.injury,
                "hp_before": cripple.hp_before,
                "hp_after": cripple.hp_after,
                "injury_checks": cripple.injury_checks,
                "injury_check_reasons": cripple.injury_check_reasons,
                "dropped_item_ids": cripple.dropped_item_ids,
                "grip_checks": cripple.grip_checks,
            }
        )
    return state, encounter, result


def checkpoint(state: PlayState, *, before: PlayState | None = None) -> PlayState:
    resources = state.resources
    for cast in casts(resources).values():
        if cast.status not in ("casting", "held"):
            continue
        try:
            current = carrier_digest(state, cast.actor_id, cast.carrier)
            valid = current == cast.carrier_digest
        except ConflictError:
            valid = False
        hp = next(p for p in resources.pools if p.id == "hp:" + cast.actor_id)
        outside_time = (
            cast.status == "casting"
            and state.resources.game_time != cast.started_at + cast.credited_seconds
            and _WORK.get() != cast.cast_id
        )
        hurt = cast.status == "casting" and hp.current < cast.hp_at_start
        if not valid or outside_time or (hurt and not cast.distracted):
            updated = cast.model_copy(
                update={"status": "dissipated"}
                if not valid
                else {"status": "cancelled"}
                if outside_time
                else {"distracted": True}
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
