"""Independent movement spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.movement import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_movement_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Apportation", 251),
        ("Armor", 253),
        ("Deflect Missile", 251),
        ("Great Haste", 251),
        ("Haste", 251),
        ("Lockmaster", 251),
        ("Magelock", 253),
        ("Shield", 252),
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
