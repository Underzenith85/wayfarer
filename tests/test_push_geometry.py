"""Mapped footprints, approved balance bonuses and explicit geometry boundaries."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_close_combat import board as hex_board
from test_mastery_combat import purchase
from test_push_combat import declare, game, pool, response
from test_statistics import profile_package

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.displacement import displace
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    DistanceSpatialFact,
    HexActorPlacement,
    Placement,
    SpatialProvenance,
    VisibilitySpatialFact,
)
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.skills.push import declare_push, defend_push
from wayfarer.errors import ValidationError


@pytest.mark.parametrize("blocked", [False, True])
async def test_hex_push_checks_whole_defender_footprint_and_keeps_canonical_placement(
    tmp_path: Path, blocked: bool
) -> None:
    board = hex_board(blocked=(Hex(q=3, r=1),) if blocked else ()).model_copy(
        update={"id": "dock", "location_id": "dock"}
    )
    _, play, state, command = await game(
        tmp_path,
        board=board,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        ),
    )
    encounter = state.encounters[0].replace_placement(
        HexActorPlacement(
            actor_id="b",
            position=Hex(q=1, r=0),
            facing=3,
            occupied_hexes=(Hex(q=1, r=0), Hex(q=1, r=1)),
        )
    )
    encounter = CombatEngine._replace(
        encounter, fighter(encounter, "a").model_copy(update={"position": Hex(q=1, r=0)})
    ).model_copy(update={"close_pairs": (("a", "b"),)})
    state = state.model_copy(update={"encounters": (encounter,)})
    declared, _ = declare(play, state, command.model_copy(update={"enter_close_combat": False}))
    play.rng = RecordedDice([5, 5, 3, 3, 3])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert play.rng.exhausted() and result.displacement is not None
    q = 2 if blocked else 3
    encounter = changed.encounters[0]
    assert fighter(encounter, "b").position == Hex(q=q, r=0)
    assert encounter.occupied_hexes("b") == (Hex(q=q, r=0), Hex(q=q, r=1))
    assert encounter.placement("b").position == Hex(q=q, r=0)
    assert result.displacement.moved_yards == (1 if blocked else 2)
    assert bool(encounter.blocked_reason) == blocked and pool(changed, "hp:b").current == 10


async def test_shared_hex_push_uses_facing_for_direction(tmp_path: Path) -> None:
    board = hex_board().model_copy(update={"id": "dock", "location_id": "dock"})
    _, play, state, command = await game(
        tmp_path,
        board=board,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        ),
    )
    encounter = state.encounters[0]
    encounter = CombatEngine._replace(
        encounter, fighter(encounter, "a").model_copy(update={"position": Hex(q=1, r=0)})
    ).model_copy(update={"close_pairs": (("a", "b"),)})
    state = state.model_copy(update={"encounters": (encounter,)})
    declared, _ = declare(play, state, command.model_copy(update={"enter_close_combat": False}))
    play.rng = RecordedDice([5, 5, 3, 3, 3])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert result.displacement is not None and result.displacement.moved_yards == 2
    assert fighter(changed.encounters[0], "b").position == Hex(q=3, r=0)
    assert not changed.encounters[0].close_pairs


@pytest.mark.parametrize("facing,expected", [(0, None), (1, 6), (2, 8), (3, 8), (4, 8), (5, 6)])
async def test_closing_push_preserves_source_rear_and_flank_defense_geometry(
    tmp_path: Path, facing: Literal[0, 1, 2, 3, 4, 5], expected: int | None
) -> None:
    board = hex_board().model_copy(update={"id": "dock", "location_id": "dock"})
    _, play, state, command = await game(
        tmp_path,
        board=board,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=facing),
        ),
    )
    declared, _ = declare(play, state, command)
    assert fighter(declared.encounters[0], "a").position == Hex(q=1, r=0)
    selected = response(play, declared, defense="dodge")
    if expected is None:
        before = declared.model_dump_json()
        play.rng = RecordedDice([])
        with pytest.raises(ValidationError, match="rear attack"):
            defend_push(play.rules_context, declared, selected, authorized_actor_id="b")
        assert declared.model_dump_json() == before and play.rng.exhausted()
        return
    play.rng = RecordedDice([2, 2, 3] + ([5, 5, 3, 3, 3] if expected == 6 else []))
    changed, result = defend_push(play.rules_context, declared, selected, authorized_actor_id="b")
    assert result.defense is not None and result.defense.effective_target == expected
    assert result.defense.outcome.succeeded == (expected == 8)
    assert result.basic_damage == (18 if expected == 6 else 0)
    assert fighter(changed.encounters[0], "b").position == Hex(q=3 if expected == 6 else 1, r=0)
    assert play.rng.exhausted()


async def test_already_shared_hex_does_not_invent_a_rear_approach(tmp_path: Path) -> None:
    board = hex_board().model_copy(update={"id": "dock", "location_id": "dock"})
    _, play, state, command = await game(
        tmp_path,
        board=board,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=0),
        ),
    )
    encounter = state.encounters[0]
    encounter = CombatEngine._replace(
        encounter, fighter(encounter, "a").model_copy(update={"position": Hex(q=1, r=0)})
    ).model_copy(update={"close_pairs": (("a", "b"),)})
    state = state.model_copy(update={"encounters": (encounter,)})
    declared, _ = declare(play, state, command.model_copy(update={"enter_close_combat": False}))
    play.rng = RecordedDice([2, 2, 3])
    _, result = defend_push(
        play.rules_context,
        declared,
        response(play, declared, defense="dodge"),
        authorized_actor_id="b",
    )
    assert result.defense is not None and result.defense.effective_target == 8
    assert result.basic_damage == 0 and play.rng.exhausted()


async def test_nonanchor_multihex_contact_rejects_before_randomness(tmp_path: Path) -> None:
    board = hex_board().model_copy(update={"id": "dock", "location_id": "dock"})
    _, play, state, command = await game(
        tmp_path,
        board=board,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        ),
    )
    encounter = state.encounters[0].replace_placement(
        HexActorPlacement(
            actor_id="b",
            position=Hex(q=1, r=0),
            facing=3,
            occupied_hexes=(Hex(q=1, r=0), Hex(q=1, r=1)),
        )
    )
    encounter = CombatEngine._replace(
        encounter, fighter(encounter, "a").model_copy(update={"position": Hex(q=1, r=1)})
    ).model_copy(update={"close_pairs": (("a", "b"),)})
    state = state.model_copy(update={"encounters": (encounter,)})
    before = state.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="out of reach"):
        declare_push(
            play.rules_context,
            state,
            command.model_copy(update={"enter_close_combat": False}),
            authorized_actor_id="a",
        )
    assert state.model_dump_json() == before and play.rng.exhausted()


@pytest.mark.parametrize("stance", [False, True])
async def test_purchased_perfect_balance_and_highest_learned_balance_skill_apply(
    tmp_path: Path, stance: bool
) -> None:
    source = profile_package("gurps-basic-set-4e-2004").sources[0].id
    balance = next(
        replace(d, source_id=source)
        for d in candidate_package().definitions
        if d.id == "trait:advantage:perfect-balance"
    )
    acrobatics = RuleDefinition(
        "skill:acrobatics",
        DefinitionKind.SKILL,
        "Acrobatics",
        source,
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.DX, Difficulty.HARD, "B174"),
    )
    _, play, state, command = await game(
        tmp_path,
        stance=stance,
        extra_definitions=(balance, acrobatics),
        extra_purchases=(
            purchase("perfect-balance"),
            Purchase(definition_id="skill:acrobatics", amount=16),
        ),
    )
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([5, 5, 4, 4, 3])
    changed, result = defend_push(
        play.rules_context,
        declared,
        response(play, declared, stance=stance),
        authorized_actor_id="b",
    )
    assert play.rng.exhausted() and result.displacement is not None
    check = result.displacement.checks[0]
    assert check.effective_target == (12 if stance else 16)  # Stance10-2+4 or Acrobatics13-1+4
    assert result.displacement.prevented == stance
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=1 if stance else 3, y=0)
    assert fighter(changed.encounters[0], "b").posture == "standing"


@pytest.mark.parametrize("direction", [(0, 0), (2, 0), (1, 1)])
async def test_invalid_displacement_direction_rejects_before_balance(
    tmp_path: Path, direction: tuple[int, int]
) -> None:
    _, play, state, _ = await game(tmp_path)
    before = state.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="adjacent mapped step"):
        displace(
            play.rules_context,
            state,
            state.encounters[0],
            source_id="a",
            target_id="b",
            basic_damage=18,
            attack_direction=direction,
        )
    assert state.model_dump_json() == before


@pytest.mark.parametrize("visible", [False, True])
async def test_mapless_push_checks_visibility_and_does_not_invent_direction(
    tmp_path: Path, visible: bool
) -> None:
    _, play, state, command = await game(tmp_path)
    provenance = SpatialProvenance(
        source="scenario", source_id="fixture", declared_by="gm", declared_revision=1
    )
    spatial = BasicSpatialContext(
        facts=(
            DistanceSpatialFact(subject_id="a", object_id="b", yards=0, provenance=provenance),
            VisibilitySpatialFact(
                subject_id="a", object_id="b", visible=visible, provenance=provenance
            ),
        )
    )
    encounter = state.encounters[0].model_copy(
        update={
            "spatial_context": spatial,
            "participants": tuple(
                p.model_copy(update={"position": None}) for p in state.encounters[0].participants
            ),
            "close_pairs": (("a", "b"),),
        }
    )
    state = state.model_copy(update={"encounters": (encounter,)})
    command = command.model_copy(update={"enter_close_combat": False})
    if not visible:
        play.rng = RecordedDice([])
        with pytest.raises(ValidationError, match="unavailable"):
            declare_push(play.rules_context, state, command, authorized_actor_id="a")
        return
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([5, 5, 3, 3, 3])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert result.displacement is not None
    assert result.displacement.adjudication_required == "mapless-direction"
    assert result.displacement.moved_yards == 0
    assert fighter(changed.encounters[0], "b").runtime_position is None
    assert changed.encounters[0].blocked_reason is not None
