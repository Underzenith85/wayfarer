"""Independent enchantment-college expectations, Campaigns B479-482."""

from spell_college_support import assert_unsupported_cast

from wayfarer.engine.rules.magic.enchantment import BINDINGS, package
from wayfarer.engine.rules.supernatural import inventory
from wayfarer.engine.world import Entity, EntityKind, World


def world() -> World:
    return World(
        entities=(
            Entity("forge", EntityKind.LOCATION, "Forge"),
            Entity("mage", EntityKind.ACTOR, "Mage", "forge"),
            Entity("rival", EntityKind.ACTOR, "Rival", "forge"),
        )
    )


def test_exact_enchantment_inventory_and_pages() -> None:
    assert {(value.name, value.page) for value in BINDINGS} == {
        ("Accuracy", 480),
        ("Deflect", 480),
        ("Enchant", 480),
        ("Fortify", 480),
        ("Power", 480),
        ("Puissance", 481),
        ("Staff", 481),
    }
    rows = {
        value.id: value for value in inventory().entries if value.id in {b.id for b in BINDINGS}
    }
    assert rows["spell:enchant"].blockers == (747, 785)
    assert rows["spell:enchant"].evidence == (
        "tests/test_spell_construction.py",
        "tests/test_college_dispatch.py",
    )


def test_enchantment_check_only_attempt_cannot_report_effect_success() -> None:
    assert_unsupported_cast(package(), BINDINGS, "spell:accuracy")
