"""Separate B245 magical HP packet; physical blow modifiers never enter here."""

from wayfarer.engine.rules.checks import CheckTrace, draw_dice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.melee_spell_admission import admit_target
from wayfarer.engine.simulation.rules_context import RulesContext


def apply_deathtouch(
    runtime: RulesContext, state: PlayState, *, command_id: str, defender_id: str, energy: int
) -> tuple[PlayState, tuple[int, ...], int, tuple[CheckTrace, ...], tuple[str, ...]]:
    admit_target(runtime, state, defender_id, after_physical_contact=True)
    compiled = build(runtime, state, defender_id)
    assert compiled.statistics is not None
    dice = draw_dice(runtime.rng, energy)
    # Ordinary-human admission excludes injury transformations. This x1,
    # locationless HP adapter is not a new source damage-type classification.
    resources, result = apply_injury(
        state.resources,
        Wound(
            id=command_id,
            actor_id=defender_id,
            expected_revision=state.resources.revision,
            basic_damage=sum(dice),
            resistance=0,
            damage_type="cr",
        ),
        ht=compiled.statistics.ht,
        rng=runtime.rng,
        system=True,
    )
    return (
        state.model_copy(update={"resources": resources}),
        dice,
        result.injury,
        tuple(c.check for c in result.checks),
        tuple(c.reason for c in result.checks),
    )
