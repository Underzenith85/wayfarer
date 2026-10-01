"""Basic Set necromantic spell inventory."""

from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.magic.colleges import CollegeSpellBinding, college_package, learning_spec
from wayfarer.engine.rules.types.skill import Difficulty, Specialty

ISSUE: Final = 225
COLLEGE: Final = "necromantic"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("banish", "Banish", 252),
        ("death-vision", "Death Vision", 251),
        ("planar-summons", "Planar Summons", 247),
        ("plane-shift", "Plane Shift", 248),
        ("sense-spirit", "Sense Spirit", 252),
        ("summon-demon", "Summon Demon", 252),
        ("summon-spirit", "Summon Spirit", 252),
        ("turn-zombie", "Turn Zombie", 252),
        ("zombie", "Zombie", 252),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)


def plane_bindings(plane: str) -> tuple[CollegeSpellBinding, ...]:
    """B247-248: each plane has its own prerequisite and Plane Shift skill."""
    if not plane or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in plane):
        raise ValueError("Plane identity needs a nonempty catalog slug")
    return (
        CollegeSpellBinding(
            "planar-summons:" + plane,
            "Planar Summons (" + plane + ")",
            247,
            "gate",
            learning=learning_spec(
                247, magery=1, colleges=10, specialty=Specialty("spell:planar-summons", plane)
            ),
        ),
        CollegeSpellBinding(
            "plane-shift:" + plane,
            "Plane Shift (" + plane + ")",
            248,
            "gate",
            learning=learning_spec(
                248,
                difficulty=Difficulty.VERY_HARD,
                spells=("planar-summons:" + plane,),
                specialty=Specialty("spell:plane-shift", plane),
            ),
        ),
    )
