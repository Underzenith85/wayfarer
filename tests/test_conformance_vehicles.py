"""Independent B464/B468 and B430-432 vehicle conformance cases (#838)."""

import pytest
from test_vehicle_modes import fixture, maneuver, map_fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.movement.transport import apply_transport
from wayfarer.engine.simulation.movement.vehicles.catalog import bind_vehicle
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    collision_dice,
    collision_exchange,
)
from wayfarer.engine.simulation.movement.vehicles.commands import DamageVehicle
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError


@pytest.mark.parametrize(
    ("definition", "hp", "dr", "handling", "acceleration", "top"),
    [("vehicle:wagon", 35, 2, -3, 4, 8), ("vehicle:luxury-car", 57, 5, 0, 3, 57)],
)
def test_registered_vehicle_source_stats_reach_runtime_movement(
    definition: str, hp: int, dr: int, handling: int, acceleration: int, top: int
) -> None:
    # B464: source Wagon and Luxury Car rows, preserving Hnd/SR and Move split.
    engine, initial = fixture()
    profile = engine.specs["sword"].durability
    assert profile is not None
    engine.specs["sword"] = engine.specs["sword"].model_copy(
        update={"durability": profile.model_copy(update={"hp": hp, "dr": dr})}
    )
    initial = initial.model_copy(
        update={
            "transports": (),
            "items": tuple(
                item.model_copy(update={"condition": ObjectCondition(hp=hp)})
                if item.id == "sword"
                else item
                for item in initial.items
            ),
        }
    )
    transport = bind_vehicle(
        engine,
        initial,
        definition_id=definition,
        transport_id="ride",
        body_id="sword",
        operator_id="a",
        occupants=("a",),
    )
    assert (
        transport.handling,
        transport.stability,
        transport.acceleration,
        transport.top_speed,
    ) == (handling, 4, acceleration, top)
    state = initial.model_copy(update={"transports": (transport,)})
    command = maneuver(acceleration, (0,) * acceleration)
    updated = apply_transport(engine, state, command, system=True, board=map_fixture())
    assert (updated.transports[0].q, updated.transports[0].speed, updated.revision) == (
        acceleration,
        acceleration,
        1,
    )
    damaged = apply_transport(
        engine,
        updated,
        DamageVehicle(
            id="source-damage",
            actor_id="a",
            expected_revision=1,
            transport_id="ride",
            basic_damage=10,
            damage_type="cr",
        ),
        system=True,
        rng=RecordedDice([]),
    )
    body = next(item for item in damaged.items if item.id == "sword")
    assert body.condition is not None and body.condition.hp == hp - (10 - dr)
    restored = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_transport(engine, restored, command, system=True, rng=RecordedDice([])) == restored
    with pytest.raises(ConflictError):
        apply_transport(
            engine,
            restored,
            command.model_copy(update={"id": "stale"}),
            system=True,
            rng=RecordedDice([]),
        )
    with pytest.raises(ValidationError, match="already bound"):
        bind_vehicle(
            engine,
            state,
            definition_id=definition,
            transport_id="ride-again",
            body_id="sword",
            operator_id="a",
            occupants=("a",),
        )


def test_catalog_join_rejects_wrong_body_and_unknown_entries() -> None:
    engine, state = fixture()
    for definition in ("vehicle:luxury-car", "vehicle:invented"):
        with pytest.raises(ValidationError):
            bind_vehicle(
                engine,
                state.model_copy(update={"transports": ()}),
                definition_id=definition,
                transport_id="ride",
                body_id="sword",
                operator_id="a",
                occupants=("a",),
            )


@pytest.mark.parametrize(
    ("hp", "speed", "hard", "expected"),
    [
        (24, 1, False, (1, -3)),
        (25, 1, False, (1, -3)),
        (26, 1, False, (1, -2)),
        (50, 1, False, (1, -2)),
        (51, 1, False, (1, -1)),
        (99, 1, False, (1, -1)),
        (100, 1, False, (1, 0)),
        (149, 1, False, (1, 0)),
        (150, 1, False, (2, 0)),
        (57, 57, False, (32, 0)),
        (57, 57, True, (65, 0)),
    ],
)
def test_collision_dice_source_rounding_edges(
    hp: int, speed: int, hard: bool, expected: tuple[int, int]
) -> None:
    # B430-431: HP*velocity/100, nearest whole dice; source fractional bands.
    assert collision_dice(hp, speed, hard=hard) == expected


@pytest.mark.parametrize(
    ("angle", "expected"),
    [
        ("head-on", ((37, 0), (23, 0))),
        ("rear-end", ((28, 0), (17, 0))),
        ("side-on", ((32, 0), (20, 0))),
    ],
)
def test_catalog_sample_collision_exchange_uses_relative_velocity(
    angle: str, expected: tuple[tuple[int, int], tuple[int, int]]
) -> None:
    # B432 with B464 source HP57 car at57 and HP35 wagon at8.
    assert collision_exchange(57, 57, 35, 8, angle) == expected
