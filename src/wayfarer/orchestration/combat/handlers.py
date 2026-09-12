"""Short commands that resolve in one engine call."""

from __future__ import annotations

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    ContinueCriticalMiss,
    DeclareThrownLanding,
    RepairEquipment,
    ResolveChokeEffects,
    ResolveWeaponExplosion,
    RetrieveEquipment,
    TakeUnarmedTurn,
    TypedCombatCommand,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.orchestration.combat.context import CombatContext, CombatStep


def _explosion(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, ResolveWeaponExplosion)
    from wayfarer.engine.simulation.combat.thrown.explosions import resolve_blast

    state, encounter, blast_deferred_ticks = resolve_blast(
        play.rules_context,
        state,
        encounter,
        blast_id=command.blast_id,
        command_id=command.id,
        responses=command.responses,
        object_cover=command.object_cover,
        object_sizes=command.object_sizes,
        center=command.center,
        environment=command.environment,
    )
    resources = state.resources
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.weapon_explosion_resolved",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result, blast_deferred_ticks)


def _landing(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, DeclareThrownLanding)
    from wayfarer.engine.simulation.combat.thrown.items import declare_landing

    resources = declare_landing(
        play.rules_context, state, encounter, command.item_id, command.landing, command.id
    )
    state = state.model_copy(update={"resources": resources})
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.thrown_landing_declared",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _critical(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, ContinueCriticalMiss)
    from wayfarer.engine.simulation.combat.criticals.continuation import continue_critical

    state, encounter, continuation = continue_critical(
        play.rules_context,
        state,
        encounter,
        critical_id=command.critical_id,
        command_id=command.id,
        stage=command.stage,
    )
    resources = state.resources
    result = CombatResult(
        encounter_id=encounter.id,
        code="critical." + continuation.status,
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _retrieve(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, RetrieveEquipment)
    from wayfarer.engine.simulation.equipment.retrieval import (
        retrieve as retrieve_field,
    )

    state, retrieval_task = retrieve_field(
        play.rules_context,
        state,
        encounter,
        actor_id=command.actor_id,
        item_id=command.item_id,
        command_id=command.id,
        stage=command.stage,
        task_id=command.task_id,
    )
    resources = state.resources
    result = CombatResult(
        encounter_id=encounter.id,
        code="equipment.retrieval_" + retrieval_task.status,
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _repair(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, RepairEquipment)
    from wayfarer.engine.simulation.equipment.repair_transitions import repair

    state, task = repair(
        play.rules_context,
        state,
        actor_id=command.actor_id,
        item_id=command.item_id,
        command_id=command.id,
        stage=command.stage,
        task_id=command.task_id,
    )
    resources = state.resources
    result = CombatResult(
        encounter_id=encounter.id,
        code="equipment.repair_" + task.status,
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _choke(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, ResolveChokeEffects)
    from wayfarer.engine.simulation.combat.unarmed.choke import resolve_choke

    state, result = resolve_choke(play.rules_context, state, encounter, command)
    resources = state.resources
    return CombatStep(state, encounter, resources, result)


def _unarmed(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, (TakeUnarmedTurn, ChooseDefense))
    from wayfarer.engine.simulation.combat.unarmed.attack import execute_unarmed

    state, encounter, result = execute_unarmed(play.rules_context, state, encounter, command)
    resources = state.resources
    return CombatStep(state, encounter, resources, result)
