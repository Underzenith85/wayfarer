"""Explicit domain dependencies for mechanic resolution; no service or storage handle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.actions import ActionRules, PlayState
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine, hex_template
from wayfarer.engine.simulation.health.symptom_state import projected_build
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.engine.simulation.play_clock import PlayClock, advance_play
from wayfarer.engine.simulation.resources import Advance
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


@dataclass(frozen=True)
class RulesContext:
    rng: RandomSource
    resources: ResourceEngine
    reviewer: PowerReviewer
    rules: ActionRules
    combat: CombatEngine | None
    correct_symptom_attributes: bool = True
    correct_defense_postures: bool = True
    correct_ranged_target_postures: bool = False
    correct_default_knowledge: bool = False
    require_default_knowledge: bool = False
    clock: PlayClock | None = None
    # Scoped to calculations for one already-launched attack, never approval/control.
    attack_source: tuple[str, ValidatedBuild] | None = None
    # Captured only within one response transaction, before reaction-option dice.
    response_attack: tuple[str, AttackRollSpec] | None = None

    def attack_spec(self, pending_id: str, current: AttackRollSpec) -> AttackRollSpec:
        captured = self.response_attack
        return captured[1] if captured is not None and captured[0] == pending_id else current

    def advance(
        self, state: PlayState, command: Advance, *, rng: RandomSource | None = None
    ) -> PlayState:
        return advance_play(self.resources, state, command, rng or self.rng, self.clock)

    def require_hex(self, encounter: Encounter) -> HexBattlefield:
        board = self.hex_map(encounter)
        if board is None:
            raise ValidationError("Encounter requires a hex template")
        return board

    def hex_map(self, encounter: Encounter) -> HexBattlefield | None:
        return hex_template(encounter, self.rules.combat)

    def approved_build(self, state: PlayState, actor_id: str) -> ValidatedBuild:
        actor = next((a for a in state.actors if a.actor_id == actor_id), None)
        if actor is None:
            raise ValidationError("Recovery requires an approved character")
        build, _ = self.reviewer.activate(
            actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
        )
        return projected_build(
            state.resources,
            actor_id,
            build,
            self.reviewer.compiler.definitions,
            correct_attributes=self.correct_symptom_attributes,
        )
