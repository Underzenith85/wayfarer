"""Breaking free of an entangling weapon inside the encounter transaction (#354).

Escape is a Ready maneuver: the victim spends its turn struggling and the result
is a contest resolved by the existing check service, not a ruling. The attempt
and its outcome live in the encounter checkpoint, so a restart or a retried
command reproduces the same struggle instead of restarting it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.errors import ValidationError
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import CombatEngine, Encounter
from wayfarer.simulation.entangle import escape

if TYPE_CHECKING:
    from wayfarer.simulation.rules_context import RulesContext


def escape_binding(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str
) -> Encounter:
    from wayfarer.simulation.mechanics.gurps_melee import build, catalog

    participant = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if participant is None or participant.entangled is None:
        raise ValidationError("Nothing is binding this actor")
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    skill_id = participant.entangled.escape_skill_id
    trained = None
    if skill_id is not None:
        # A named escape skill is only used when the victim actually has a level
        # in it; naming a skill never grants one.
        value = next((v for v in compiled.sheet.values if v.target == skill_id), None)
        trained = int(value.value) if value is not None else None
    freed, _ = escape(
        catalog(runtime).profile_id,
        participant,
        strength=compiled.statistics.st,
        skill=trained,
        rng=runtime.rng,
    )
    return CombatEngine._replace(encounter, freed)
