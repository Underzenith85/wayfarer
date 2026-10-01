"""Independent knowledge spell expectations, Characters B249-250."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.knowledge import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_knowledge_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Analyze Magic", 249),
        ("Aura", 249),
        ("Blur", 250),
        ("Continual Light", 249),
        ("Counterspell", 250),
        ("Darkness", 250),
        ("Detect Magic", 249),
        ("Dispel Magic", 250),
        ("Identify Spell", 249),
        ("Light", 249),
        ("Seeker", 249),
        ("Trace", 249),
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
