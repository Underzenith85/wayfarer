"""Independent earth spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.earth import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_earth_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Create Earth", 246),
        ("Earth to Stone", 245),
        ("Entombment", 246),
        ("Flesh to Stone", 246),
        ("Seek Earth", 245),
        ("Shape Earth", 245),
        ("Stone to Earth", 246),
        ("Stone to Flesh", 246),
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
