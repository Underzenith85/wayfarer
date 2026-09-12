"""Independent enchantment-college expectations, Campaigns B479-482."""

import pytest
from spell_college_support import approved_spell

from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.spell_enchantment import BINDINGS, package
from wayfarer.rules.supernatural import inventory
from wayfarer.simulation.resources import Item, ResourceState
from wayfarer.simulation.spell_colleges import (
    CollegeSpellCommand,
    apply_college_spell,
    visible_history,
)
from wayfarer.world import Entity, EntityKind, World


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
    assert all(value.blockers == (191,) for value in rows.values())
    assert all(value.evidence == ("tests/test_spell_enchantment.py",) for value in rows.values())


def test_enchantment_learning_authority_privacy_retry_and_restart() -> None:
    build = approved_spell(package(), "spell:accuracy")
    command = CollegeSpellCommand(
        id="enchant",
        actor_id="mage",
        expected_revision=0,
        build_revision=build.revision,
        spell_id="spell:accuracy",
        target_item_id="sword",
    )
    initial = ResourceState(
        items=(Item(id="sword", definition_id="equipment:sword", owner_id="mage"),)
    )
    changed, result = apply_college_spell(
        initial,
        world(),
        build,
        command,
        BINDINGS,
        authorized_actor_id="mage",
        rng=RecordedDice([3, 3, 3]),
    )
    assert result.outcome == "success" and changed.revision == 1
    restarted = ResourceState.model_validate_json(changed.model_dump_json())
    assert apply_college_spell(
        restarted,
        world(),
        build,
        command,
        BINDINGS,
        authorized_actor_id="mage",
        rng=RecordedDice([]),
    ) == (restarted, result)
    assert visible_history(restarted, viewer_actor_id="mage") == (result,)
    assert visible_history(restarted, viewer_actor_id="rival") == ()
    with pytest.raises(AuthorizationError):
        apply_college_spell(
            initial,
            world(),
            build,
            command,
            BINDINGS,
            authorized_actor_id="rival",
            rng=RecordedDice([]),
        )
    with pytest.raises(ConflictError, match="interrupted"):
        apply_college_spell(
            initial,
            world(),
            build,
            command.model_copy(update={"interrupted": True}),
            BINDINGS,
            authorized_actor_id="mage",
            rng=RecordedDice([]),
        )
    with pytest.raises(ValidationError, match="outside"):
        apply_college_spell(
            initial,
            world(),
            build,
            command.model_copy(update={"spell_id": "spell:light"}),
            BINDINGS,
            authorized_actor_id="mage",
            rng=RecordedDice([]),
        )
