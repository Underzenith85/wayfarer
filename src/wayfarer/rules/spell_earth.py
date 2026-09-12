"""Basic Set earth spell inventory."""

from typing import Final

from wayfarer.rules.catalog import RulesPackage
from wayfarer.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 230
COLLEGE: Final = "earth"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("create-earth", "Create Earth", 246),
        ("earth-to-stone", "Earth to Stone", 245),
        ("entombment", "Entombment", 246),
        ("flesh-to-stone", "Flesh to Stone", 246),
        ("seek-earth", "Seek Earth", 245),
        ("shape-earth", "Shape Earth", 245),
        ("stone-to-earth", "Stone to Earth", 246),
        ("stone-to-flesh", "Stone to Flesh", 246),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
