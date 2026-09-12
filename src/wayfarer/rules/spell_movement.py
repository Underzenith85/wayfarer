"""Basic Set movement spell inventory."""

from typing import Final

from wayfarer.rules.catalog import RulesPackage
from wayfarer.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 223
COLLEGE: Final = "movement"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("apportation", "Apportation", 251),
        ("armor", "Armor", 253),
        ("deflect-missile", "Deflect Missile", 251),
        ("great-haste", "Great Haste", 251),
        ("haste", "Haste", 251),
        ("lockmaster", "Lockmaster", 251),
        ("magelock", "Magelock", 253),
        ("shield", "Shield", 252),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
