"""Basic Set healing spell inventory."""

from typing import Final

from wayfarer.rules.catalog import RulesPackage
from wayfarer.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 224
COLLEGE: Final = "healing"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("awaken", "Awaken", 248),
        ("great-healing", "Great Healing", 248),
        ("lend-energy", "Lend Energy", 248),
        ("lend-vitality", "Lend Vitality", 248),
        ("major-healing", "Major Healing", 248),
        ("minor-healing", "Minor Healing", 248),
        ("recover-energy", "Recover Energy", 248),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
