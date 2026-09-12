"""Individual combat phases and shared-clock fixtures (Campaigns B362-B366)."""

from pathlib import Path

from test_encounter_context import load, setup

from wayfarer.engine.simulation.campaign.encounter_context import bind_scene
from wayfarer.engine.simulation.campaign.party import Subgroup
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.withdrawal import elapsed_seconds
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService


def test_shared_seconds_settle_by_cycle_and_decisive_partial_cycle() -> None:
    """B362: turns overlap; a completed cycle is one second, not N seconds."""
    from test_combat import combat_engine, resources
    from test_scenes import configured

    engine = combat_engine().combat
    assert engine is not None
    _, world = configured()
    original = engine.start(
        "fight",
        "dock-fight",
        (
            Placement(actor_id="a", position=GridPoint(x=0, y=0)),
            Placement(actor_id="b", position=GridPoint(x=1, y=0)),
        ),
        {"a": 10, "b": 9},
        world,
        resources(),
        frozenset({"a", "b"}),
    )
    after_a = engine._advance(original)
    assert elapsed_seconds(original, after_a) == 0
    after_b = engine._advance(after_a)
    assert elapsed_seconds(after_a, after_b) == 1

    decisive = after_a.model_copy(
        update={"status": "completed", "completion_reason": "incapacitation"}
    )
    assert elapsed_seconds(original, decisive) == 1
    # Ending combat is a lifecycle decision, not a hidden one-second maneuver.
    manually_ended = original.model_copy(
        update={"status": "completed", "completion_reason": "resolved"}
    )
    assert elapsed_seconds(original, manually_ended) == 0


def test_defense_resources_reset_only_at_that_actors_next_turn() -> None:
    """B362-B363/B374: actor-relative defenses do not reset with another actor."""
    from test_combat import combat_engine, resources
    from test_scenes import configured

    engine = combat_engine().combat
    assert engine is not None
    _, world = configured()
    encounter = engine.start(
        "fight",
        "dock-fight",
        (
            Placement(actor_id="a", position=GridPoint(x=0, y=0)),
            Placement(actor_id="b", position=GridPoint(x=1, y=0)),
        ),
        {"a": 12, "b": 11},
        world,
        resources(),
        frozenset({"a", "b"}),
    )
    marked = tuple(
        participant.model_copy(
            update={
                "parries": ("weapon",),
                "block_used": True,
                "retreat_used": True,
                "reaction_available": False,
            }
        )
        for participant in encounter.participants
    )
    encounter = encounter.model_copy(update={"participants": marked})
    after_a = engine._advance(encounter)
    actors = {p.actor_id: p for p in after_a.participants}
    assert actors["a"].parries == ("weapon",) and actors["a"].retreat_used
    assert actors["b"].parries == () and not actors["b"].retreat_used


async def test_two_fights_overlap_at_the_minimum_subgroup_frontier(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    state = await load(play, cid)
    engine = play.engine.combat
    assert engine is not None and play.engine.rules.scenes is not None

    def fight(encounter_id: str, left: str, right: str) -> Encounter:
        encounter = engine.start(
            encounter_id,
            "dock-fight",
            (
                Placement(actor_id=left, position=GridPoint(x=0, y=0)),
                Placement(actor_id=right, position=GridPoint(x=1, y=0)),
            ),
            {left: 10, right: 10},
            state.world,
            state.resources,
            frozenset({"a", "b", "c", "d"}),
        )
        return bind_scene(encounter, play.engine.rules.scenes, engine.rules, "dock-scene")

    seeded = state.model_copy(
        update={
            "encounters": (fight("one", "a", "b"), fight("two", "c", "d")),
            "party": state.party.model_copy(
                update={
                    "groups": (
                        Subgroup(
                            id="one",
                            actor_ids=("a", "b"),
                            scene_id="dock-scene",
                            ready_through=0,
                        ),
                        Subgroup(
                            id="two",
                            actor_ids=("c", "d"),
                            scene_id="dock-scene",
                            ready_through=0,
                        ),
                    )
                }
            ),
        }
    )
    play.engine.validate(seeded)
    campaign = await play.store.read(cid)
    campaign["play_json"] = seeded.model_dump_json()
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    store = AsyncSQLiteStore(tmp_path / "parallel-fights.sqlite", 10)
    await store.insert(campaign)
    play = PlayService(store, play.engine, rng=play.rng)
    combat = CombatService(play)

    revision = 0
    for actor, encounter_id in (("a", "one"), ("b", "one"), ("c", "two")):
        await combat.execute(
            cid,
            TakeCombatTurn(
                id=f"turn-{actor}",
                actor_id=actor,
                expected_revision=revision,
                encounter_id=encounter_id,
                maneuver="do_nothing",
            ),
            authenticated_actor_id=actor,
        )
        revision += 1
    held = await load(play, cid)
    assert [g.ready_through for g in held.party.groups] == [1, 0]
    assert held.resources.game_time == 0

    last = TakeCombatTurn(
        id="turn-d",
        actor_id="d",
        expected_revision=revision,
        encounter_id="two",
        maneuver="do_nothing",
    )
    result = await combat.execute(cid, last, authenticated_actor_id="d")
    after = await load(play, cid)
    assert [g.ready_through for g in after.party.groups] == [1, 1]
    assert after.resources.game_time == 1
    # A retry and a restarted service return the receipt without consuming a second.
    assert await combat.execute(cid, last, authenticated_actor_id="d") == result
    restarted = CombatService(PlayService(store, play.engine))
    assert await restarted.execute(cid, last, authenticated_actor_id="d") == result
    assert (await load(play, cid)).resources.game_time == 1
    assert await store.replay(cid) == await store.read(cid)
