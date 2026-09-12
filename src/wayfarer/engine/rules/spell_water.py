"""Basic Set water spell inventory."""

from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 231
COLLEGE: Final = "water"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("create-water", "Create Water", 253),
        ("destroy-water", "Destroy Water", 253),
        ("fog", "Fog", 253),
        ("icy-weapon", "Icy Weapon", 253),
        ("purify-water", "Purify Water", 253),
        ("seek-water", "Seek Water", 253),
        ("shape-water", "Shape Water", 253),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
