"""Basic Set body control spell inventory."""

from dataclasses import replace
from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.magic.colleges import CollegeSpellBinding, college_package, learning_spec
from wayfarer.engine.rules.types.skill import PrerequisiteGroup, PrerequisiteKind, SkillPrerequisite

ISSUE: Final = 227
COLLEGE: Final = "body-control"
LEARNING: Final = {
    "itch": learning_spec(244),
    "spasm": learning_spec(244, spells=("itch",)),
    "pain": learning_spec(244, spells=("spasm",)),
    "clumsiness": learning_spec(244, spells=("spasm",)),
    "hinder": replace(
        learning_spec(244),
        prerequisite_groups=(
            PrerequisiteGroup(
                tuple(
                    SkillPrerequisite("spell:" + key, 1, PrerequisiteKind.PURCHASED_DEFINITION)
                    for key in ("clumsiness", "haste")
                )
            ),
        ),
    ),
    "rooted-feet": learning_spec(244, spells=("hinder",)),
    # The five other Body Control witnesses are validated privately by the compiler.
    "paralyze-limb": learning_spec(244, magery=1, spells=("pain",)),
    "wither-limb": learning_spec(244, magery=2, spells=("paralyze-limb",)),
    "deathtouch": learning_spec(245, spells=("wither-limb",)),
}
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE, learning=LEARNING[key])
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
