"""Explicit domain dependencies for mechanic resolution; no service or storage handle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.actions import ActionRules, PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine, hex_template
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
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
        return build
