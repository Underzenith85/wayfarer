"""Basic Set communication empathy spell inventory."""

from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.spell_colleges import CollegeSpellBinding, college_package

ISSUE: Final = 232
COLLEGE: Final = "communication-empathy"
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE)
    for key, name, page in (
        ("hide-thoughts", "Hide Thoughts", 245),
        ("mind-reading", "Mind-Reading", 245),
        ("sense-emotion", "Sense Emotion", 245),
        ("sense-foes", "Sense Foes", 245),
        ("truthsayer", "Truthsayer", 245),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)
