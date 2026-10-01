"""Independent necromantic spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.necromantic import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_necromantic_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Banish", 252),
        ("Death Vision", 251),
        ("Planar Summons", 247),
        ("Plane Shift", 248),
        ("Sense Spirit", 252),
        ("Summon Demon", 252),
        ("Summon Spirit", 252),
        ("Turn Zombie", 252),
        ("Zombie", 252),
    }
    rows = {
        value.id: value
        for value in inventory().entries
        if value.id in {binding.id for binding in BINDINGS}
    }
    assert rows["spell:plane-shift"].blockers == (802,)
    assert rows["spell:plane-shift"].evidence == (
        "tests/test_spell_construction.py",
        "tests/test_college_dispatch.py",
    )
    assert_unsupported_cast(package(), BINDINGS, BINDINGS[0].id)
