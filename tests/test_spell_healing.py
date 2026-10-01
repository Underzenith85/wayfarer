"""Independent healing spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.healing import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_healing_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Awaken", 248),
        ("Great Healing", 248),
        ("Lend Energy", 248),
        ("Lend Vitality", 248),
        ("Major Healing", 248),
        ("Minor Healing", 248),
        ("Recover Energy", 248),
    }
    rows = {
        value.id: value
        for value in inventory().entries
        if value.id in {binding.id for binding in BINDINGS}
    }
    assert rows["spell:major-healing"].blockers == ()
    assert rows["spell:great-healing"].blockers == ()
    assert rows["spell:major-healing"].evidence == (
        "tests/test_spell_construction.py",
        "tests/test_college_dispatch.py",
        "tests/test_healing_spell_effects.py",
    )
    assert_unsupported_cast(package(), BINDINGS, BINDINGS[0].id)
