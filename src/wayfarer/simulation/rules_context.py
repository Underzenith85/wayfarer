"""Explicit domain dependencies for mechanic resolution; no service or storage handle."""

from dataclasses import dataclass

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.character.power import PowerReviewer
from wayfarer.errors import ValidationError
from wayfarer.rules.checks import RandomSource
from wayfarer.simulation.actions import ActionRules, PlayState
from wayfarer.simulation.combat import CombatEngine, Encounter, hex_template
from wayfarer.simulation.hex_geometry import HexBattlefield
from wayfarer.simulation.resources import ResourceEngine


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
