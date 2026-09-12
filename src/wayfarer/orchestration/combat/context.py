"""What one combat command carries through its transaction."""

from __future__ import annotations

from dataclasses import dataclass

from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import MigrateEncounterHex, TypedCombatCommand
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService


def encounter_for(state: PlayState, encounter_id: str) -> Encounter:
    """The named encounter in this checkpoint, or a validation failure."""
    encounter = next((e for e in state.encounters if e.id == encounter_id), None)
    if encounter is None:
        raise ValidationError("Unknown encounter")
    return encounter


@dataclass(frozen=True)
class CombatContext:
    """Command-scoped inputs; never carries a mutable transaction callback frame."""

    play: PlayService
    initial_state: PlayState
    resuming: bool = False
    reaction: bool = False

    @property
    def engine(self) -> CombatEngine:
        engine = self.play.engine.combat
        assert engine is not None
        return engine


@dataclass(frozen=True)
class CombatStep:
    state: PlayState
    encounter: Encounter
    resources: ResourceState
    result: CombatResult
    blast_deferred_ticks: int = 0
    defense_before: Encounter | None = None


def _bind_combat_command(
    campaign: Campaign,
    command: TypedCombatCommand,
    play: PlayService,
) -> tuple[PlayService, PlayState, TypedCombatCommand]:
    before = play._load(campaign)
    if isinstance(command, MigrateEncounterHex):
        from wayfarer.orchestration.battlefield_templates import prepare

        return prepare(campaign, play, before, command)
    return play, before, command
