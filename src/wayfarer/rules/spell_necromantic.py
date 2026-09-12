"""Basic Set necromantic spell inventory."""

from typing import Final

from wayfarer.rules.catalog import RulesPackage
from wayfarer.rules.spell_colleges import CollegeSpellBinding, college_package

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
