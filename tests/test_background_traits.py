"""Independent expected results: Characters 4e third printing B23-30."""

from fractions import Fraction

import pytest
from test_mundane_traits import runtime_compiler
from test_statistics import gurps_draft

from wayfarer.character.background_traits import BackgroundContext, background_traits
from wayfarer.character.compiler import Purchase
from wayfarer.errors import ValidationError
from wayfarer.rules.background_traits import BackgroundTraits, LanguageAbility, comprehension
from wayfarer.rules.mundane_traits import Vocabulary, inventory

DEFAULT_CONTEXT = BackgroundContext()


def projected(
    *purchases: Purchase, context: BackgroundContext = DEFAULT_CONTEXT
) -> BackgroundTraits:
    engine = runtime_compiler()
    build = engine.compile(gurps_draft(*purchases)).build
    assert build is not None
    return background_traits(build, engine.definitions, context)


@pytest.mark.parametrize(
    ("identifier", "average", "expected"),
    [
        ("poor", 1000, Fraction(200)),
        ("struggling", 1000, Fraction(500)),
        ("comfortable", 1000, Fraction(2000)),
        ("wealthy", 1000, Fraction(5000)),
        ("very-wealthy", 1000, Fraction(20000)),
        ("filthy-rich", 1000, Fraction(100000)),
        ("multimillionaire-1", 1000, Fraction(1000000)),
        ("multimillionaire-2", 1000, Fraction(10000000)),
    ],
)
def test_each_wealth_tier_derives_exact_starting_assets(
    identifier: str, average: int, expected: Fraction
) -> None:
    value = projected(Purchase(definition_id="trait:wealth-" + identifier))
    assert value.starting_assets(average) == expected


def test_multimillionaire_constructions_have_independent_prices() -> None:
    values = {entry.id: entry.points for entry in inventory()}
    assert values["trait:wealth-multimillionaire-1"] == 75
    assert values["trait:wealth-multimillionaire-2"] == 100
    assert values["trait:wealth-multimillionaire-3"] == 125


def test_wealth_and_each_ordinary_rank_grant_free_status() -> None:
    value = projected(
        Purchase(definition_id="trait:wealth-multimillionaire-1"),
        Purchase(definition_id="trait:rank-watch", amount=5),
    )
    assert value.free_status == 4 and value.status == 4
    assert value.status_reaction(observer_status=1, disposition="neutral") == 3
    assert value.status_reaction(observer_status=6, disposition="friendly") == 0
    assert value.status_reaction(observer_status=6, disposition="angry") == -2


def test_negative_status_penalty_is_capped_and_resentment_suppresses_deference() -> None:
    assert BackgroundTraits(purchased_status=-2).status_reaction(3, "neutral") == -4
    assert BackgroundTraits(purchased_status=4).status_reaction(0, "resentful") == 0


def test_replacement_and_courtesy_rank_have_distinct_semantics() -> None:
    replacement = projected(Purchase(definition_id="trait:rank-replaces-status-watch", amount=3))
    assert replacement.status == 3 and replacement.free_status == 0
    courtesy = projected(Purchase(definition_id="trait:courtesy-rank-watch", amount=3))
    assert courtesy.status == 0
    with pytest.raises(ValidationError, match="membership"):
        projected(
            Purchase(definition_id="trait:rank-watch"),
            context=BackgroundContext(organization_memberships=()),
        )


def test_language_talent_native_language_and_form_levels_are_derived() -> None:
    value = projected(
        Purchase(definition_id="trait:language-talent"),
        Purchase(definition_id="trait:language-trade-spoken", amount=1),
        context=BackgroundContext(native_language="home"),
    )
    assert value.languages == (
        LanguageAbility(language_id="home", spoken="native", written="native"),
        LanguageAbility(language_id="trade", spoken="accented", written="none"),
    )
    assert value.languages[1].skill_penalty("spoken") == -1
    assert comprehension(2, talent=True) == "native"


def test_alien_culture_has_its_separate_two_point_construction() -> None:
    values = inventory(Vocabulary(cultures=(), alien_cultures=("martian",)))
    culture = next(value for value in values if value.id == "trait:culture-martian")
    assert culture.points == 2 and culture.identity == "martian"
