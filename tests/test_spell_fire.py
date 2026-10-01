"""Independent fire spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.fire import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_fire_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Cold", 247),
        ("Create Fire", 246),
        ("Deflect Energy", 246),
        ("Explosive Fireball", 247),
        ("Extinguish Fire", 247),
        ("Fireball", 247),
        ("Heat", 247),
        ("Ignite Fire", 246),
        ("Resist Cold", 247),
        ("Resist Fire", 247),
        ("Shape Fire", 246),
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
    assert all("tests/test_spell_fire.py" in value.evidence for value in rows.values())
    assert_unsupported_cast(package(), BINDINGS, BINDINGS[0].id)
