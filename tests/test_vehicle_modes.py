"""Independent Campaigns 4e fourth-printing examples, B394-395, B430-432, B468-469."""

import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_resources import campaign
from test_transport import fixture as legacy_fixture

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.resources import ResourceService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.conformance import BASELINE_ID
from wayfarer.rules.transport_types import Transport
from wayfarer.rules.vehicle_types import PassengerEjection, PassengerProtection
from wayfarer.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.simulation.resources import ResourceEngine, ResourceState
from wayfarer.simulation.transport import apply_transport
from wayfarer.simulation.vehicle_collisions import collision_exchange, passenger_injury
from wayfarer.simulation.vehicle_commands import (
    ResolveAirAftermath,
    ResolveVehicleEjection,
    VehicleControl,
    VehicleImpact,
    VehicleManeuver,
    VehicleRollover,
    VehicleSkid,
)
from wayfarer.simulation.vehicle_motion import ground_cruising_speed, safe_deceleration


def fixture(**changes: object) -> tuple[ResourceEngine, ResourceState]:
    engine, state = legacy_fixture()
    t = Transport.model_validate(
        {**state.transports[0].model_dump(), "mechanics_version": 2, "occupants": ("a",), **changes}
    )
    return engine, state.model_copy(update={"transports": (t,)})


def map_fixture(
    *, mud: bool = False, blocked: int | None = None, elevation: int = 0
) -> HexBattlefield:
    return HexBattlefield(
        id="map",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id=BASELINE_ID,
        cells=tuple(
            Cell(
                position=Hex(q=q, r=r),
                elevation=elevation,
                blocked=q == blocked,
                extra_cost=1 if mud and q == 1 else 0,
            )
            for q in range(-10, 40)
            for r in range(-10, 40)
        ),
    )


def maneuver(speed: int, course: tuple[int, ...], **changes: object) -> VehicleManeuver:
    return VehicleManeuver.model_validate(
        {
            "id": "move",
            "actor_id": "a",
            "expected_revision": 0,
            "transport_id": "ride",
            "end_speed": speed,
            "course": course,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "mode,handling,expected",
    [
        ("ground-wheeled", 0, 5),
        ("ground-tracked", 0, 10),
        ("ground-drawn", 0, 10),
        ("ground-walking", 0, 10),
        ("ground-slithering", 0, 10),
        ("air", 2, 7),
        ("water", -5, 1),
        ("underwater", -2, 3),
    ],
)
def test_b468_safe_deceleration(mode: str, handling: int, expected: int) -> None:
    _, state = fixture(locomotion=mode, handling=handling)
    assert safe_deceleration(state.transports[0]) == expected


@pytest.mark.parametrize(
    "mode", ["ground-tracked", "ground-drawn", "ground-walking", "ground-slithering"]
)
def test_new_ground_modes_move_and_stop(mode: str) -> None:
    engine, state = fixture(locomotion=mode, speed=10)
    updated = apply_transport(
        engine, state, maneuver(0, (0,) * 10), system=True, board=map_fixture()
    )
    assert (updated.transports[0].q, updated.transports[0].speed) == (10, 0)
    assert updated.revision == 1


def test_b394_turn_radius_and_b395_terrain_costs() -> None:
    engine, state = fixture(speed=6)
    command = maneuver(6, (0, 0, 1, 1, 2, 2))
    updated = apply_transport(engine, state, command, system=True, board=map_fixture())
    assert (updated.transports[0].q, updated.transports[0].r, updated.transports[0].facing) == (
        0,
        4,
        2,
    )
    with pytest.raises(ValidationError, match="turn"):
        apply_transport(engine, state, maneuver(6, (1,) * 6), system=True, board=map_fixture())
    # Six MP: five yards plus one mud surcharge; velocity falls from six to five.
    updated = apply_transport(
        engine, state, maneuver(6, (0,) * 5), system=True, board=map_fixture(mud=True)
    )
    assert (updated.transports[0].q, updated.transports[0].speed) == (5, 5)


def test_b466_ground_cruising_speed_tables_and_road_bound_cap() -> None:
    assert ground_cruising_speed(60, 3, "ground-wheeled", "very-bad") == 6
    assert ground_cruising_speed(60, 3, "ground-tracked", "very-bad") == 9
    assert ground_cruising_speed(60, 3, "ground-walking", "very-bad") == 12
    assert ground_cruising_speed(60, 3, "ground-wheeled", "good") == 75


def test_authored_vertical_flight_uses_the_three_dimensional_terrain_clearance() -> None:
    engine, state = fixture(locomotion="air", speed=3, altitude=10)
    climbed = apply_transport(
        engine,
        state,
        maneuver(3, (0, 0, 0), end_altitude=13),
        system=True,
        board=map_fixture(),
    )
    assert (climbed.transports[0].q, climbed.transports[0].altitude) == (3, 13)
    ridge = map_fixture().model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"elevation": 9}) if c.position == Hex(q=1, r=0) else c
                for c in map_fixture().cells
            )
        }
    )
    with pytest.raises(ValidationError, match="intersects terrain"):
        apply_transport(
            engine,
            state,
            maneuver(3, (0, 0, 0), end_altitude=7),
            system=True,
            board=ridge,
        )


def test_air_drift_and_continuing_dive_are_persisted_turn_aftermath() -> None:
    engine, drifting = fixture(
        locomotion="air", speed=3, altitude=20, status="drifting", remaining_points=3
    )
    drift = ResolveAirAftermath(id="drift", actor_id="a", expected_revision=0, transport_id="ride")
    displaced = apply_transport(
        engine, drifting, drift, system=True, board=map_fixture(), health={"a": 12}
    )
    assert (displaced.transports[0].q, displaced.transports[0].status) == (3, "controlled")
    assert (
        apply_transport(
            engine, displaced, drift, system=True, board=map_fixture(), health={"a": 12}
        )
        == displaced
    )

    engine, diving = fixture(locomotion="air", speed=3, top_speed=10, altitude=25, status="diving")
    descended = apply_transport(
        engine,
        diving,
        drift.model_copy(update={"id": "fall"}),
        system=True,
        board=map_fixture(),
        health={"a": 12},
    )
    assert (
        descended.transports[0].altitude,
        descended.transports[0].vertical_speed,
        descended.transports[0].status,
    ) == (15, 10, "diving")
    recovered = apply_transport(
        engine,
        descended,
        VehicleControl(
            id="recover", actor_id="a", expected_revision=1, transport_id="ride", skill=12
        ),
        system=True,
        rng=RecordedDice([2, 2, 2]),
    )
    assert (recovered.transports[0].status, recovered.transports[0].vertical_speed) == (
        "controlled",
        0,
    )


def test_stall_fall_reaches_terrain_and_uses_existing_collision_reducers() -> None:
    engine, state = fixture(locomotion="air", speed=3, top_speed=10, altitude=4, status="stalled")
    crashed = apply_transport(
        engine,
        state,
        ResolveAirAftermath(id="air-crash", actor_id="a", expected_revision=0, transport_id="ride"),
        system=True,
        board=map_fixture(),
        health={"a": 12},
        rng=RecordedDice([1] * 12),
    )
    assert crashed.transports[0].altitude == 0
    assert crashed.transports[0].status in ("crashed", "ejection-pending")
    body = next(i for i in crashed.items if i.id == state.transports[0].body_id)
    assert body.condition is not None and body.condition.hp < 30
    assert (
        ground_cruising_speed(60, 3, "ground-wheeled", "average", road_bound=True, on_road=False)
        == 6
    )


def test_authored_slope_costs_and_automatic_terrain_control_loss() -> None:
    engine, state = fixture()
    board = map_fixture().model_copy(
        update={
            "cells": tuple(
                cell.model_copy(update={"elevation": 1, "extra_cost": 1})
                if cell.position == Hex(q=1, r=0)
                else cell
                for cell in map_fixture().cells
            )
        }
    )
    climbed = apply_transport(engine, state, maneuver(3, (0, 0)), system=True, board=board)
    assert (climbed.transports[0].q, climbed.transports[0].speed) == (2, 2)

    engine, state = fixture(speed=20)
    board = map_fixture().model_copy(
        update={
            "cells": tuple(
                cell.model_copy(update={"extra_cost": 2})
                if 1 <= cell.position.q <= 6 and cell.position.r == 0
                else cell
                for cell in map_fixture().cells
            )
        }
    )
    lost = apply_transport(engine, state, maneuver(20, (0,) * 8), system=True, board=board)
    assert (lost.transports[0].status, lost.transports[0].speed) == ("skidding", 8)
    assert lost.transports[0].traces[-1].reason == "automatic-terrain-control-loss"


def test_minor_skid_consumes_difficult_terrain_points() -> None:
    engine, state = fixture(speed=5, status="skidding", remaining_points=5)
    board = map_fixture().model_copy(
        update={
            "cells": tuple(
                cell.model_copy(update={"extra_cost": 1})
                if cell.position == Hex(q=1, r=0)
                else cell
                for cell in map_fixture().cells
            )
        }
    )
    resolved = apply_transport(
        engine,
        state,
        VehicleSkid(id="skid", actor_id="a", expected_revision=0, transport_id="ride"),
        system=True,
        board=board,
        health={"a": 12},
    )
    assert (resolved.transports[0].q, resolved.transports[0].remaining_points) == (4, 0)
    assert resolved.transports[0].status == "controlled"


def test_minor_skid_hits_declared_actor_and_preserves_impact_pose() -> None:
    engine, state = fixture(speed=5, status="skidding", remaining_points=5)
    resolved = apply_transport(
        engine,
        state,
        VehicleSkid(
            id="skid-hit",
            actor_id="a",
            expected_revision=0,
            transport_id="ride",
            target_actor_id="b",
            target_q=2,
            target_r=0,
        ),
        system=True,
        board=map_fixture(),
        occupied=frozenset({Hex(q=2, r=0)}),
        health={"a": 12, "b": 12},
        rng=RecordedDice([6, 1, 1]),
    )
    assert (resolved.transports[0].q, resolved.transports[0].status) == (
        1,
        "control-required",
    )
    assert next(p for p in resolved.pools if p.id == "hp:b").current == 6
    assert resolved.transports[0].traces[-2].reason == "skid-collision-actor"


def test_risky_turn_failure_stores_remaining_course_and_resolves_skid() -> None:
    engine, state = fixture(speed=6)
    command = maneuver(6, (1,) * 6, control_skill=12)
    # Velocity6/BasicMove3: early-turn penalty -1. Roll12 fails by one.
    updated = apply_transport(
        engine, state, command, system=True, board=map_fixture(), rng=RecordedDice([4, 4, 4])
    )
    assert updated.transports[0].status == "skidding"
    assert updated.transports[0].remaining_points == 6
    assert (updated.transports[0].q, updated.transports[0].r) == (0, 0)
    skid = VehicleSkid(id="skid", actor_id="a", expected_revision=1, transport_id="ride")
    resolved = apply_transport(
        engine, updated, skid, system=True, board=map_fixture(), health={"a": 12}
    )
    assert (resolved.transports[0].q, resolved.transports[0].r, resolved.transports[0].status) == (
        6,
        0,
        "controlled",
    )
    assert resolved.transports[0].attack_penalty == -1
    assert apply_transport(engine, resolved, skid, system=True, rng=RecordedDice([])) == resolved


def test_emergency_braking_check_and_full_move_before_acceleration() -> None:
    engine, state = fixture(speed=10)
    updated = apply_transport(
        engine,
        state,
        maneuver(2, (0,) * 10, control_skill=12),
        system=True,
        board=map_fixture(),
        rng=RecordedDice([3, 3, 3]),
    )
    assert (updated.transports[0].q, updated.transports[0].speed) == (10, 2)
    assert updated.transports[0].traces[-1].target == 11  # (8 - safe5)//2 = -1
    engine, state = fixture(speed=0)
    updated = apply_transport(
        engine, state, maneuver(3, (0,) * 3), system=True, board=map_fixture()
    )
    assert updated.transports[0].q == 3
    updated = apply_transport(
        engine, state, maneuver(6, (0,) * 3), system=True, board=map_fixture()
    )
    assert (updated.transports[0].q, updated.transports[0].speed) == (3, 6)
    with pytest.raises(ValidationError, match="acceleration"):
        apply_transport(engine, state, maneuver(7, (0,) * 3), system=True, board=map_fixture())


@pytest.mark.parametrize(
    "mode,altitude,waterline,elevation",
    [
        ("air", 20, None, 0),
        ("water", 0, 0, -10),
        ("underwater", -5, 0, -10),
    ],
)
def test_level_flight_and_water_depth_paths(
    mode: str, altitude: int, waterline: int | None, elevation: int
) -> None:
    engine, state = fixture(locomotion=mode, altitude=altitude, draft=2)
    command = maneuver(3, (0,) * 3, waterline=waterline)
    updated = apply_transport(
        engine, state, command, system=True, board=map_fixture(elevation=elevation)
    )
    assert updated.transports[0].q == 3
    with pytest.raises(ValidationError):
        apply_transport(engine, state, command, system=True, board=map_fixture(elevation=altitude))


@pytest.mark.parametrize(
    "mode,rolls,status,speed,altitude",
    [
        ("air", [5, 4, 4], "drifting", 10, 95),
        ("air", [6, 6, 6], "diving", 20, 100),
        ("water", [6, 6, 6], "sinking", 20, 100),
        ("underwater", [5, 4, 4], "drifting", 20, 105),
        ("space", [6, 6, 6, 5, 5, 5], "stress-failure", 20, 100),
    ],
)
def test_b469_mode_specific_control(
    mode: str, rolls: list[int], status: str, speed: int, altitude: int
) -> None:
    engine, state = fixture(locomotion=mode, speed=20, altitude=100)
    command = VehicleControl(
        id="control", actor_id="a", expected_revision=0, transport_id="ride", skill=12
    )
    updated = apply_transport(engine, state, command, system=True, rng=RecordedDice(rolls))
    t = updated.transports[0]
    assert (t.status, t.speed, t.altitude) == (status, speed, altitude)
    assert t.aim_lost
    reloaded = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_transport(engine, reloaded, command, system=True, rng=RecordedDice([])) == reloaded


@pytest.mark.parametrize(
    "facts,expected",
    [
        ((60, 25, 10, 5, "rear-end"), ((12, 0), (2, 0))),  # B432 printed example.
        ((10, 20, 100, 5, "head-on"), ((3, 0), (3, 0))),
        ((100, 5, 10, 20, "head-on"), ((3, 0), (3, 0))),
        ((10, 10, 100, 10, "head-on"), ((2, 0), (20, 0))),
        ((10, 10, 100, 50, "side-on"), ((1, 0), (1, 0))),
    ],
)
def test_b432_collision_angle_exchange(
    facts: tuple[int, int, int, int, str], expected: tuple[tuple[int, int], tuple[int, int]]
) -> None:
    assert collision_exchange(*facts) == expected


@pytest.mark.parametrize(
    "raw,armor,innate,belt,airbag,expected",
    [
        (10, 10, 0, False, False, 2),
        (11, 10, 0, False, False, 1),
        (10, 0, 10, False, False, 0),
        (5, 0, 0, True, False, 0),
        (11, 0, 0, True, False, 6),
        (11, 0, 0, False, True, 1),
        (10, 10, 0, True, False, 1),
        (10, 10, 0, True, True, 0),
    ],
)
def test_b431_b432_armor_and_restraints(
    raw: int, armor: int, innate: int, belt: bool, airbag: bool, expected: int
) -> None:
    assert (
        passenger_injury(
            raw,
            PassengerProtection(
                actor_id="a", worn_dr=armor, innate_dr=innate, belted=belt, airbag=airbag
            ),
        )
        == expected
    )


def test_two_vehicle_collision_is_one_revision_and_does_not_repeat() -> None:
    engine, state = fixture(speed=5)
    source = state.transports[0]
    target = source.model_copy(
        update={
            "id": "target",
            "body_id": "wagon",
            "operator_id": "b",
            "occupants": ("b",),
            "speed": 2,
        }
    )
    item = next(i for i in state.items if i.id == "sword")
    state = state.model_copy(
        update={
            "transports": (source, target),
            "items": (*state.items, item.model_copy(update={"id": "wagon", "owner_id": "b"})),
        }
    )
    command = VehicleImpact(
        id="collision",
        actor_id="a",
        expected_revision=0,
        transport_id="ride",
        angle="rear-end",
        target_transport_id="target",
    )
    updated = apply_transport(
        engine,
        state,
        command,
        system=True,
        health={"a": 12, "b": 12},
        rng=RecordedDice([4, 4, 4, 4]),
    )
    assert updated.revision == 1
    assert [p.current for p in updated.pools] == [6, 8]
    assert [t.status for t in updated.transports] == ["control-required", "control-required"]
    assert apply_transport(engine, updated, command, system=True, rng=RecordedDice([])) == updated
    with pytest.raises(ConflictError):
        apply_transport(engine, updated, command.model_copy(update={"speed_after": 1}), system=True)


def test_b431_rollover_retains_fractional_skid_distance() -> None:
    engine, state = fixture(speed=10, status="crashed", skid_thirds=10)
    command = VehicleRollover(id="roll", actor_id="a", expected_revision=0, transport_id="ride")
    updated = apply_transport(
        engine,
        state,
        command,
        system=True,
        board=map_fixture(),
        health={"a": 12},
        rng=RecordedDice([1, 1, 1, 1]),
    )
    t = updated.transports[0]
    assert (t.q, t.subhex_thirds, t.skid_thirds, t.speed, t.status) == (3, 1, 0, 0, "crashed")
    assert next(p for p in updated.pools if p.id == "hp:a").current == 8
    assert ResourceState.model_validate_json(updated.model_dump_json()) == updated


def test_explicit_version_and_capability_rejection_before_dice() -> None:
    engine, state = legacy_fixture()
    with pytest.raises(ValidationError, match="version 2"):
        apply_transport(engine, state, maneuver(3, (0,) * 3), system=True, rng=RecordedDice([]))
    with pytest.raises(SchemaError, match="version 2"):
        Transport.model_validate({**state.transports[0].model_dump(), "locomotion": "air"})
    engine, state = fixture(locomotion="space")
    with pytest.raises(ValidationError, match="navigation"):
        apply_transport(
            engine,
            state,
            maneuver(3, (0,) * 3),
            system=True,
            board=map_fixture(),
            rng=RecordedDice([]),
        )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_v2_collision_concurrency_and_restart(tmp_path: Path, backend: str) -> None:
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if not url:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "vehicles.sqlite", 10)
    engine, state = fixture(speed=5)
    service = ResourceService(store, engine)
    initial = campaign(engine)
    await service.create(initial, state)
    command = VehicleImpact(
        id="crash", actor_id="a", expected_revision=0, transport_id="ride", angle="immovable"
    )
    results = await asyncio.gather(
        *(
            service.execute_transport(
                initial["id"],
                command,
                authenticated_actor_id="a",
                system=True,
                health={"a": 12},
                rng=RecordedDice([4, 4]),
            )
            for _ in range(3)
        )
    )
    assert results[0] == results[1] == results[2]
    restarted = ResourceService(store, engine)
    assert (
        await restarted.execute_transport(
            initial["id"], command, authenticated_actor_id="a", system=True, rng=RecordedDice([])
        )
        == results[0]
    )


def test_breakable_obstacle_caps_both_damage_amounts() -> None:
    from wayfarer.rules.object_types import ObjectCondition, ObjectProfile

    engine, state = fixture(speed=20)
    engine.specs["bag"] = engine.specs["bag"].model_copy(
        update={"durability": ObjectProfile(construction="homogenous", hp=5, dr=2, ht=12)}
    )
    state = state.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"condition": ObjectCondition(hp=5)}) if i.id == "bag" else i
                for i in state.items
            )
        }
    )
    command = VehicleImpact(
        id="impact",
        actor_id="a",
        expected_revision=0,
        transport_id="ride",
        angle="immovable",
        obstacle_item_id="bag",
        speed_after=20,
    )
    updated = apply_transport(
        engine, state, command, system=True, health={"a": 12}, rng=RecordedDice([6, 6, 6, 6])
    )
    conditions = {i.id: i.condition for i in updated.items}
    assert conditions["sword"] is not None and conditions["sword"].hp == 5
    assert conditions["bag"] is not None and conditions["bag"].hp == 0
    assert updated.transports[0].traces[0].basic_damage == 7  # obstacle HP5 + DR2
    assert updated.pools[0].current == 10  # No speed lost, no whiplash.


def test_open_cabin_ejection_distance_uses_damage_before_armor() -> None:
    engine, state = fixture(speed=20, open_cabin=True)
    command = VehicleImpact(
        id="impact",
        actor_id="a",
        expected_revision=0,
        transport_id="ride",
        angle="immovable",
        protection=(PassengerProtection(actor_id="a", worn_dr=20, strength=10),),
    )
    updated = apply_transport(
        engine,
        state,
        command,
        system=True,
        health={"a": 12},
        rng=RecordedDice([1, 1, 1, 1, 2, 2, 2, 2]),
    )
    trace = updated.transports[0].traces[-1]
    assert (trace.basic_damage, trace.injury, trace.ejection_yards) == (8, 1, 1)
    assert updated.transports[0].status == "ejection-pending"
    assert updated.transports[0].occupants == ()
    assert updated.transports[0].pending_ejections[0].actor_id == "a"
    landed = apply_transport(
        engine,
        updated,
        ResolveVehicleEjection(
            id="land",
            actor_id="a",
            expected_revision=1,
            transport_id="ride",
            passenger_id="a",
            destination_q=1,
            destination_r=0,
        ),
        system=True,
        board=map_fixture(),
        health={"a": 12},
        rng=RecordedDice([1, 1, 1, 1]),
    )
    assert landed.transports[0].pending_ejections == ()
    assert landed.transports[0].traces[-1].model_dump()["destination_q"] == 1
    assert next(p for p in landed.pools if p.id == "hp:a").current == 5
    restored = ResourceState.model_validate_json(landed.model_dump_json())
    assert (
        apply_transport(
            engine,
            restored,
            ResolveVehicleEjection(
                id="land",
                actor_id="a",
                expected_revision=1,
                transport_id="ride",
                passenger_id="a",
                destination_q=1,
                destination_r=0,
            ),
            system=True,
            rng=RecordedDice([]),
        )
        == restored
    )
    with pytest.raises(ValidationError, match="consequences"):
        apply_transport(
            engine,
            updated,
            maneuver(3, (0,) * 3, id="after", expected_revision=1),
            system=True,
            board=map_fixture(),
            rng=RecordedDice([]),
        )


def test_water_ejection_enters_existing_drowning_schedule() -> None:
    engine, state = fixture(open_cabin=True)
    transport = state.transports[0].model_copy(
        update={
            "occupants": (),
            "status": "ejection-pending",
            "pending_ejections": (
                PassengerEjection(
                    actor_id="a",
                    origin_q=0,
                    origin_r=0,
                    facing=0,
                    distance_yards=1,
                    collision_speed=5,
                ),
            ),
        }
    )
    state = state.model_copy(update={"transports": (transport,)})
    resolved = apply_transport(
        engine,
        state,
        ResolveVehicleEjection(
            id="water-entry",
            actor_id="a",
            expected_revision=0,
            transport_id="ride",
            passenger_id="a",
            destination_q=1,
            destination_r=0,
            landing="water",
            swimming_skill=12,
        ),
        system=True,
        board=map_fixture(),
        health={"a": 12},
        rng=RecordedDice([1, 1, 1]),
    )
    assert resolved.hazards[0].spec.kind == "drowning"
    assert resolved.hazards[0].stage == "swimming"
    assert resolved.transports[0].traces[-1].reason == "ejection-water"


def test_planar_footprint_blocks_swinging_tail_through_obstacle() -> None:
    engine, state = fixture(footprint_offsets=((0, 0), (-1, 0), (0, 1)))
    board = map_fixture()
    # Turn from facing0 to1 sweeps the tail into (0,-1) before the reference hex moves.
    board = board.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"blocked": True}) if c.position == Hex(q=0, r=-1) else c
                for c in board.cells
            )
        }
    )
    with pytest.raises(ValidationError, match="collision"):
        apply_transport(
            engine, state, maneuver(1, (1,)), system=True, board=board, rng=RecordedDice([])
        )


def test_air_recovery_unsinkable_capsize_and_low_speed_stall() -> None:
    for setup, skill, dice, expected in [
        ({"locomotion": "air", "status": "diving", "altitude": 50}, 12, [2, 2, 2], "controlled"),
        ({"locomotion": "water", "unsinkable": True}, 12, [6, 6, 6], "capsized"),
        (
            {"locomotion": "air", "speed": 10, "minimum_speed": 5, "altitude": 50},
            12,
            [5, 4, 4],
            "stalled",
        ),
    ]:
        engine, state = fixture(**setup)
        if state.transports[0].status in ("diving", "stalled"):
            state = state.model_copy(
                update={
                    "transports": (
                        state.transports[0].model_copy(update={"aftermath_turn": state.game_time}),
                    )
                }
            )
        command = VehicleControl(
            id="check", actor_id="a", expected_revision=0, transport_id="ride", skill=skill
        )
        updated = apply_transport(engine, state, command, system=True, rng=RecordedDice(dice))
        assert updated.transports[0].status == expected


def test_invalid_protection_and_unmodeled_deck_reject_before_randomness() -> None:
    engine, state = fixture(speed=5)
    command = VehicleImpact(
        id="impact",
        actor_id="a",
        expected_revision=0,
        transport_id="ride",
        angle="immovable",
        protection=(PassengerProtection(actor_id="unknown"),),
    )
    with pytest.raises(ValidationError, match="passenger"):
        apply_transport(engine, state, command, system=True, health={"a": 12}, rng=RecordedDice([]))
    engine, state = fixture(locomotion="water", open_cabin=True)
    with pytest.raises(ValidationError, match="Open-deck"):
        apply_transport(
            engine,
            state,
            VehicleControl(
                id="control", actor_id="a", expected_revision=0, transport_id="ride", skill=12
            ),
            system=True,
            rng=RecordedDice([]),
        )


def test_upgrade_is_explicit_atomic_and_replayable() -> None:
    from wayfarer.simulation.vehicle_commands import UpgradeVehicle

    engine, initial = legacy_fixture()
    command = UpgradeVehicle(id="upgrade", actor_id="a", expected_revision=0, transport_id="ride")
    assert initial.transports[0].mechanics_version == 1
    updated = apply_transport(engine, initial, command, system=True)
    assert updated.transports[0].mechanics_version == 2
    assert (
        updated.revision == 1 and updated.pools == initial.pools and updated.items == initial.items
    )
    assert apply_transport(engine, updated, command, system=True) == updated
    with pytest.raises(ValidationError, match="authority"):
        apply_transport(engine, initial, command)


def test_operation_matrix_does_not_advertise_unimplemented_navigation() -> None:
    from wayfarer.rules.conformance import CoverageStatus, capability
    from wayfarer.rules.vehicle_capabilities import VEHICLE_OPERATIONS

    assert "vehicle-maneuver" not in VEHICLE_OPERATIONS["space"]
    assert not VEHICLE_OPERATIONS["ground-mount"]
    assert "vehicle-rollover" in VEHICLE_OPERATIONS["ground-tracked"]
    assert "vehicle-rollover" not in VEHICLE_OPERATIONS["water"]
    assert capability("gurps.vehicles.movement").status == CoverageStatus.PARTIAL
    assert capability("gurps.vehicles.combat").status == CoverageStatus.PARTIAL


def test_failed_air_recovery_cannot_fish_for_another_roll_in_same_second() -> None:
    engine, initial = fixture(locomotion="air", status="diving", altitude=100, aftermath_turn=0)
    command = VehicleControl(
        id="recover", actor_id="a", expected_revision=0, transport_id="ride", skill=12
    )
    updated = apply_transport(engine, initial, command, system=True, rng=RecordedDice([4, 4, 4]))
    assert updated.transports[0].status == "diving"
    with pytest.raises(ConflictError, match="recovery"):
        apply_transport(
            engine,
            updated,
            command.model_copy(update={"id": "again", "expected_revision": 1}),
            system=True,
            rng=RecordedDice([]),
        )


def test_vehicle_collision_uses_area_injury_for_diffuse_passenger() -> None:
    from wayfarer.rules.location_types import InjuryTolerance

    engine, state = fixture(speed=5)
    pool = state.pools[0]
    assert pool.injury is not None
    pool = pool.model_copy(
        update={
            "injury": pool.injury.model_copy(
                update={"anatomy": "human", "tolerance": InjuryTolerance(structure="diffuse")}
            )
        }
    )
    state = state.model_copy(update={"pools": (pool, *state.pools[1:])})
    command = VehicleImpact(
        id="crash", actor_id="a", expected_revision=0, transport_id="ride", angle="immovable"
    )
    updated = apply_transport(
        engine, state, command, system=True, health={"a": 12}, rng=RecordedDice([4, 4])
    )
    assert updated.pools[0].current == 6  # Area collision does not get the 2 HP attack cap.


def test_vehicle_movement_does_not_ignore_fractional_height_changes() -> None:
    engine, state = fixture()
    board = map_fixture()
    board = board.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"elevation_inches": 1}) if c.position == Hex(q=1, r=0) else c
                for c in board.cells
            )
        }
    )
    with pytest.raises(ValidationError, match="slope"):
        apply_transport(engine, state, maneuver(1, (0,)), system=True, board=board)
