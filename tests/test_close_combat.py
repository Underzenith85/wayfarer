"""Independent expectations for Campaigns B391-B392 close and multi-hex combat."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as ModelError

from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.simulation.combat.close_combat import (
    cooperative_control_score,
    enter,
    leave,
    stray_target_order,
    validate_defense,
)
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.spatial import HexActorPlacement, HexSpatialContext
from wayfarer.engine.simulation.combat.tactical import move_hex, transformed_footprint
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield, Pose
from wayfarer.errors import ValidationError


def h(q: int, r: int) -> Hex:
    return Hex(q=q, r=r)


def board(*, blocked: tuple[Hex, ...] = ()) -> HexBattlefield:
    return HexBattlefield(
        id="arena",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id=BASELINE_ID,
        cells=tuple(
            Cell(position=h(q, r), blocked=h(q, r) in blocked)
            for q in range(-3, 5)
            for r in range(-3, 5)
        ),
    )


def fighter(actor_id: str, position: Hex, facing: int) -> Combatant:
    return Combatant(
        actor_id=actor_id,
        initiative=12 if actor_id == "a" else 10,
        position=position,
        hex_facing=facing,
        reach=1,
        movement_allowance=5,
    )


def encounter(*, large: bool = False) -> Encounter:
    a, b = fighter("a", h(0, 0), 0), fighter("b", h(3, 0), 3)
    a_placement = HexActorPlacement(
        actor_id="a",
        position=h(0, 0),
        facing=0,
        occupied_hexes=(h(0, 0), h(-1, 0)) if large else (),
    )
    return Encounter(
        id="fight",
        participants=(a, b),
        turn_order=("a", "b"),
        spatial_context=HexSpatialContext(
            battlefield_id="arena",
            placements=(
                a_placement,
                HexActorPlacement(actor_id="b", position=h(3, 0), facing=3),
            ),
        ),
    )


def test_multi_hex_footprint_is_one_identity_and_transforms_atomically() -> None:
    fight = encounter(large=True)
    assert fight.occupied_hexes("a") == (h(0, 0), h(-1, 0))
    destination = Pose(position=h(1, 0), facing=1)
    assert transformed_footprint(fight, "a", destination) == (h(1, 0), h(1, -1))
    moved = move_hex(fight, fight.participants[0], "move", (h(1, 0),), 1, None, board=board())
    assert moved.position == h(1, 0) and moved.hex_facing == 1

    with pytest.raises(ValidationError, match="destination is blocked"):
        move_hex(
            fight,
            fight.participants[0],
            "move",
            (h(1, 0),),
            1,
            None,
            board=board(blocked=(h(1, -1),)),
        )


def test_multi_hex_footprint_requires_one_contiguous_head() -> None:
    with pytest.raises(ModelError, match="contain its head"):
        HexActorPlacement(
            actor_id="dragon",
            position=h(0, 0),
            facing=0,
            occupied_hexes=(h(-1, 0), h(-2, 0)),
        )
    with pytest.raises(ModelError, match="contiguous"):
        HexActorPlacement(
            actor_id="dragon",
            position=h(0, 0),
            facing=0,
            occupied_hexes=(h(0, 0), h(2, 0)),
        )


def test_close_entry_exit_and_defense_are_explicit() -> None:
    fight = encounter()
    actor = fight.participants[0].model_copy(update={"position": h(3, 0)})
    fight = fight.replace_placement(
        HexActorPlacement(actor_id="a", position=h(3, 0), facing=0)
    ).model_copy(update={"participants": (actor, fight.participants[1])})
    fight = enter(fight, actor, "b")
    assert fight.close_pairs == (("a", "b"),)
    with pytest.raises(ValidationError, match="evasion"):
        leave(fight, actor, (h(4, 0),))
    leave(fight, actor, (h(2, 0),))
    with pytest.raises(ValidationError, match="Blocking"):
        validate_defense(defense="block")
    with pytest.raises(ValidationError, match="Reach C"):
        validate_defense(defense="parry", parry_reaches=(1,))
    validate_defense(defense="parry", parry_reaches=(0,))


def test_close_combat_risk_and_cooperative_control_are_mechanical() -> None:
    assert stray_target_order(("ally", "neutral"), (2, 1)) == ("neutral", "ally")
    with pytest.raises(ValidationError, match="recorded entropy"):
        stray_target_order(("ally",), ())
    assert cooperative_control_score(14, (10,), pin=False) == 16
    assert cooperative_control_score(14, (10, 9), pin=True) == 17
    with pytest.raises(ValidationError, match="Too many helpers"):
        cooperative_control_score(14, (10, 9), pin=False)


async def test_authoritative_turn_records_same_hex_entry(tmp_path: Path) -> None:
    from test_tactical import setup

    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.orchestration.combat import CombatService

    campaign_id, play = await setup(tmp_path)
    command = TakeCombatTurn(
        id="enter-close",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="move",
        target_id="b",
        hex_path=(h(1, 0),),
        enter_close_combat=True,
    )
    result = await CombatService(play).execute(campaign_id, command, authenticated_actor_id="a")
    assert result == await CombatService(play).execute(
        campaign_id, command, authenticated_actor_id="a"
    )
    saved = play._load(await play.store.read(campaign_id)).encounters[0]
    assert saved.close_pairs == (("a", "b"),)
    assert saved.participants[0].position == saved.participants[1].position
