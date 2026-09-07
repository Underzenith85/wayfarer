"""Independent B367/B377/B384-392 fixtures; no rulebook prose is reproduced.

Baseline: Basic Set: Campaigns, Fourth Edition (2004), first printing,
errata 2007-01-26. Exact geometric edge conventions are engine policy, not
published examples; see docs/tactical-geometry.md for the source-audit boundary.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as ModelError

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import BASELINE_ID
from wayfarer.simulation.combat import Battlefield, CombatEngine, GridPoint
from wayfarer.simulation.hex_geometry import (
    Cell,
    Facing,
    Hex,
    HexBattlefield,
    Occupant,
    Pose,
    RetreatContext,
    SightPoint,
    arc,
    can_retreat,
    distance,
    in_reach,
    line_of_sight,
    movement,
    neighbor,
    posture_move,
    step_allowance,
)


def h(q: int, r: int) -> Hex:
    return Hex(q=q, r=r)


def board(*overrides: Cell) -> HexBattlefield:
    cells = {h(q, r): Cell(position=h(q, r)) for q in range(-4, 5) for r in range(-4, 5)}
    cells.update({cell.position: cell for cell in overrides})
    return HexBattlefield(
        id="test-map",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id=BASELINE_ID,
        cells=tuple(cells.values()),
    )


@pytest.mark.parametrize(
    ("q", "r", "expected"),
    [
        (1, 0, "front"),
        (0, 1, "front"),
        (-1, 1, "right"),
        (-1, 0, "rear"),
        (0, -1, "left"),
        (1, -1, "front"),
        (0, 0, "close"),
        (3, 0, "front"),
        (-3, 0, "rear"),
        (-2, 2, "right"),
        (0, -3, "left"),
        (-1, 2, "right"),  # exact front/right sector boundary: conservative
    ],
)
def test_b385_arcs(q: int, r: int, expected: str) -> None:
    assert arc(Pose(position=h(0, 0), facing=0), h(q, r)) == expected


@pytest.mark.parametrize("facing", [0, 1, 2, 3, 4, 5])
def test_six_facings(facing: Facing) -> None:
    pose = Pose(position=h(0, 0), facing=facing)
    assert distance(pose.position, neighbor(pose.position, facing)) == 1
    assert arc(pose, neighbor(pose.position, facing)) == "front"


@given(st.integers(-50, 50), st.integers(-50, 50), st.integers(-50, 50), st.integers(-50, 50))
def test_distance_metric(q: int, r: int, s: int, t: int) -> None:
    a, b, origin = h(q, r), h(s, t), h(0, 0)
    assert distance(a, b) == distance(b, a)
    assert (distance(a, b) == 0) == (a == b)
    assert distance(a, b) <= distance(a, origin) + distance(origin, b)


def test_hex_not_square_and_coordinate_system_is_required() -> None:
    assert distance(h(0, 0), h(-1, 1)) == 1
    assert CombatEngine.distance(GridPoint(x=1, y=0), GridPoint(x=0, y=1)) == 2
    legacy = Battlefield(id="old", location_id="room", width=5, height=5)
    with pytest.raises(ModelError):
        HexBattlefield.model_validate_json(legacy.model_dump_json())
    data = board().model_dump()
    for field, wrong in [
        ("coordinate_system", "square"),
        ("profile_id", "gurps-lite-4e-2004"),
        ("baseline_id", "latest"),
    ]:
        with pytest.raises(ModelError):
            HexBattlefield.model_validate({**data, field: wrong})
    del data["coordinate_system"]
    with pytest.raises(ModelError):
        HexBattlefield.model_validate(data)
    assert HexBattlefield.model_validate_json(board().model_dump_json()) == board()


@pytest.mark.parametrize("invalid", [True, 1.0, "1", -1, 6])
def test_facing_rejects_coercion(invalid: object) -> None:
    with pytest.raises(ModelError):
        Pose.model_validate({"position": h(0, 0), "facing": invalid})


def test_b387_forward_back_side_and_facing_costs() -> None:
    pose = Pose(position=h(0, 0), facing=0)
    result = movement(board(), pose, (h(0, 1), h(0, 2)), move=5)
    assert result.cost == 2
    assert result.destination.facing == 1
    for target in (h(-1, 0), h(-1, 1), h(0, -1)):
        result = movement(board(), pose, (target,), move=5)
        assert result.cost == 2
        assert result.destination.facing == 0
    assert movement(board(), pose, (), move=5, final_facing=1).cost == 0
    assert movement(board(), pose, (), move=5, final_facing=3).cost == 0
    assert movement(board(), pose, (h(-1, 0),), move=5, step=True, final_facing=3).cost == 1


@pytest.mark.parametrize(("move", "expected"), [(0, 1), (9, 1), (10, 1), (11, 2), (20, 2), (21, 3)])
def test_b368_step_rounding(move: int, expected: int) -> None:
    assert step_allowance(move) == expected


def test_b367_posture_and_b387_terrain_budget() -> None:
    assert posture_move(5, "standing") == 5
    assert posture_move(5, "crouching") == 3
    assert posture_move(5, "kneeling") == 1
    assert posture_move(5, "crawling") == 1
    assert posture_move(5, "lying") == 0
    assert posture_move(5, "sitting") == 0
    pose = Pose(position=h(0, 0), facing=0)
    terrain = board(Cell(position=h(1, 0), extra_cost=2))
    assert movement(terrain, pose, (h(1, 0),), move=3).cost == 3
    assert movement(terrain, pose, (h(1, 0),), move=2).cost == 3
    with pytest.raises(ValidationError, match="Posture"):
        movement(board(), Pose(position=h(0, 0), facing=0, posture="sitting"), (h(1, 0),), move=5)


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ((h(2, 0),), "adjacent"),
        ((h(1, 0),), "blocked"),
        ((h(0, 1), h(0, 2)), "allowance"),
    ],
)
def test_invalid_paths(path: tuple[Hex, ...], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        movement(
            board(Cell(position=h(1, 0), blocked=True)),
            Pose(position=h(0, 0), facing=0),
            path,
            move=1,
        )


def test_close_combat_requires_explicit_terminal_entry() -> None:
    pose = Pose(position=h(0, 0), facing=0)
    occupants = (Occupant(actor_id="foe", position=h(1, 0)),)
    with pytest.raises(ValidationError, match="close-combat"):
        movement(board(), pose, (h(1, 0),), move=5, occupants=occupants)
    result = movement(
        board(), pose, (h(1, 0),), move=5, occupants=occupants, enter_close_combat=True
    )
    assert result.destination.position == h(1, 0)
    with pytest.raises(ValidationError, match="close-combat"):
        movement(
            board(), pose, (h(1, 0), h(2, 0)), move=5, occupants=occupants, enter_close_combat=True
        )
    assert in_reach(board(), pose, h(0, 0), reaches=frozenset({0}))
    assert not in_reach(board(), pose, h(0, 0), reaches=frozenset({1}))


def test_b388_reach_and_elevation_are_explicit() -> None:
    pose = Pose(position=h(0, 0), facing=0)
    assert in_reach(board(), pose, h(2, 0), reaches=frozenset({1, 2}))
    assert not in_reach(board(), pose, h(-1, 0), reaches=frozenset({1}))
    assert not in_reach(board(), pose, h(3, 0), reaches=frozenset({1, 2}))
    elevated = board(Cell(position=h(1, 0), elevation=1))
    assert in_reach(elevated, pose, h(1, 0), reaches=frozenset({1}))
    with pytest.raises(ValidationError, match="Elevation"):
        movement(elevated, pose, (h(1, 0),), move=5)


def test_b377_b391_retreat_paths() -> None:
    defender = Pose(position=h(0, 0), facing=0)
    context = RetreatContext()
    assert can_retreat(board(), defender, h(1, 0), h(-1, 0), context=context)
    assert not can_retreat(board(), defender, h(1, 0), h(0, 1), context=context)
    assert not can_retreat(board(), defender, h(1, 0), h(-2, 0), context=context)
    assert not can_retreat(
        board(Cell(position=h(-1, 0), blocked=True)), defender, h(1, 0), h(-1, 0), context=context
    )
    assert not can_retreat(
        board(),
        defender,
        h(1, 0),
        h(-1, 0),
        context=context,
        occupants=(Occupant(actor_id="friend", position=h(-1, 0)),),
    )
    for denied in (
        RetreatContext(already_retreated=True),
        RetreatContext(stunned=True),
        RetreatContext(grappled=True),
        RetreatContext(maneuver_allows_retreat=False),
        RetreatContext(moved_more_than_basic_move=True),
    ):
        assert not can_retreat(board(), defender, h(1, 0), h(-1, 0), context=denied)


def test_los_height_obstacles_and_missing_cells() -> None:
    a, b = SightPoint(position=h(0, 0), height=2), SightPoint(position=h(3, 0), height=2)
    assert line_of_sight(board(), a, b)
    assert line_of_sight(board(Cell(position=h(1, 0), blocked=True)), a, b)
    assert not line_of_sight(board(Cell(position=h(1, 0), opaque_height=2)), a, b)
    assert line_of_sight(board(Cell(position=h(1, 0), opaque_height=1)), a, b)
    assert not line_of_sight(board(Cell(position=h(1, 0), elevation=3)), a, b)
    missing = HexBattlefield(
        id="hole",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id=BASELINE_ID,
        cells=(Cell(position=a.position), Cell(position=b.position)),
    )
    assert not line_of_sight(missing, a, b)
    elevated = board(
        Cell(position=b.position, elevation=4), Cell(position=h(1, 0), opaque_height=2)
    )
    assert line_of_sight(elevated, a, b)
    assert line_of_sight(elevated, b, a)


def test_los_boundary_ties_and_reversal() -> None:
    a, b = SightPoint(position=h(0, 0), height=2), SightPoint(position=h(2, -1), height=2)
    # Segment lies on the boundary between (1, 0) and (1, -1).
    for position in (h(1, 0), h(1, -1)):
        terrain = board(Cell(position=position, opaque_height=3))
        assert not line_of_sight(terrain, a, b)
        assert not line_of_sight(terrain, b, a)


@given(st.integers(-3, 3), st.integers(-3, 3), st.integers(-3, 3), st.integers(-3, 3))
def test_los_is_symmetric(q: int, r: int, s: int, t: int) -> None:
    a, b = SightPoint(position=h(q, r), height=2), SightPoint(position=h(s, t), height=3)
    terrain = board(Cell(position=h(1, 0), opaque_height=3))
    assert line_of_sight(terrain, a, b) == line_of_sight(terrain, b, a)


def test_explicit_turns_and_zero_move() -> None:
    pose = Pose(position=h(0, 0), facing=0)
    result = movement(board(), pose, (h(-1, 0),), move=5, turns=(3,))
    assert result.cost == 4
    assert result.destination.facing == 3
    with pytest.raises(ValidationError, match="allowance"):
        movement(board(), pose, (h(-1, 0),), move=3, turns=(3,))
    with pytest.raises(ValidationError, match="one pre-move"):
        movement(board(), pose, (h(1, 0),), move=5, turns=(0, 1))
    with pytest.raises(ValidationError, match="Zero Move"):
        movement(board(), pose, (h(1, 0),), move=0, step=True)
    with pytest.raises(ValidationError, match="nonnegative"):
        step_allowance(-1)


def test_duplicate_map_cells_and_occupants_fail_closed() -> None:
    data = board().model_dump()
    data["cells"] = (Cell(position=h(0, 0)), Cell(position=h(0, 0)))
    with pytest.raises(ModelError, match="Duplicate"):
        HexBattlefield.model_validate(data)
    pose = Pose(position=h(0, 0), facing=0)
    with pytest.raises(ValidationError, match="Duplicate occupant"):
        movement(
            board(),
            pose,
            (),
            move=5,
            occupants=(
                Occupant(actor_id="same", position=h(1, 0)),
                Occupant(actor_id="same", position=h(2, 0)),
            ),
        )
    with pytest.raises(ValidationError, match="outside"):
        movement(board(), Pose(position=h(4, 0), facing=0), (h(5, 0),), move=5)
    with pytest.raises(ValidationError, match="Origin"):
        movement(board(Cell(position=h(0, 0), blocked=True)), pose, (), move=5)


def test_tactical_capabilities_remain_fail_closed() -> None:
    from wayfarer.rules.conformance import CoverageStatus, capability, require_verified

    for name in ("hex_movement", "facing", "visibility"):
        identifier = f"gurps.tactical.{name}"
        assert capability(identifier).status is CoverageStatus.PARTIAL
        with pytest.raises(ValidationError, match="not verified"):
            require_verified(identifier)
