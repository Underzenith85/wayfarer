"""Basic Set mind control spell inventory."""

from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 229
COLLEGE: Final = "mind-control"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("command", "Command", 251),
        ("daze", "Daze", 250),
        ("foolishness", "Foolishness", 250),
        ("forgetfulness", "Forgetfulness", 250),
        ("mass-daze", "Mass Daze", 251),
        ("mass-sleep", "Mass Sleep", 251),
        ("sleep", "Sleep", 251),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
