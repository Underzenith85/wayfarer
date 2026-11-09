"""B9/B15/B16 prices stay exact before their one final upward rounding.

The seven source prices are the already reviewed, independently entered cases in
size_modifier_costs.json. Decimal-context and large-integer checks exercise the
same arithmetic contract; they do not claim extra legal character-score ranges.
"""

import json
from decimal import Inexact, Rounded, localcontext
from pathlib import Path

import pytest
from test_size_modifier_costs import engine, size
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import Purchase, pool_limits
from wayfarer.engine.character.size_modifier import SizeCostTarget, cost
from wayfarer.engine.character.statistics import Secondary

PROFILE = "gurps-basic-set-4e-2004"
SOURCE_PRICES = (
    ("st-sm-1", "attribute:st", 10, 1, 9, 10),
    ("st-sm-3", "attribute:st", 20, 3, 14, 30),
    ("hp-round-up", "secondary:hp", 2, 3, 2, 30),
    ("cap-at-sm-8", "attribute:st", 10, 8, 2, 80),
    ("cap-above-sm-8", "attribute:st", 10, 12, 2, 80),
    ("reduced-st-no-discount", "attribute:st", -10, 4, -10, 0),
    ("reduced-hp-no-discount", "secondary:hp", -2, 4, -2, 0),
)


@pytest.mark.parametrize(
    ("case_id", "target", "base_cost", "size_modifier", "expected_cost", "expected_discount"),
    SOURCE_PRICES,
    ids=[row[0] for row in SOURCE_PRICES],
)
def test_reviewed_size_prices_are_independent_of_decimal_context(
    case_id: str,
    target: SizeCostTarget,
    base_cost: int,
    size_modifier: int,
    expected_cost: int,
    expected_discount: int,
) -> None:
    source = json.loads(Path("tests/fixtures/gurps/size_modifier_costs.json").read_text())
    row = next(row for row in source["cases"] if row["id"] == case_id)
    assert row["input"] == {
        "target": target,
        "base_cost": base_cost,
        "size_modifier": size_modifier,
    }
    assert row["expected"] == {
        "adjusted_cost": expected_cost,
        "discount_percent": expected_discount,
    }
    for precision in (2, 3, 28):
        with localcontext() as context:
            context.prec = precision
            context.traps[Inexact] = context.traps[Rounded] = True
            priced = cost(PROFILE, target, base_cost, size_modifier)
        assert (priced.adjusted_cost, priced.discount_percent) == (
            expected_cost,
            expected_discount,
        )
        assert priced.source_id == source["source_id"]
        assert priced.capability_id == source["capability_id"]


def test_hp_discount_rounds_once_after_exact_fraction_even_with_low_precision() -> None:
    # Eleven additional HP cost22 before the30% SM3 discount.15.4 rounds up16.
    with localcontext() as context:
        context.prec = 2
        assert cost(PROFILE, "secondary:hp", 22, 3).adjusted_cost == 16


def test_size_price_does_not_lose_integer_digits_before_rounding() -> None:
    # Arithmetic-service input, not a declaration that this is a legal build.
    price = cost(PROFILE, "secondary:hp", 10**30 + 2, 3)
    assert price.adjusted_cost == 7 * 10**29 + 2


@pytest.mark.parametrize("precision", [2, 28])
def test_real_compiler_keeps_the_canonical_price_and_normal_historic_revision(
    precision: int,
) -> None:
    compiler = engine()
    with localcontext() as context:
        context.prec = precision
        result = compiler.compile(
            gurps_draft(size(3), Purchase(definition_id="secondary:hp", amount=21))
        )
    assert result.build is not None, result.diagnostics
    build = result.build
    hp = next(p for p in build.purchases if p.definition_id == "secondary:hp")
    assert result.spent == hp.cost == 16
    assert build.statistics is not None
    assert build.statistics.costs.secondaries[Secondary.HP] == 16
    assert pool_limits(build) == {"hp": 21, "fp": 10}
    # Actual unchanged b4541f8a compiler output at its ordinary precision28.
    assert build.revision == "bc08445fab527121bf56ccdc40446c2db89ef91f5419bf959d5dcbde35439e05"


@pytest.mark.parametrize("base_cost", [0, -2, -(10**30 + 2)])
def test_nonpositive_size_prices_are_never_discounted(base_cost: int) -> None:
    priced = cost(PROFILE, "secondary:hp", base_cost, 10000)
    assert priced.adjusted_cost == base_cost and priced.discount_percent == 0
