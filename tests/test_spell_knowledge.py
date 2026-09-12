"""Independent knowledge spell expectations, Characters B249-250."""

from spell_college_support import approved_spell

from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.spell_knowledge import BINDINGS, package
from wayfarer.rules.supernatural import inventory
from wayfarer.simulation.resources import ResourceState
from wayfarer.simulation.spell_colleges import (
    CollegeSpellCommand,
    apply_college_spell,
    visible_history,
)
from wayfarer.world import Entity, EntityKind, World


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
    assert all(value.blockers == (191,) for value in rows.values())
    assert all(value.evidence == ("tests/test_spell_knowledge.py",) for value in rows.values())
    build = approved_spell(package(), BINDINGS[0].id)
    world = World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("mage", EntityKind.ACTOR, "Mage", "room"),
            Entity("rival", EntityKind.ACTOR, "Rival", "room"),
        )
    )
    command = CollegeSpellCommand(
        id="knowledge",
        actor_id="mage",
        expected_revision=0,
        build_revision=build.revision,
        spell_id=BINDINGS[0].id,
    )
    changed, outcome = apply_college_spell(
        ResourceState(),
        world,
        build,
        command,
        BINDINGS,
        authorized_actor_id="mage",
        rng=RecordedDice([3, 3, 3]),
    )
    assert changed.revision == 1 and outcome.outcome == "success"
    restarted = ResourceState.model_validate_json(changed.model_dump_json())
    assert apply_college_spell(
        restarted, world, build, command, BINDINGS, authorized_actor_id="mage", rng=RecordedDice([])
    ) == (restarted, outcome)
    assert visible_history(restarted, viewer_actor_id="mage") == (outcome,)
    assert visible_history(restarted, viewer_actor_id="rival") == ()
