"""Independent water spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.water import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_water_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Create Water", 253),
        ("Destroy Water", 253),
        ("Fog", 253),
        ("Icy Weapon", 253),
        ("Purify Water", 253),
        ("Seek Water", 253),
        ("Shape Water", 253),
    }
    rows = {
        value.id: value
        for value in inventory().entries
        if value.id in {binding.id for binding in BINDINGS}
    }
    assert all(
        value.blockers
        or value.id in {"spell:light", "spell:daze", "spell:create-fire", "spell:fireball"}
        for value in rows.values()
    )
    assert all(value.evidence for value in rows.values())
    assert_unsupported_cast(package(), BINDINGS, BINDINGS[0].id)
