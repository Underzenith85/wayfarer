"""Independent numeric cases: Campaigns 4e fourth printing B397, B430-432, B468-469."""

import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_objects import fixture as object_fixture
from test_resources import campaign

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.resources import ResourceService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.conformance import BASELINE_ID
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.rules.transport_types import Transport
from wayfarer.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.simulation.resources import Pool, ResourceEngine, ResourceState
from wayfarer.simulation.transport import (
    CollideTransport,
    ControlTransport,
    Drive,
    SpookMount,
    apply_transport,
    collision_dice,
)


def fixture(*, mount: bool = False, speed: int = 0) -> tuple[ResourceEngine, ResourceState]:
    engine, state = object_fixture("unliving")
    return engine, state.model_copy(
        update={
            "pools": tuple(
                Pool(
                    id="hp:" + a,
                    current=10,
                    maximum=10,
                    injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
                )
                for a in ("a", "b")
            ),
            "transports": (
                Transport(
                    id="ride",
                    locomotion="ground-mount" if mount else "ground-wheeled",
                    body_id="b" if mount else "sword",
                    operator_id="a",
                    occupants=("a",) if mount else ("a", "b"),
                    acceleration=3,
                    top_speed=20,
                    stability=4,
                    speed=speed,
                ),
            ),
        }
    )


def board() -> HexBattlefield:
    return HexBattlefield(
        id="road",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id=BASELINE_ID,
        cells=tuple(Cell(position=Hex(q=q, r=0)) for q in range(-2, 50)),
    )


@pytest.mark.parametrize(
    "hp,speed,hard,expected",
    [
        (10, 0, False, (0, 0)),
        (10, 2, False, (1, -3)),
        (25, 1, False, (1, -3)),
        (26, 1, False, (1, -2)),
        (50, 1, False, (1, -2)),
        (51, 1, False, (1, -1)),
        (10, 10, False, (1, 0)),
        (10, 15, False, (2, 0)),
        (57, 10, True, (11, 0)),
        (10, 5, True, (1, 0)),
    ],
)
def test_b430_collision_rounding(
    hp: int, speed: int, hard: bool, expected: tuple[int, int]
) -> None:
    assert collision_dice(hp, speed, hard=hard) == expected


def test_ground_movement_acceleration_braking_and_footprint() -> None:
    engine, initial = fixture()
    move = Drive(id="move", actor_id="a", expected_revision=0, transport_id="ride", speed=3)
    moved = apply_transport(engine, initial, move, system=True, board=board())
    assert (moved.transports[0].q, moved.transports[0].speed, moved.revision) == (3, 3, 1)
    assert apply_transport(engine, moved, move, system=True) == moved
    with pytest.raises(ConflictError, match="second"):
        apply_transport(
            engine,
            moved,
            move.model_copy(update={"id": "twice", "expected_revision": 1}),
            system=True,
            board=board(),
        )
    with pytest.raises(ValidationError, match="terrain"):
        apply_transport(
            engine, initial, move, system=True, board=board(), occupied=frozenset({Hex(q=2, r=0)})
        )
    engine, fast = fixture(speed=10)
    with pytest.raises(ValidationError, match="braking"):
        apply_transport(
            engine, fast, move.model_copy(update={"speed": 4}), system=True, board=board()
        )
    stopped = apply_transport(
        engine, fast, move.model_copy(update={"speed": 5}), system=True, board=board()
    )
    assert (stopped.transports[0].q, stopped.transports[0].speed) == (10, 5)


@pytest.mark.parametrize(
    "dice,status,penalty",
    [
        ([4, 4, 4], "controlled", 0),
        ([5, 4, 4], "skidding", -1),
        ([6, 5, 5], "skidding", -4),
        ([6, 6, 5], "crashed", -5),
        ([6, 6, 6], "crashed", -6),
    ],
)
def test_b469_control_loss(dice: list[int], status: str, penalty: int) -> None:
    engine, state = fixture(speed=10)
    command = ControlTransport(
        id="control", actor_id="a", expected_revision=0, transport_id="ride", skill=12
    )
    updated = apply_transport(engine, state, command, system=True, rng=RecordedDice(dice))
    assert updated.transports[0].status == status
    assert updated.transports[0].attack_penalty == penalty
    assert updated.transports[0].aim_lost == (penalty != 0)
    reloaded = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_transport(engine, reloaded, command, system=True, rng=RecordedDice([])) == updated


@pytest.mark.parametrize("restraints,expected", [("none", 6), ("seatbelts", 10), ("airbags", 10)])
def test_b431_hard_collision_and_b432_passenger_injury(restraints: str, expected: int) -> None:
    engine, state = fixture(speed=5)
    transport = Transport.model_validate(
        {**state.transports[0].model_dump(), "restraints": restraints}
    )
    state = state.model_copy(update={"transports": (transport,)})
    command = CollideTransport(id="crash", actor_id="a", expected_revision=0, transport_id="ride")
    # HP10 * velocity5 * hard2 / 100 = 1d, each independent die is 4.
    # Body DR2 => 2 injury. Unrestrained humans each take 4 injury.
    updated = apply_transport(
        engine, state, command, system=True, health={"a": 12, "b": 12}, rng=RecordedDice([4, 4, 4])
    )
    assert next(i for i in updated.items if i.id == "sword").condition.hp == 8  # type: ignore[union-attr]
    assert [p.current for p in updated.pools] == [expected, expected]
    assert updated.transports[0].speed == 0 and updated.transports[0].status == "crashed"
    assert updated.revision == 1
    assert apply_transport(engine, updated, command, system=True, rng=RecordedDice([])) == updated


def test_b397_spooked_mount_three_successes_and_critical_failure() -> None:
    engine, state = fixture(mount=True)
    state = apply_transport(
        engine,
        state,
        SpookMount(id="spook", actor_id="a", expected_revision=0, transport_id="ride"),
        system=True,
    )
    start = state
    for second in range(3):
        state = state.model_copy(update={"game_time": second})
        state = apply_transport(
            engine,
            state,
            ControlTransport(
                id=f"calm-{second}",
                actor_id="a",
                expected_revision=state.revision,
                transport_id="ride",
                skill=12,
            ),
            system=True,
            rng=RecordedDice([3, 3, 3]),
        )
        assert state.transports[0].status == ("controlled" if second == 2 else "spooked")
        state = ResourceState.model_validate_json(state.model_dump_json())
    failed = apply_transport(
        engine,
        start,
        ControlTransport(
            id="fail", actor_id="a", expected_revision=1, transport_id="ride", skill=12
        ),
        system=True,
        rng=RecordedDice([6, 6, 6]),
    )
    assert failed.transports[0].status == "lost"


def test_authority_stale_ids_and_unsupported_modes_fail_closed() -> None:
    engine, state = fixture()
    command = Drive(id="move", actor_id="a", expected_revision=0, transport_id="ride", speed=3)
    with pytest.raises(ValidationError, match="authority"):
        apply_transport(engine, state, command)
    with pytest.raises(ValidationError, match="operator"):
        apply_transport(engine, state, command.model_copy(update={"actor_id": "b"}), system=True)
    with pytest.raises(ConflictError):
        apply_transport(
            engine, state, command.model_copy(update={"expected_revision": 2}), system=True
        )
    with pytest.raises(SchemaError):
        Transport.model_validate({**state.transports[0].model_dump(), "locomotion": "flying"})
    updated = apply_transport(engine, state, command, system=True, board=board())
    with pytest.raises(ConflictError, match="payload"):
        apply_transport(engine, updated, command.model_copy(update={"speed": 2}), system=True)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_collision_atomic_retry_and_restart(tmp_path: Path, backend: str) -> None:
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if not url:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "transport.sqlite", 10)
    engine, state = fixture(speed=5)
    service = ResourceService(store, engine)
    initial = campaign(engine)
    await service.create(initial, state)
    command = CollideTransport(id="crash", actor_id="a", expected_revision=0, transport_id="ride")
    results = await asyncio.gather(
        *(
            service.execute_transport(
                initial["id"],
                command,
                authenticated_actor_id="a",
                system=True,
                health={"a": 12, "b": 12},
                rng=RecordedDice([4, 4, 4]),
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
    with pytest.raises(ValidationError, match="authority"):
        await restarted.execute_transport(
            initial["id"], command, authenticated_actor_id="b", system=True
        )


def test_frozen_scenario_v1_rejects_internal_transport_fields() -> None:
    from wayfarer.simulation.scenario_document import InitialResources

    assert "transports" not in InitialResources.model_json_schema()["properties"]
    assert "Transport" not in InitialResources.model_json_schema().get("$defs", {})
    with pytest.raises(SchemaError, match="scenario v1"):
        InitialResources.model_validate({"transports": ()})


def test_live_play_accepts_transport_after_atomic_combat_integration() -> None:
    from test_actions import engine as action_engine
    from test_actions import seed as play_state

    engine = action_engine()
    initial = play_state(engine)
    body = next(i for i in initial.resources.items if i.owner_id == "a")
    resources = initial.resources.model_copy(
        update={
            "transports": (
                Transport(
                    id="live-ride",
                    mechanics_version=2,
                    locomotion="ground-wheeled",
                    body_id=body.id,
                    operator_id="a",
                    occupants=("a",),
                    acceleration=3,
                    top_speed=20,
                ),
            )
        }
    )
    engine.validate(initial.model_copy(update={"resources": resources}))
