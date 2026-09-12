"""Basic Set knowledge spell inventory (Characters B249-250)."""

from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 222
COLLEGE: Final = "knowledge"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("analyze-magic", "Analyze Magic", 249),
        ("aura", "Aura", 249),
        ("blur", "Blur", 250),
        ("continual-light", "Continual Light", 249),
        ("counterspell", "Counterspell", 250),
        ("darkness", "Darkness", 250),
        ("detect-magic", "Detect Magic", 249),
        ("dispel-magic", "Dispel Magic", 250),
        ("identify-spell", "Identify Spell", 249),
        ("light", "Light", 249),
        ("seeker", "Seeker", 249),
        ("trace", "Trace", 249),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
