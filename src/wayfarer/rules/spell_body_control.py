"""Basic Set body control spell inventory."""

from typing import Final

from wayfarer.rules.catalog import RulesPackage
from wayfarer.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 227
COLLEGE: Final = "body-control"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("clumsiness", "Clumsiness", 244),
        ("deathtouch", "Deathtouch", 245),
        ("hinder", "Hinder", 244),
        ("itch", "Itch", 244),
        ("pain", "Pain", 244),
        ("paralyze-limb", "Paralyze Limb", 244),
        ("rooted-feet", "Rooted Feet", 244),
        ("spasm", "Spasm", 244),
        ("wither-limb", "Wither Limb", 244),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
