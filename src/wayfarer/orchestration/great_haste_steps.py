"""Selected Great Haste Steps and canonical paused-maneuver completion."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import TYPE_CHECKING

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ResumeInterruptedTurn, TakeCombatTurn
from wayfarer.engine.simulation.combat.concentrate_steps import (
    ConcentrationResolution,
    selected_step,
)
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.explosions import guard as explosion_guard
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.maneuver_budget import begin
from wayfarer.engine.simulation.magic.great_haste_casting import PREFIX as ORIGIN
from wayfarer.engine.simulation.magic.great_haste_casting import (
    CastingOrigin,
    origins,
    subjective_casting,
)
from wayfarer.engine.simulation.magic.great_haste_host import apply_host, bound_cast
from wayfarer.engine.simulation.magic.great_haste_named import binding as named_binding
from wayfarer.engine.simulation.magic.great_haste_named import named_step
from wayfarer.engine.simulation.magic.great_haste_state import RECEIPT, GreatHasteReceipt, save
from wayfarer.engine.simulation.magic.great_haste_step_state import (
    PREFIX,
    RESOLVED,
    CastingStepLease,
    InitialStepCastGreatHaste,
    NamedInitialStepCastGreatHaste,
    NamedOngoingStepCastGreatHaste,
    NamedStepCastGreatHaste,
    OngoingStepCastGreatHaste,
    ResolvedStepDistraction,
    StepCommand,
    cast_command,
    distractions,
    leases,
)
from wayfarer.engine.simulation.magic.initial_step_binding import initial_binding
from wayfarer.engine.simulation.magic.spell_state import (
    PREFIX as SPELL_PREFIX,
)
from wayfarer.engine.simulation.magic.spell_state import (
    RUNTIME_PREFIX as RUNTIME_SPELL_PREFIX,
)
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEvent,
    SpellResult,
    event_id,
    latest,
    parse_event,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep, encounter_for
from wayfarer.orchestration.combat.encounters import _prepare_encounter
from wayfarer.orchestration.combat.preflight import _prepare_command
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat
from wayfarer.orchestration.combat.turns import _take_turn

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def pending_resume(state: PlayState, command: object) -> CastingStepLease | None:
    if not isinstance(command, ResumeInterruptedTurn):
        return None
    encounter = encounter_for(state, command.encounter_id)
    interrupt = encounter.wait_interrupt
    if interrupt is None:
        return None
    encoded = validation.mapping(validation.decode(interrupt.command_json))
    lease = leases(state.resources).get(validation.string(encoded.get("id")))
    if lease is None or lease.completed:
        return None
    saved = TakeCombatTurn.model_validate_json(interrupt.command_json)
    if (lease.encounter_id, lease.command.actor_id) != (encounter.id, interrupt.actor_id):
        raise ConflictError("Great Haste Step lease identity changed")
    if saved.actor_id != lease.command.actor_id or saved.maneuver != "concentrate":
        raise ConflictError("Great Haste Step continuation changed")
    return lease


def _origin_guard(state: PlayState, encounter: Encounter, command: StepCommand) -> None:
    effect = latest(state.resources).get(command.cast_id)
    if command.operation == "start" and effect is not None:
        raise ConflictError("Great Haste cast identity is already recorded")
    if command.operation == "concentrate" and (effect is None or effect.phase != "casting"):
        raise ConflictError("Great Haste Step requires current casting concentration")
    origin = origins(state.resources).get(command.cast_id)
    if command.operation == "concentrate" and (
        origin is None
        or origin.encounter_id != encounter.id
        or origin.turn_index != encounter.turn_index
        or state.resources.game_time != origin.game_time + encounter.round - origin.round
    ):
        raise ConflictError("Great Haste requires consecutive subjective Concentrate opportunities")


def _finish_cast(
    state: PlayState,
    moved: Encounter,
    resources: ResourceState,
    *,
    context: CombatContext,
    original: Encounter,
    lease: CastingStepLease,
    turn: TakeCombatTurn,
) -> tuple[PlayState, ResourceState]:
    # The engine has computed its ordinary maneuver advance, but shared clock and
    # physiology have not settled. Bind casting to the moved pose in this turn.
    witness = moved.model_copy(
        update={
            "round": original.round,
            "turn_index": original.turn_index,
            "maneuver_budget": original.maneuver_budget,
        }
    )
    command = cast_command(lease.command).model_copy(
        update={"id": turn.id, "expected_revision": turn.expected_revision}
    )
    state = state.model_copy(
        update={
            "resources": resources,
            "encounters": tuple(witness if e.id == witness.id else e for e in state.encounters),
        }
    )
    with named_step(lease.command):
        _, spell, bound = bound_cast(context.play.rules_context, state, command, witness)
        if (bound.build_revision, spell.energy) != (lease.build_revision, lease.energy):
            raise ConflictError("Accepted Great Haste Step binding changed")
        if lease.named_origin_json is not None:
            selected = named_binding(state, bound.target_id, command)
            if selected is None or selected.model_dump_json() != lease.named_origin_json:
                raise ConflictError("Accepted named Step subject knowledge changed")
        with subjective_casting():
            state, _ = apply_host(
                context.play.rules_context, state, command, combat_encounter=witness
            )
    if command.operation == "start":
        state = state.model_copy(
            update={
                "resources": save(
                    state.resources,
                    ORIGIN,
                    command.cast_id,
                    command.actor_id,
                    CastingOrigin(
                        cast_id=command.cast_id,
                        encounter_id=witness.id,
                        round=witness.round,
                        turn_index=witness.turn_index,
                        game_time=resources.game_time,
                    ),
                )
            }
        )
    finished = lease.model_copy(update={"completed": True})
    resources = save(
        state.resources, PREFIX, lease.command.id + ":completed", command.actor_id, finished
    )
    return state.model_copy(
        update={"resources": resources, "revision": context.initial_state.revision}
    ), resources


def execute_step(
    state: PlayState,
    turn: TakeCombatTurn,
    encounter: Encounter,
    context: CombatContext,
    lease: CastingStepLease,
    *,
    cancel: bool = False,
) -> CombatStep:
    effect = latest(state.resources).get(lease.command.cast_id)
    actor = next(p for p in encounter.participants if p.actor_id == turn.actor_id)
    interrupted = lease.command.operation == "concentrate" and (
        effect is None
        or effect.phase != "casting"
        or context.resuming
        and not actor.maneuver_state.concentrating
    )
    abort = cancel or interrupted
    if abort:
        turn = turn.model_copy(
            update={
                "maneuver": "do_nothing",
                "destination": None,
                "facing": None,
                "hex_path": (),
                "hex_facing": None,
            }
        )
        if effect is not None and effect.phase == "casting":
            command = cast_command(lease.command).model_copy(
                update={
                    "id": turn.id + ":cancel",
                    "operation": "cancel",
                    "expected_revision": turn.expected_revision,
                }
            )
            state, _ = apply_host(context.play.rules_context, state, command)
        resources = save(
            state.resources,
            PREFIX,
            lease.command.id + ":cancelled",
            turn.actor_id,
            lease.model_copy(update={"completed": True}),
        )
        state = state.model_copy(
            update={"resources": resources, "revision": context.initial_state.revision}
        )
    else:
        if (encounter.round, encounter.turn_index, state.resources.game_time) != (
            lease.round,
            lease.turn_index,
            lease.game_time,
        ):
            raise ConflictError("Accepted Great Haste Step turn or clock changed")
        _origin_guard(state, encounter, lease.command)
    if context.resuming and not abort:
        state = _acknowledge_distraction(state, turn, lease)
    completion = (
        None
        if abort
        else lambda current, moved, resources: _finish_cast(
            current, moved, resources, context=context, original=encounter, lease=lease, turn=turn
        )
    )
    with selected_step():
        step = _take_turn(
            state,
            turn,
            encounter,
            context,
            preserve_concentration=not abort,
            concentrate_completion=completion,
        )
    return _retire_unfinished_step(step, turn, lease, context)


def cast_with_step(
    play: PlayService, before: PlayState, command: StepCommand, *, ritual_step: bool = False
) -> tuple[PlayState, GreatHasteReceipt]:
    encounter = next(
        (e for e in before.encounters if e.status == "active" and command.actor_id in e.turn_order),
        None,
    )
    if encounter is None:
        raise ValidationError("A selected casting Step requires an active encounter")
    if (
        encounter.wait_interrupt
        or encounter.pending_defense
        or encounter.pending_unarmed
        or encounter.blocked_reason
        or encounter.current_actor_id != command.actor_id
    ):
        raise ConflictError("Great Haste must obey the encounter turn and response pause")
    turn = TakeCombatTurn(
        id=command.id,
        actor_id=command.actor_id,
        expected_revision=command.expected_revision,
        encounter_id=encounter.id,
        maneuver="concentrate",
        **command.step.model_dump(),
    )
    context = CombatContext(play, before)
    state, _, context = _prepare_command(before, turn, context)
    encounter = _prepare_encounter(state, turn, context)
    explosion_guard(state.resources)
    _origin_guard(state, encounter, command)
    with initial_binding(
        enabled=isinstance(command, (InitialStepCastGreatHaste, NamedInitialStepCastGreatHaste))
    ):
        _, spell, bound = bound_cast(play.rules_context, state, cast_command(command), encounter)
    ongoing = isinstance(command, (OngoingStepCastGreatHaste, NamedOngoingStepCastGreatHaste))
    if ongoing and bound.skill - 5 * int(bound.mana == "low") < 20:
        raise ValidationError("Larger ongoing casting Step requires ritual base skill 20+")
    if (
        (ritual_step or isinstance(command, NamedStepCastGreatHaste))
        and not isinstance(command, (InitialStepCastGreatHaste, NamedInitialStepCastGreatHaste))
        and not ongoing
    ):
        ritual_skill = bound.skill - 5 * int(bound.mana == "low")
        actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
        distance = (
            len(command.step.hex_path)
            if command.step.hex_path
            else context.engine.distance(actor.position, command.step.destination)
            if command.step.destination is not None
            else 0
        )
        if distance > 1:
            raise ValidationError("Selected casting Step supports at most one yard")
        if command.operation == "concentrate" and ritual_skill < 15:
            raise ValidationError("Ongoing casting Step requires ritual base skill 15+")
    selected_name = named_binding(state, bound.target_id, cast_command(command))
    lease = CastingStepLease(
        command=command,
        ritual_step=ritual_step,
        named_origin_json=selected_name.model_dump_json() if selected_name else None,
        encounter_id=encounter.id,
        build_revision=bound.build_revision,
        energy=spell.energy,
        round=encounter.round,
        turn_index=encounter.turn_index,
        game_time=state.resources.game_time,
    )
    with combat_generation(frozenset({"maneuver-budget"})):
        encounter = begin(play.rules_context, before, encounter)
    resources = save(state.resources, PREFIX, command.id, command.actor_id, lease)
    state = state.model_copy(update={"resources": resources})
    step = execute_step(state, turn, encounter, context, lease)
    encounters = tuple(step.encounter if e.id == encounter.id else e for e in step.state.encounters)
    step, encounters = _settle_combat(step, turn, encounters, context)
    state, _ = _finish_combat(step, turn, encounters, context)
    if step.result.code == "combat.wait_triggered":
        receipt = GreatHasteReceipt(
            command_id=command.id, outcome="paused", game_time=state.resources.game_time
        )
        state = state.model_copy(
            update={
                "resources": save(state.resources, RECEIPT, command.id, command.actor_id, receipt)
            }
        )
    else:
        receipt = GreatHasteReceipt.model_validate_json(
            next(
                e.kind
                for e in state.resources.events
                if e.id == RECEIPT + hashlib.sha256(command.id.encode()).hexdigest()
            )
        )
    return state, receipt


def _acknowledge_distraction(
    state: PlayState, turn: TakeCombatTurn, lease: CastingStepLease
) -> PlayState:
    effect = latest(state.resources).get(lease.command.cast_id)
    if effect is None or effect.phase != "casting":
        return state
    hp = next(p for p in state.resources.pools if p.id == "hp:" + turn.actor_id)
    if not effect.distracted and hp.current >= effect.hp_at_start:
        return state
    witness = distractions(state.resources).get(lease.command.id)
    if (
        witness is None
        or witness.cast_id != effect.cast_id
        or witness.hp != hp.current
        or (witness.spell_event_id, witness.spell_event_digest)
        != _spell_witness(state.resources, effect.cast_id)
        or witness.effect_digest != hashlib.sha256(effect.model_dump_json().encode()).hexdigest()
        or not witness.check.outcome.succeeded
    ):
        return state
    # Only this exact canonical adjudication may acknowledge the distraction.
    record = RuntimeSpellEvent(
        effect=effect.model_copy(update={"distracted": False, "hp_at_start": hp.current}),
        result=SpellResult(outcome="casting"),
    )
    resources = state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=event_id(turn.id + ":step-distraction", effect.spell_id),
                    at=state.resources.game_time,
                    target_id=turn.actor_id,
                    kind=record.model_dump_json(),
                ),
            )
        }
    )
    return state.model_copy(update={"resources": resources})


def record_distractions(
    before: PlayState,
    step: CombatStep,
    command_id: str,
    observed: tuple[ConcentrationResolution, ...],
) -> CombatStep:
    resources = step.resources
    for lease in leases(before.resources).values():
        if lease.completed:
            continue
        effect = latest(resources).get(lease.command.cast_id)
        if effect is None or effect.phase != "casting":
            continue
        for resolution in observed:
            if resolution.actor_id != lease.command.actor_id:
                continue
            record = ResolvedStepDistraction(
                lease_id=lease.command.id,
                cast_id=effect.cast_id,
                command_id=command_id,
                revision=before.revision + 1,
                effect_digest=hashlib.sha256(effect.model_dump_json().encode()).hexdigest(),
                hp=resolution.hp,
                spell_event_id=_spell_witness(resources, effect.cast_id)[0],
                spell_event_digest=_spell_witness(resources, effect.cast_id)[1],
                check=resolution.check,
            )
            resources = save(
                resources,
                RESOLVED,
                command_id + ":" + lease.command.id,
                resolution.actor_id,
                record,
            )
    return replace(
        step, resources=resources, state=step.state.model_copy(update={"resources": resources})
    )


def _spell_witness(resources: ResourceState, cast_id: str) -> tuple[str, str]:
    for event in reversed(resources.events):
        if (
            event.id.startswith((SPELL_PREFIX, RUNTIME_SPELL_PREFIX))
            and parse_event(event).effect.cast_id == cast_id
        ):
            return event.id, hashlib.sha256(event.kind.encode()).hexdigest()
    raise ConflictError("Great Haste Step requires its recorded cast witness")


def _retire_unfinished_step(
    step: CombatStep, turn: TakeCombatTurn, lease: CastingStepLease, context: CombatContext
) -> CombatStep:
    if (
        step.result.code == "combat.wait_triggered"
        or leases(step.resources)[lease.command.id].completed
    ):
        return step
    state = step.state.model_copy(update={"resources": step.resources})
    effect = latest(state.resources).get(lease.command.cast_id)
    if effect is not None and effect.phase == "casting":
        command = cast_command(lease.command).model_copy(
            update={
                "id": turn.id + ":retire",
                "operation": "cancel",
                "expected_revision": turn.expected_revision,
            }
        )
        state, _ = apply_host(context.play.rules_context, state, command)
    resources = save(
        state.resources,
        PREFIX,
        lease.command.id + ":retired",
        turn.actor_id,
        lease.model_copy(update={"completed": True}),
    )
    receipt = GreatHasteReceipt(
        command_id=turn.id, outcome="interrupted", game_time=resources.game_time
    )
    resources = save(resources, RECEIPT, turn.id, turn.actor_id, receipt)
    return replace(
        step,
        state=state.model_copy(
            update={"resources": resources, "revision": context.initial_state.revision}
        ),
        resources=resources,
    )


def has_pending_step(state: PlayState, encounter_id: str) -> bool:
    encounter = encounter_for(state, encounter_id)
    interrupt = encounter.wait_interrupt
    if interrupt is None:
        return False
    encoded = validation.mapping(validation.decode(interrupt.command_json))
    lease = leases(state.resources).get(validation.string(encoded.get("id")))
    return lease is not None and not lease.completed
