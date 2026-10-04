"""Real one-second personal casting and separately settled B240 contact."""

import hashlib
from contextvars import ContextVar

from wayfarer.engine.rules.checks import CheckTrace, Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.magic.backfires import apply_backfire
from wayfarer.engine.simulation.magic.deathtouch_effects import apply_deathtouch
from wayfarer.engine.simulation.magic.melee_spell_admission import ready
from wayfarer.engine.simulation.magic.melee_spell_state import (
    CastDeathtouch,
    MeleeCast,
    MeleeSpellCommand,
    MeleeSpellContact,
    MeleeSpellContactResult,
    MeleeSpellReceipt,
    ObserveMeleeMana,
    append,
    cast_event,
    casts,
)
from wayfarer.engine.simulation.magic.melee_staff_carrier import carrier_digest
from wayfarer.engine.simulation.magic.spells import _casting_modifiers, cost_reduction
from wayfarer.engine.simulation.resources import Advance, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ConflictError

_WORK: ContextVar[str | None] = ContextVar("melee_spell_work", default=None)


def work(
    runtime: RulesContext, state: PlayState, command: CastDeathtouch, cast: MeleeCast
) -> tuple[PlayState, MeleeCast]:
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


def _observe(
    state: PlayState, command: ObserveMeleeMana
) -> tuple[ResourceState, MeleeSpellReceipt]:
    resources = state.resources
    if not any(
        entity.id == command.location_id and entity.kind is EntityKind.LOCATION
        for entity in state.world.entities
    ):
        raise ConflictError("Melee mana requires an actual world location")
    if any(
        e.target_id == command.location_id and e.id.startswith("melee-spell:mana:")
        for e in resources.events
    ):
        raise ConflictError("Melee mana observation is immutable")
    resources = append(resources, "mana", command.id, command.location_id, command)
    receipt = MeleeSpellReceipt(
        command_id=command.id, outcome="observed", game_time=resources.game_time
    )
    return resources, receipt


def apply(
    runtime: RulesContext, state: PlayState, command: MeleeSpellCommand
) -> tuple[PlayState, MeleeSpellReceipt]:
    resources = state.resources
    if isinstance(command, ObserveMeleeMana):
        resources, receipt = _observe(state, command)
    else:
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
            cast = MeleeCast(
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
        receipt = MeleeSpellReceipt(
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
    contact: MeleeSpellContact,
    *,
    ordinary_hit: bool,
    actual_defense: Defense,
    defense_check: CheckTrace | None,
    defense_implement_id: str | None,
    critical_row: int | None = None,
) -> tuple[PlayState, Encounter, MeleeSpellContactResult]:
    del critical_row
    charge = casts(state.resources).get(contact.cast_id)
    if charge is None or charge.status != "held" or charge.actor_id != contact.attacker_id:
        raise ConflictError("Melee contact charge is no longer held")
    event = cast_event(state.resources, charge.cast_id)
    if (
        event.id != contact.charge_event_id
        or hashlib.sha256(event.kind.encode()).hexdigest() != contact.charge_digest
        or encounter.id != contact.encounter_id
    ):
        raise ConflictError("Melee contact immutable source identity changed")
    # Caller revalidates immediately before the physical packet. Its own
    # injury/drop consequences cannot retroactively undo accepted contact.
    defended = defense_check is not None and defense_check.outcome.succeeded
    arc = defended and (
        actual_defense == "block" or (actual_defense == "parry" and defense_implement_id is None)
    )
    trigger = ordinary_hit or arc
    dice: tuple[int, ...] = ()
    injury = 0
    injury_checks: tuple[CheckTrace, ...] = ()
    injury_check_reasons: tuple[str, ...] = ()
    hp_before = next(
        p.current for p in state.resources.pools if p.id == "hp:" + contact.defender_id
    )
    if trigger:
        state, dice, injury, injury_checks, injury_check_reasons = apply_deathtouch(
            runtime,
            state,
            command_id=contact.pending_id + ":deathtouch",
            defender_id=contact.defender_id,
            energy=contact.energy,
        )
        charge = charge.model_copy(update={"status": "spent"})
        state = state.model_copy(
            update={
                "resources": append(
                    state.resources, "cast", contact.pending_id + ":spent", charge.actor_id, charge
                )
            }
        )
    hp_after = next(p.current for p in state.resources.pools if p.id == "hp:" + contact.defender_id)
    result = MeleeSpellContactResult(
        attacker_id=contact.attacker_id,
        hp_before=hp_before,
        hp_after=hp_after,
        pending_id=contact.pending_id,
        cast_id=contact.cast_id,
        defender_id=contact.defender_id,
        triggered=trigger,
        status="spent" if trigger else "held",
        dice=dice,
        injury=injury,
        injury_checks=injury_checks,
        injury_check_reasons=injury_check_reasons,
    )
    state = state.model_copy(
        update={
            "resources": append(
                state.resources, "result", contact.pending_id, contact.defender_id, result
            )
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
