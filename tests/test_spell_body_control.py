"""Independent body control spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.body_control import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_body_control_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Clumsiness", 244),
        ("Deathtouch", 245),
        ("Hinder", 244),
        ("Itch", 244),
        ("Pain", 244),
        ("Paralyze Limb", 244),
        ("Rooted Feet", 244),
        ("Spasm", 244),
        ("Wither Limb", 244),
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
