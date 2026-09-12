"""Independent Jumper, Snatcher, and Warp expectations, Characters B64-99."""

import pytest
from test_statistics import gurps_draft
from trait_support import approved_build, options, trait_compiler

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.character.traits.world_travel import world_travel_traits
from wayfarer.engine.rules.supernatural import inventory
from wayfarer.engine.rules.traits.world_travel import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.traits.world_travel import package as world_travel_package
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits.world_travel import (
    TravelCommand,
    TravelRoute,
    apply_world_travel,
    history,
)
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError

EXPECTED = {"advantage:jumper": 100, "advantage:snatcher": 80, "advantage:warp": 100}


def compiler() -> CharacterCompiler:
    return trait_compiler(
        "world-travel-traits", PROFILE, world_travel_package(), hooks=RUNTIME_HOOKS
    )


def approved(purchase: Purchase) -> tuple[ValidatedBuild, CharacterCompiler]:
    return approved_build(compiler(), purchase)


def test_registry_and_inventory_account_for_all_three_entries() -> None:
    assert {binding.id: binding.point_cost for binding in BINDINGS} == EXPECTED
    rows = {row.id: row for row in inventory().entries if row.id in EXPECTED}
    assert set(rows) == set(EXPECTED)
    assert all(row.blockers == (191,) for row in rows.values())
    assert all(row.evidence == ("tests/test_world_travel_traits.py",) for row in rows.values())


def test_required_variants_and_variable_costs_are_compiled() -> None:
    jumper, engine = approved(
        Purchase(definition_id="advantage:jumper", trait=options(kind="time"))
    )
    assert next(p.cost for p in jumper.purchases if p.definition_id == "advantage:jumper") == 100
    snatcher, _ = approved(Purchase(definition_id="advantage:snatcher", trait=options(weight=40)))
    assert (
        next(p.cost for p in snatcher.purchases if p.definition_id == "advantage:snatcher") == 104
    )
    warp, _ = approved(Purchase(definition_id="advantage:warp", trait=options(reliability=5)))
    assert next(p.cost for p in warp.purchases if p.definition_id == "advantage:warp") == 125
    projected = world_travel_traits(warp, engine.definitions)
    assert projected.warp_reliability() == 5
    for purchase in (
        Purchase(definition_id="advantage:jumper"),
        Purchase(definition_id="advantage:snatcher", trait=options(weight=30)),
        Purchase(definition_id="advantage:warp", trait=options(reliability=11)),
    ):
        assert compiler().compile(gurps_draft(purchase)).build is None


def world() -> World:
    return World(
        entities=(
            Entity("here", EntityKind.LOCATION, "Here"),
            Entity("there", EntityKind.LOCATION, "There"),
            Entity("wrong", EntityKind.LOCATION, "Wrong"),
            Entity("a", EntityKind.ACTOR, "Traveler", "here"),
            Entity("relic", EntityKind.OBJECT, "Relic", "there"),
        )
    )


def resources(*, revision: int = 0, game_time: int = 10) -> ResourceState:
    return ResourceState(
        revision=revision,
        game_time=game_time,
        pools=(Pool(id="fp:a", current=10, maximum=10),),
    )


def command(definition_id: str = "advantage:warp") -> TravelCommand:
    return TravelCommand(
        id="travel",
        actor_id="a",
        expected_revision=0,
        definition_id=definition_id,
        route_id="route",
    )


def route(definition_id: str = "advantage:warp", **changes: object) -> TravelRoute:
    return TravelRoute(
        id="route",
        definition_id=definition_id,
        actor_id="a",
        origin_id="here",
        destination_id="there",
        kind="spatial",
        iq=12,
        roll=11,
        difficulty_modifier=-2,
        preparation_bonus=1,
        fatigue_cost=2,
    ).model_copy(update=changes)


def test_warp_moves_canonical_world_spends_fp_and_replays_after_restart() -> None:
    build, engine = approved(Purchase(definition_id="advantage:warp", trait=options(reliability=2)))
    state, moved, outcome = apply_world_travel(
        resources(),
        world(),
        command(),
        build,
        engine.definitions,
        (route(),),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "arrived" and outcome.effective_target == 13
    assert next(entity for entity in moved.entities if entity.id == "a").location_id == "there"
    assert state.pools[0].current == 8 and history(state)[0].outcome == outcome
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_world_travel(
        restarted,
        moved,
        command(),
        build,
        engine.definitions,
        (route(),),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, moved, outcome)


def test_snatcher_retrieves_only_an_authored_unowned_object_within_weight() -> None:
    build, engine = approved(Purchase(definition_id="advantage:snatcher", trait=options(weight=20)))
    snatch = route(
        definition_id="advantage:snatcher",
        kind="snatch",
        object_id="relic",
        object_weight=20,
        fatigue_cost=0,
    )
    _, moved, outcome = apply_world_travel(
        resources(),
        world(),
        command("advantage:snatcher"),
        build,
        engine.definitions,
        (snatch,),
        authorized_actor_id="a",
        system=True,
    )
    relic = next(entity for entity in moved.entities if entity.id == "relic")
    assert outcome.outcome == "retrieved" and (relic.location_id, relic.owner_id) == ("here", "a")
    with pytest.raises(ValidationError, match="exceeds"):
        apply_world_travel(
            resources(),
            world(),
            command("advantage:snatcher"),
            build,
            engine.definitions,
            (snatch.model_copy(update={"object_weight": 21}),),
            authorized_actor_id="a",
            system=True,
        )


def test_preparation_failure_misjump_authority_and_cas_fail_closed() -> None:
    build, engine = approved(Purchase(definition_id="advantage:warp", trait=options(reliability=0)))
    with pytest.raises(ConflictError, match="preparation"):
        apply_world_travel(
            resources(),
            world(),
            command(),
            build,
            engine.definitions,
            (route(available_at=11),),
            authorized_actor_id="a",
            system=True,
        )
    _, unchanged, failed = apply_world_travel(
        resources(),
        world(),
        command(),
        build,
        engine.definitions,
        (route(roll=16),),
        authorized_actor_id="a",
        system=True,
    )
    assert failed.outcome == "failed" and unchanged == world()
    _, wrong, misjump = apply_world_travel(
        resources(),
        world(),
        command(),
        build,
        engine.definitions,
        (route(roll=18, failure_destination_id="wrong"),),
        authorized_actor_id="a",
        system=True,
    )
    assert misjump.outcome == "misjumped"
    assert next(entity for entity in wrong.entities if entity.id == "a").location_id == "wrong"
    with pytest.raises(ValidationError, match="authority"):
        apply_world_travel(
            resources(),
            world(),
            command(),
            build,
            engine.definitions,
            (route(),),
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError, match="revision"):
        apply_world_travel(
            resources(revision=1),
            world(),
            command(),
            build,
            engine.definitions,
            (route(),),
            authorized_actor_id="a",
            system=True,
        )
