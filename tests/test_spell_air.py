"""Independent air spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.air import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_air_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Breathe Water", 243),
        ("Create Air", 243),
        ("Earth to Air", 243),
        ("Lightning", 244),
        ("No-Smell", 243),
        ("Predict Weather", 243),
        ("Purify Air", 243),
        ("Shape Air", 243),
        ("Stench", 244),
        ("Walk on Air", 243),
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
