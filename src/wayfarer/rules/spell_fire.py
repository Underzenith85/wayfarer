"""Basic Set fire spell inventory."""

from typing import Final

from wayfarer.rules.catalog import RulesPackage
from wayfarer.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 228
COLLEGE: Final = "fire"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("cold", "Cold", 247),
        ("create-fire", "Create Fire", 246),
        ("deflect-energy", "Deflect Energy", 246),
        ("explosive-fireball", "Explosive Fireball", 247),
        ("extinguish-fire", "Extinguish Fire", 247),
        ("fireball", "Fireball", 247),
        ("heat", "Heat", 247),
        ("ignite-fire", "Ignite Fire", 246),
        ("resist-cold", "Resist Cold", 247),
        ("resist-fire", "Resist Fire", 247),
        ("shape-fire", "Shape Fire", 246),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
