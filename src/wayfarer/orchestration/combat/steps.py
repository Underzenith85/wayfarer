"""Which step handles which command."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    StartBasicEncounter,
    StartEncounter,
    TypedCombatCommand,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.tactical_transitions import finish_defense
from wayfarer.orchestration.combat.context import CombatContext, CombatStep
from wayfarer.orchestration.combat.defense import _defend
from wayfarer.orchestration.combat.encounters import (
    _prepare_encounter,
    _start_basic_encounter,
    _start_encounter,
)
from wayfarer.orchestration.combat.handlers import (
    _choke,
    _critical,
    _explosion,
    _landing,
    _repair,
    _retrieve,
    _unarmed,
)
from wayfarer.orchestration.combat.migration import _declare_basic_facts, _migrate, _migrate_basic
from wayfarer.orchestration.combat.preflight import _prepare_command
from wayfarer.orchestration.combat.roster import _end, _join, _set_opposition, _withdraw
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat
from wayfarer.orchestration.combat.turns import _take_turn

_COMBAT_STEPS: dict[
    str, Callable[[PlayState, TypedCombatCommand, Encounter, CombatContext], CombatStep]
] = {
    "declare_basic_spatial_facts": _declare_basic_facts,
    "migrate_encounter_hex": _migrate,
    "migrate_encounter_basic": _migrate_basic,
    "resolve_weapon_explosion": _explosion,
    "declare_thrown_landing": _landing,
    "continue_critical_miss": _critical,
    "retrieve_equipment": _retrieve,
    "repair_equipment": _repair,
    "resolve_choke_effects": _choke,
    "take_unarmed_turn": _unarmed,
    "join_encounter": _join,
    "set_encounter_opposition": _set_opposition,
    "withdraw_encounter": _withdraw,
    "end_encounter": _end,
    "take_combat_turn": _take_turn,
    "choose_defense": _defend,
}


def reduce_combat(
    state: PlayState, command: TypedCombatCommand, context: CombatContext
) -> tuple[PlayState, CombatResult]:
    state, command, context = _prepare_command(state, command, context)
    if isinstance(command, (StartEncounter, StartBasicEncounter)):
        step = (
            _start_encounter(state, command, context)
            if isinstance(command, StartEncounter)
            else _start_basic_encounter(state, command, context)
        )
        encounters = step.state.encounters + (step.encounter,)
    else:
        encounter = _prepare_encounter(state, command, context)
        step = _COMBAT_STEPS[command.kind](state, command, encounter, context)
        if isinstance(command, ChooseDefense):
            step = replace(
                step,
                encounter=finish_defense(
                    context.play.rules_context, step.state, step.encounter, command
                ),
            )
        encounters = tuple(
            step.encounter if e.id == step.encounter.id else e for e in step.state.encounters
        )
    step, encounters = _settle_combat(step, command, encounters, context)
    return _finish_combat(step, command, encounters, context)
