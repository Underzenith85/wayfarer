"""Printed B235/B253 learning: four water spells share IQ/Hard, named chains."""

import pytest
from test_spell_construction import compile_spells

from wayfarer.engine.rules.magic.water import package
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty

CHAIN = ("seek-water", "purify-water", "create-water", "destroy-water")


@pytest.mark.parametrize("index", range(4))
def test_water_valid_learning_with_source_chain(index: int) -> None:
    source = package()
    result = compile_spells(
        (source,), tuple(("spell:" + key, 1) for key in CHAIN[: index + 1]), magery=1
    )
    assert result.build is not None, result.diagnostics
    assert (
        next(v.value for v in result.build.sheet.values if v.target == "spell:" + CHAIN[index])
        == 11
    )
    spec = next(d.skill for d in source.definitions if d.id == "spell:" + CHAIN[index])
    assert spec is not None
    assert spec.attribute is ControllingAttribute.IQ
    assert spec.difficulty is Difficulty.HARD
    assert spec.reference == "B253"


@pytest.mark.parametrize("key", CHAIN[1:])
def test_water_prerequisite_cannot_be_replaced_by_points(key: str) -> None:
    result = compile_spells((package(),), (("spell:" + key, 16),), magery=3)
    assert not result.legal and result.build is None


def test_seek_water_purchase_must_fit_approved_budget() -> None:
    result = compile_spells((package(),), (("spell:seek-water", 2000),), magery=1)
    assert not result.legal and result.build is None
