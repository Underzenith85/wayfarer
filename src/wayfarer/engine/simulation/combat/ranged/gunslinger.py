"""Basic Set Characters, third printing, B58: unaimed Gunslinger Accuracy."""

from collections.abc import Mapping

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.skills.mundane.ranged import PROCEDURES
from wayfarer.engine.simulation.equipment.catalog import RangedMode


def accuracy_bonus(
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    weapon: RangedMode,
    *,
    aimed: bool = False,
    suppressed: bool = False,
) -> int:
    """Only source-bound weapon skills qualify; muscle-powered missiles do not.

    B58 uses the weapon's RoF 1-3 classification for one-handed single shots.
    A high-RoF weapon still gets half Acc when only one round is declared.
    Callers use this instead of (never on top of) the ordinary Aim bonus.
    """
    procedure = PROCEDURES.get(weapon.skill_id)
    if (
        aimed
        or suppressed
        or procedure is None
        or procedure.specialty is None
        or procedure.specialty.family not in {"beam-weapons", "gunner", "guns", "liquid-projector"}
        or weapon.thrown
        or weapon.skill_id == "skill:gunner-catapult"
        or not any(
            effect.definition_id == "trait:advantage:gunslinger"
            for effect in mundane_trait_effects(build, definitions)
        )
    ):
        return 0
    if weapon.hands == 1 and weapon.rate_of_fire <= 3:
        return weapon.accuracy
    return (weapon.accuracy + 1) // 2
