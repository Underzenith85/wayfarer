"""Basic Set enchantment spell inventory (Campaigns B480-481)."""

from typing import Final

from wayfarer.rules.catalog import RulesPackage
from wayfarer.rules.spell_colleges import (
    CAMPAIGNS_SOURCE,
    CollegeSpellBinding,
    college_package,
)

ISSUE: Final = 221
COLLEGE: Final = "enchantment"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE, CAMPAIGNS_SOURCE)
    for key, name, page in (
        ("accuracy", "Accuracy", 480),
        ("deflect", "Deflect", 480),
        ("enchant", "Enchant", 480),
        ("fortify", "Fortify", 480),
        ("power", "Power", 480),
        ("puissance", "Puissance", 481),
        ("staff", "Staff", 481),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
