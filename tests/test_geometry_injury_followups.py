"""Independent Campaigns B380, B387, B402-403, B399/B552 boundary evidence.

Numeric expectations were checked against the fourth printing. Full frozen-source
reconciliation remains #191; these tests are not profile certification.
"""

from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_hex_geometry import board, h
from test_hit_locations import human, wound
from test_tactical import migration, setup

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.location_types import HitLocation, HumanLocation, InjuryTolerance
from wayfarer.simulation.combat_height import melee_height
from wayfarer.simulation.gurps_equipment import DamageType
from wayfarer.simulation.hex_geometry import (
    Cell,
    Facing,
    HexBattlefield,
    Pose,
    Stairway,
    in_reach,
    movement,
)
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.resources import ResourceState


@pytest.mark.parametrize(
    "feet,location,attack,defense",
    [
        (1, "skull", 0, 0),
        (2, "skull", 1, 0),
        (3, "skull", 1, -1),
        (4, "skull", 1, -2),
        (5, "skull", 1, -3),
        (6, "skull", 0, -3),
        (-2, "left-foot", 2, 0),
        (-3, "left-leg", 2, 1),
        (-4, "left-leg", 2, 2),
        (-5, "left-foot", 2, 3),
        (-6, "left-foot", 0, 3),
        (-2, "skull", -2, 0),
        (2, "left-foot", -2, 0),
        (2, "neck", 1, 0),
    ],
)
def test_height_bands(feet: int, location: HitLocation, attack: int, defense: int) -> None:
    effect = melee_height(Fraction(feet, 3), Fraction(0), reach=1, location=location)
    assert (effect.attack_modifier, effect.defender_modifier) == (attack, defense)


@pytest.mark.parametrize(
    "feet,location",
    [(4, "left-leg"), (-5, "face"), (6, "torso"), (-6, "torso"), (7, "skull"), (5, "random")],
)
def test_height_rejects_unreachable_parts_before_resolution(
    feet: int, location: HitLocation
) -> None:
    with pytest.raises(ValidationError):
        melee_height(Fraction(feet, 3), Fraction(0), reach=1, location=location)


def test_height_exact_inches_and_asymmetric_long_reach() -> None:
    assert (
        melee_height(Fraction(12, 36), Fraction(0), reach=1, location="skull").attack_modifier == 0
    )
    assert (
        melee_height(Fraction(13, 36), Fraction(0), reach=1, location="skull").attack_modifier == 1
    )
    assert melee_height(Fraction(-2), Fraction(0), reach=2, location="skull").attack_modifier == -2
    # Long reach does not remove the other fighter's elevation advantage.
    assert melee_height(Fraction(-2), Fraction(0), reach=2, location="skull").defender_modifier == 3
    raised = board(Cell(position=h(1, 0), elevation=3))
    assert not in_reach(raised, Pose(position=h(0, 0), facing=0), h(1, 0), reaches=frozenset({1}))
    assert in_reach(raised, Pose(position=h(0, 0), facing=0), h(1, 0), reaches=frozenset({1, 2}))


def test_authored_stairs_cost_both_directions_and_persist() -> None:
    terrain = board(Cell(position=h(1, 0), elevation_inches=24))
    terrain = terrain.model_copy(update={"stairs": (Stairway(start=h(0, 0), end=h(1, 0)),)})
    terrain = HexBattlefield.model_validate_json(terrain.model_dump_json())
    assert terrain.cell(h(1, 0)).ground == Fraction(2, 3)
    directions: tuple[Facing, ...] = (0, 3)
    for facing in directions:
        origin, target = (h(0, 0), h(1, 0)) if facing == 0 else (h(1, 0), h(0, 0))
        pose = Pose(position=origin, facing=facing)
        assert movement(terrain, pose, (target,), move=2).cost == 2
        assert movement(terrain, pose, (target,), move=1, step=True).cost == 1
    with pytest.raises(ValidationError, match="Elevation"):
        movement(
            board(Cell(position=h(1, 0), elevation_inches=24)),
            Pose(position=h(0, 0), facing=0),
            (h(1, 0),),
            move=5,
        )
    with pytest.raises(SchemaError):
        HexBattlefield.model_validate(
            terrain.model_copy(
                update={"stairs": (Stairway(start=h(0, 0), end=h(2, 0)),)}
            ).model_dump()
        )


def tolerant(tolerance: InjuryTolerance) -> ResourceState:
    state = human()
    pool = state.pools[0]
    assert pool.injury is not None
    return state.model_copy(
        update={
            "pools": (
                pool.model_copy(
                    update={"injury": pool.injury.model_copy(update={"tolerance": tolerance})}
                ),
            )
        }
    )


@pytest.mark.parametrize(
    "structure,location,kind,damage,expected",
    [
        ("unliving", "torso", "pi", 9, 3),
        ("unliving", "torso", "pi-", 10, 2),
        ("unliving", "vitals", "pi", 3, 9),
        ("unliving", "skull", "pi", 4, 8),
        ("homogenous", "vitals", "imp", 10, 5),
        ("homogenous", "skull", "pi", 12, 2),
        ("homogenous", "torso", "pi-", 9, 1),
        ("homogenous", "neck", "cut", 4, 6),
        ("diffuse", "vitals", "imp", 10, 1),
        ("diffuse", "skull", "cr", 9, 2),
    ],
)
def test_tolerance_numeric_boundaries(
    structure: str, location: HumanLocation, kind: DamageType, damage: int, expected: int
) -> None:
    status = InjuryTolerance.model_validate({"structure": structure})
    _, result = apply_injury(
        tolerant(status),
        wound(location, damage, kind),
        ht=20,
        rng=RecordedDice([2, 2, 2] * 5),
        system=True,
    )
    assert result.injury == expected
    if structure in ("homogenous", "diffuse") and expected <= 5:
        assert not result.checks


def test_missing_parts_reject_target_and_random_falls_back_without_extra_roll() -> None:
    state = tolerant(InjuryTolerance(no_neck=True, no_eyes=True))
    with pytest.raises(ValidationError, match="body part"):
        apply_injury(state, wound("left-eye", 4, "imp"), ht=10, rng=RecordedDice([]), system=True)
    command = wound("torso", 1, "cr").model_copy(update={"location": "random"})
    rng = RecordedDice([6, 6, 6])
    _, result = apply_injury(state, command, ht=10, rng=rng, system=True)
    assert result.location == "torso" and result.location_dice == (6, 6, 6)
    assert rng.exhausted()


def test_no_brain_eye_still_cripples_without_skull_multiplier() -> None:
    state = tolerant(InjuryTolerance(no_brain=True))
    updated, result = apply_injury(
        state, wound("left-eye", 2, "imp"), ht=20, rng=RecordedDice([2, 2, 2]), system=True
    )
    assert result.injury == 4
    assert updated.pools[0].injury is not None
    assert updated.pools[0].injury.lasting_injuries[0].location == "left-eye"
    assert result.checks[0].check.effective_target == 20


@pytest.mark.parametrize("source", ["area", "internal"])
def test_diffuse_does_not_cap_area_or_internal_hp_loss(source: str) -> None:
    command = Wound.model_validate(
        {
            "id": "damage",
            "actor_id": "a",
            "expected_revision": 0,
            "basic_damage": 7,
            "resistance": 0,
            "damage_type": "burn",
            "injury_source": source,
        }
    )
    _, result = apply_injury(
        tolerant(InjuryTolerance(structure="diffuse")),
        command,
        ht=20,
        rng=RecordedDice([2, 2, 2]),
        system=True,
    )
    assert result.injury == 7


def test_tolerance_and_lasting_injury_survive_checkpoint_and_exact_retry() -> None:
    state = tolerant(InjuryTolerance(structure="unliving", no_vitals=True))
    command = wound("right-arm", 8, "cr")
    updated, receipt = apply_injury(state, command, ht=20, rng=RecordedDice([2, 2, 2]), system=True)
    restored = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_injury(restored, command, ht=20, rng=RecordedDice([]), system=True) == (
        restored,
        receipt,
    )
    with pytest.raises(ConflictError):
        apply_injury(
            restored,
            command.model_copy(update={"injury_source": "internal"}),
            ht=20,
            rng=RecordedDice([]),
            system=True,
        )


async def test_elevated_melee_uses_height_and_preserves_pending_defense(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, migrate=False)
    command = migration()
    board_ = command.battlefield
    board_ = board_.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"elevation": 1}) if c.position == h(1, 0) else c
                for c in board_.cells
            )
        }
    )
    await CombatService(play).execute(
        cid, command.model_copy(update={"battlefield": board_}), authenticated_actor_id="gm"
    )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="height-attack",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            target_id="b",
            item_id="sword-a",
            mode_id="swing",
            hit_location="left-leg",
        ),
        authenticated_actor_id="a",
    )
    reloaded = PlayService(
        AsyncSQLiteStore(tmp_path / "melee.sqlite"),
        play.engine,
        rng=RecordedDice([3, 3, 3, 6, 5, 5, 2, 2, 2, 2, 2, 2]),
    )
    state = reloaded._load(await reloaded.store.read(cid))
    defense = ChooseDefense(
        id="height-defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
    )
    result = await CombatService(reloaded).execute(cid, defense, authenticated_actor_id="b")
    assert result.injury is not None
    assert result.injury.attack is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 10
    assert await CombatService(reloaded).execute(cid, defense, authenticated_actor_id="b") == result


@pytest.mark.parametrize(
    "location,roll,expected",
    [
        ("skull", (2, 2, 3), True),
        ("left-arm", (4, 4, 4), False),
        ("vitals", (4, 4, 3), True),
        ("vitals", (6, 6, 5), False),
    ],
)
def test_targeted_near_miss_is_torso_only_for_published_locations(
    location: HitLocation, roll: tuple[int, ...], expected: bool
) -> None:
    from wayfarer.rules.gurps_checks import success_roll
    from wayfarer.simulation.hit_locations import torso_near_miss

    target = sum(roll) - 1
    check = success_roll("gurps-basic-set-4e-2004", target, rng=RecordedDice(roll))
    assert torso_near_miss(location, check) is expected


async def test_melee_location_near_miss_hits_torso_and_can_be_defended(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="near-miss-attack",
            actor_id="a",
            expected_revision=2,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
            hit_location="skull",
        ),
        authenticated_actor_id="a",
    )
    # Sword-13, skull -7 = 6; a seven hits the torso. Defender still gets Dodge.
    play.rng = RecordedDice([2, 2, 3, 5, 5, 5, 2])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="near-miss-defense",
            actor_id="b",
            expected_revision=3,
            encounter_id="fight",
            defense="dodge",
        ),
        authenticated_actor_id="b",
    )
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.attack.effective_target == 6
    assert result.injury.attack.margin == -1
    assert result.injury.location == "torso"
    assert result.injury.injury > 0


async def test_unreachable_elevated_target_rejects_without_dice_or_revision(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, migrate=False)
    command = migration()
    board_ = command.battlefield.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"elevation": 2}) if c.position == h(1, 0) else c
                for c in command.battlefield.cells
            )
        }
    )
    await CombatService(play).execute(
        cid, command.model_copy(update={"battlefield": board_}), authenticated_actor_id="gm"
    )
    play.rng = RecordedDice([])
    original = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="Height requires"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="unreachable",
                actor_id="a",
                expected_revision=original.revision,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="swing",
                target_id="b",
                hit_location="torso",
            ),
            authenticated_actor_id="a",
        )
    assert play._load(await play.store.read(cid)) == original


def test_followup_fixture_ledger_executes_independent_expectations() -> None:
    import json

    ledger = json.loads(Path("tests/fixtures/gurps/conformance.json").read_text())
    cases = {case["id"]: case for case in ledger["cases"]}
    case = cases["basic-height-three-feet-head"]
    effect = melee_height(
        Fraction(case["input"]["attacker_feet"], 3),
        Fraction(case["input"]["defender_feet"], 3),
        reach=case["input"]["reach"],
        location=case["input"]["location"],
    )
    assert effect.model_dump() == case["expected"]
    case = cases["basic-unliving-torso-piercing"]
    _, result = apply_injury(
        tolerant(InjuryTolerance.model_validate({"structure": case["input"]["structure"]})),
        wound(
            case["input"]["location"], case["input"]["basic_damage"], case["input"]["damage_type"]
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.injury == case["expected"]["injury"]


def test_b387_posture_costs_and_final_facing_use_exact_budget() -> None:
    from decimal import Decimal

    crouching = Pose(position=h(0, 0), facing=0, posture="crouching")
    assert movement(board(), crouching, (h(-1, 0),), move=5).cost == Decimal("2.5")
    # Half budget remaining permits any final facing; one extra half point does not.
    assert movement(board(), crouching, (h(-1, 0),), move=5, final_facing=3).destination.facing == 3
    with pytest.raises(ValidationError, match="final facing"):
        movement(board(), crouching, (h(1, 0), h(2, 0)), move=5, final_facing=3)
    assert (
        movement(
            board(), Pose(position=h(0, 0), facing=0, posture="lying"), (h(1, 0),), move=5
        ).cost
        == 5
    )
    terrain = board(Cell(position=h(1, 0), extra_cost=5))
    assert movement(terrain, crouching, (h(1, 0),), move=1, step=True).cost == 1


def test_parrying_height_uses_own_weapon_reach() -> None:
    from wayfarer.simulation.combat_height import defense_height

    assert defense_height(Fraction(0), Fraction(2), reach=1) == -3
    assert defense_height(Fraction(0), Fraction(2), reach=2) == -1
