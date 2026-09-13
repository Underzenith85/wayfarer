"""Independent Campaigns B396-B398 mounted and personal-flight fixtures."""

import pytest
from test_creatures import catalog
from test_transport import fixture as transport_fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.special_combat import FlightStep
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.spatial import HexActorPlacement, HexSpatialContext
from wayfarer.engine.simulation.combat.special_movement import (
    begin_personal_flight,
    bind_mount,
    flight_attack_defense,
    fly,
    mounted_action_context,
    separate_mount,
)
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ValidationError


def combatant(actor_id: str, q: int, *, initiative: int) -> Combatant:
    return Combatant(
        actor_id=actor_id,
        initiative=initiative,
        position=Hex(q=q, r=0),
        hex_facing=0,
        reach=1,
        movement_allowance=5,
    )


def encounter() -> Encounter:
    actors = (
        combatant("a", -1, initiative=12),
        combatant("b", 0, initiative=11),
        combatant("c", 2, initiative=10),
    )
    placements = []
    for actor in actors:
        assert isinstance(actor.position, Hex) and actor.hex_facing is not None
        placements.append(
            HexActorPlacement(
                actor_id=actor.actor_id,
                position=actor.position,
                facing=actor.hex_facing,
            )
        )
    return Encounter(
        id="fight",
        spatial_context=HexSpatialContext(
            battlefield_id="map",
            placements=tuple(placements),
        ),
        participants=actors,
        turn_order=("a", "b", "c"),
    )


def board() -> HexBattlefield:
    return HexBattlefield(
        id="map",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id=BASELINE_ID,
        cells=tuple(Cell(position=Hex(q=q, r=0)) for q in range(-2, 8)),
    )


def mounted_fixture(*, war_trained: bool) -> tuple[ResourceEngine, ResourceState]:
    engine, resources = transport_fixture(mount=True)
    horse = catalog().compile("b", "Horse", "creature:cavalry-horse").creature
    assert horse.mount is not None
    horse = horse.model_copy(
        update={
            "mount": horse.mount.model_copy(
                update={
                    "war_trained": war_trained,
                    "combat_training_years": 1 if war_trained else 0,
                }
            )
        }
    )
    resources = resources.model_copy(
        update={
            "creatures": (horse,),
            "transports": (
                resources.transports[0].model_copy(
                    update={
                        "mechanics_version": 2,
                        "acceleration": horse.statistics.move("ground").ordinary_move,
                        "top_speed": horse.statistics.move("ground").enhanced_move
                        or horse.statistics.move("ground").ordinary_move,
                    }
                ),
            ),
            "pools": tuple(
                Pool(
                    id="hp:" + actor,
                    current=10,
                    maximum=10,
                    injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
                )
                for actor in ("a", "b", "c")
            ),
        }
    )
    return engine, resources


@pytest.mark.parametrize("war_trained", [False, True])
def test_mount_and_rider_remain_separate_and_training_controls_mount_actions(
    war_trained: bool,
) -> None:
    _, resources = mounted_fixture(war_trained=war_trained)
    bound = bind_mount(encounter(), resources, transport_id="ride", riding_skill=9)
    relationship = bound.mounted_combat[0]
    assert (relationship.rider_id, relationship.mount_id) == ("a", "b")
    assert next(p.position for p in bound.participants if p.actor_id == "a") == Hex(q=0, r=0)
    rider_moves, rider_cap, defense = mounted_action_context(bound, resources, "a")
    mount_moves, _, _ = mounted_action_context(bound, resources, "b")
    assert ("attack" in rider_moves, rider_cap, defense) == (True, 9, -3)
    assert ("attack" in mount_moves) is war_trained


def test_mounted_collision_separates_and_injures_each_identity_atomically() -> None:
    engine, resources = mounted_fixture(war_trained=True)
    bound = bind_mount(encounter(), resources, transport_id="ride", riding_skill=12)
    separated, injured = separate_mount(
        engine,
        bound,
        resources,
        command_id="impact",
        expected_revision=0,
        transport_id="ride",
        health={"a": 12, "b": 12},
        collision_speed=5,
        rng=RecordedDice([1, 1]),
    )
    assert separated.mounted_combat[0].control == "separated"
    assert next(p.posture for p in separated.participants if p.actor_id == "a") == "prone"
    assert [
        next(p.current for p in injured.pools if p.id == "hp:" + actor) for actor in ("a", "b")
    ] == [9, 9]
    replayed_encounter, replayed_resources = separate_mount(
        engine,
        bound,
        injured,
        command_id="impact",
        expected_revision=0,
        transport_id="ride",
        health={"a": 12, "b": 12},
        collision_speed=5,
        rng=RecordedDice([]),
    )
    assert (replayed_encounter, replayed_resources) == (separated, injured)


def test_personal_flight_uses_vertical_cost_and_not_vehicle_state() -> None:
    flying = begin_personal_flight(encounter(), "a", altitude=2, basic_air_move=3, top_air_speed=12)
    moved = fly(
        flying,
        board(),
        "a",
        "move",
        (FlightStep(q=0, r=0, altitude=3), FlightStep(q=1, r=0, altitude=4)),
    )
    actor = next(p for p in moved.participants if p.actor_id == "a")
    assert actor.position == Hex(q=1, r=0)
    assert actor.personal_flight is not None
    assert (actor.personal_flight.altitude, actor.personal_flight.source) == (4, "personal-flight")
    assert moved.mounted_combat == ()

    with pytest.raises(ValidationError, match="exceeds basic air Move"):
        fly(
            flying,
            board(),
            "a",
            "move",
            (
                FlightStep(q=0, r=0, altitude=3),
                FlightStep(q=1, r=0, altitude=4),
                FlightStep(q=2, r=0, altitude=5),
            ),
        )


def test_winged_stall_and_hovering_vertical_retreat_are_explicit() -> None:
    winged = begin_personal_flight(
        encounter(),
        "a",
        altitude=4,
        basic_air_move=5,
        top_air_speed=12,
        winged=True,
        cannot_hover=True,
    )
    stalled = fly(
        winged,
        board(),
        "a",
        "move",
        (FlightStep(q=0, r=0, altitude=5),),
    )
    attacker = next(p for p in stalled.participants if p.actor_id == "a")
    with pytest.raises(ValidationError, match="stalled"):
        flight_attack_defense(attacker, stalled.participants[2])

    hovering = begin_personal_flight(
        encounter(), "a", altitude=4, basic_air_move=5, top_air_speed=12
    )
    flyer = next(p for p in hovering.participants if p.actor_id == "a")
    assert flight_attack_defense(flyer, flyer, retreat_vertical=True) == (0, 1)
