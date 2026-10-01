"""Basic Set enchantment spell inventory (Campaigns B480-481)."""

from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.magic.colleges import (
    CAMPAIGNS_SOURCE,
    CollegeSpellBinding,
    college_package,
    learning_spec,
)
from wayfarer.engine.rules.types.skill import Difficulty

ISSUE: Final = 221
COLLEGE: Final = "enchantment"
LEARNING: Final = {
    "enchant": learning_spec(
        480,
        difficulty=Difficulty.VERY_HARD,
        magery=2,
        colleges=10,
        excluded_colleges=(COLLEGE,),
    ),
    "staff": learning_spec(481, spells=("enchant",)),
    "power": learning_spec(480, spells=("enchant", "recover-energy")),
}
BINDINGS: Final = tuple(
    CollegeSpellBinding(
        key,
        name,
        page,
        COLLEGE,
        CAMPAIGNS_SOURCE,
        LEARNING.get(key),
    )
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
