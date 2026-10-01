"""Independent communication empathy spell expectations."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.communication_empathy import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory


def test_exact_communication_empathy_inventory_pages_and_runtime() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Hide Thoughts", 245),
        ("Mind-Reading", 245),
        ("Sense Emotion", 245),
        ("Sense Foes", 245),
        ("Truthsayer", 245),
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
    assert all(
        "tests/test_spell_communication_empathy.py" in value.evidence for value in rows.values()
    )
    assert_unsupported_cast(package(), BINDINGS, BINDINGS[0].id)
