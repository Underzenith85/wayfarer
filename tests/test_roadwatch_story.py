"""The installed raw Roadside Watch graph keeps its actual canonical setup facts."""

from pathlib import Path

from wayfarer.adventures.roadwatch import adventure, build_adventure, engine
from wayfarer.adventures.roadwatch_rules import EQUIPMENT_SPECS, PC_ACTOR_IDS
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def test_installed_graph_retains_explicit_anatomy_grips_and_pristine_items() -> None:
    graph = adventure()
    assert graph == build_adventure()
    assert {actor.actor_id for actor in graph.actors} == {"a", "b", "brigand"}
    for actor in graph.actors:
        assert actor.body is not None and actor.body.anatomy == "human"
        assert actor.held_item_hands == ((f"sword-{actor.actor_id}", "right-hand"),)
        assert actor.conditions == ()
        assert {"a", "b", "brigand"} <= set(actor.aware_of)
        # ActorSetup awareness alone does not establish World knowledge. These
        # public identities make the announced confrontation participants known
        # without moving them together or bypassing native visibility checks.
        assert {"a", "b", "brigand"} <= {
            entity.id for entity in graph.world.perspective(actor.actor_id).entities
        }
    assert {
        entity.id: entity.location_id
        for entity in graph.world.entities
        if entity.id in {"a", "b", "brigand"}
    } == {"a": "post", "b": "post", "brigand": "road"}
    # HP/FP, injury receipts, treatment and elapsed time must come from the real
    # activation/play pipeline, not a convenient fabricated wound or fatigue.
    assert graph.resources.pools == ()
    assert graph.resources.events == ()
    assert graph.resources.receipts == ()
    assert graph.resources.recovery_tasks == () and graph.resources.game_time == 0
    specs = {spec.definition_id: spec for spec in EQUIPMENT_SPECS}
    for item in graph.resources.items:
        durability = specs[item.definition_id].durability
        if durability is not None:
            assert item.condition is not None
            assert item.condition.hp == durability.hp
            assert not item.condition.disabled and not item.condition.destroyed


def test_canonical_starting_load_is_below_each_actual_basic_lift() -> None:
    graph = adventure()
    specs = {spec.definition_id: spec for spec in EQUIPMENT_SPECS}
    loads = {
        actor: sum(
            specs[item.definition_id].unit_weight * item.quantity
            for item in graph.resources.items
            if item.owner_id == actor
        )
        for actor in PC_ACTOR_IDS
    }
    # Catalog units are 1,000 per pound: sword3 + leather10, plus Iven's2lb
    # sterile bandages. B17's rounded BL values are24lb and20lb respectively.
    assert loads == {"a": 13_000, "b": 15_000}
    assert loads["a"] <= 24_000 and loads["b"] <= 20_000


def test_production_studio_accepts_every_scene_and_a_truthful_failure_path(tmp_path: Path) -> None:
    play = PlayService(AsyncSQLiteStore(tmp_path / "unopened.sqlite3"), engine())
    graph = adventure()
    report = ScenarioStudio(play, npc_reviewer=play.engine.reviewer).validate(graph)
    assert report.valid, report.findings
    assert set(report.reachable_scene_ids) == {"post-scene", "road-scene", "shelter-scene"}
    assert graph.objectives.deadline == 86400
    assert graph.scenes.version == 2 and graph.scenes.interrupt_recovery_on_travel
    required = {objective.id for objective in graph.objectives.objectives if objective.required}
    assert required == {"return-a", "return-b"}
    assert graph.objectives.rewards == ()
