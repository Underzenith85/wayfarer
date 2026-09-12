"""Allowlisted player views and no-dice legal-choice previews for tactical v1."""

from __future__ import annotations

from wayfarer.engine.simulation.combat.visibility import visible_actors
from wayfarer.orchestration.tactical_view.basic_choices import basic_choices
from wayfarer.orchestration.tactical_view.hex_choices import choices
from wayfarer.orchestration.tactical_view.preview import preview
from wayfarer.orchestration.tactical_view.projection import (
    legacy_encounter,
    project,
    snapshot,
)
from wayfarer.orchestration.tactical_view.records import (
    TacticalActor,
    TacticalChoice,
    TacticalEncounter,
    TacticalGrip,
    TacticalSnapshot,
)

__all__ = [
    "TacticalActor",
    "TacticalChoice",
    "TacticalEncounter",
    "TacticalGrip",
    "TacticalSnapshot",
    "basic_choices",
    "choices",
    "legacy_encounter",
    "preview",
    "project",
    "snapshot",
    "visible_actors",
]
