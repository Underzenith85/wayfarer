"""Characters third printing B235/B242/B251/B253 lock-spell construction."""

import pytest
from test_spell_construction import compile_spells

from wayfarer.engine.rules.magic.colleges import spell_colleges
from wayfarer.engine.rules.magic.movement import package
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty


@pytest.mark.parametrize(
    "key,magery,prerequisites,expected,page",
    [
        ("apportation", 1, (), 11, "B251"),
        ("lockmaster", 2, ("apportation",), 12, "B251"),
        ("magelock", 1, (), 11, "B253"),
    ],
)
def test_lock_learning_is_hard_with_source_prerequisites(
    key: str, magery: int, prerequisites: tuple[str, ...], expected: int, page: str
) -> None:
    source = package()
    result = compile_spells(
        (source,), tuple(("spell:" + name, 1) for name in (*prerequisites, key)), magery=magery
    )
    assert result.build is not None, result.diagnostics
    assert (
        next(v.value for v in result.build.sheet.values if v.target == "spell:" + key) == expected
    )
    spec = next(d.skill for d in source.definitions if d.id == "spell:" + key)
    assert spec is not None
    assert spec.attribute is ControllingAttribute.IQ
    assert spec.difficulty is Difficulty.HARD
    assert spec.reference == page


@pytest.mark.parametrize("key,magery", [("apportation", 0), ("magelock", 0), ("lockmaster", 1)])
def test_high_points_cannot_replace_required_magery(key: str, magery: int) -> None:
    learned = (("spell:apportation", 1),) if key == "lockmaster" else ()
    result = compile_spells((package(),), learned + (("spell:" + key, 16),), magery=magery)
    assert not result.legal and result.build is None


def test_lockmaster_requires_an_actually_purchased_apportation() -> None:
    result = compile_spells((package(),), (("spell:lockmaster", 16),), magery=3)
    assert not result.legal and result.build is None


def test_magelock_is_protection_warning_despite_legacy_inventory_group() -> None:
    definitions = {d.id: d for d in package().definitions}
    assert spell_colleges(definitions["spell:magelock"]) == {"protection-warning"}
    assert spell_colleges(definitions["spell:lockmaster"]) == {"movement"}
    assert spell_colleges(definitions["spell:apportation"]) == {"movement"}
    assert definitions["spell:haste"].skill is None
