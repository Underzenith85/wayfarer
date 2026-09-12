"""Basic Set air spell inventory."""

from typing import Final

from wayfarer.rules.catalog import RulesPackage
from wayfarer.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 226
COLLEGE: Final = "air"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("breathe-water", "Breathe Water", 243),
        ("create-air", "Create Air", 243),
        ("earth-to-air", "Earth to Air", 243),
        ("lightning", "Lightning", 244),
        ("no-smell", "No-Smell", 243),
        ("predict-weather", "Predict Weather", 243),
        ("purify-air", "Purify Air", 243),
        ("shape-air", "Shape Air", 243),
        ("stench", "Stench", 244),
        ("walk-on-air", "Walk on Air", 243),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
