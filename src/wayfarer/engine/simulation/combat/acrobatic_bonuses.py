"""B174 task bonuses for the B375 defensive reaction, not temporary DX loss."""

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.simulation.combat.generations import acrobatic_trait_bonuses_enabled


def modifiers(compiled: ValidatedBuild, skill_id: str) -> tuple[Modifier, ...]:
    if not acrobatic_trait_bonuses_enabled():
        return ()
    owned = {p.definition_id for p in compiled.purchases}
    result: tuple[Modifier, ...] = ()
    if "trait:advantage:perfect-balance" in owned:
        result += (Modifier(1, "Perfect Balance", "trait:advantage:perfect-balance", "B174/B74"),)
    if skill_id == "skill:aerobatics" and "trait:advantage:3d-spatial-sense" in owned:
        result += (Modifier(2, "3D Spatial Sense", "trait:advantage:3d-spatial-sense", "B174/B34"),)
    return result
