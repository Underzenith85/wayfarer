"""Issue #714: Basic Set B368/B371/B406 shield rush execution."""

from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_actions import world
from test_gurps_melee import setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.combat.shield_rush import fall_resolution
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.catalog import (
    LITE_SOURCE,
    Damage,
    EquipmentProfile,
    MeleeMode,
    Shield,
)
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.simulation.resources import Item
from wayfarer.engine.world import Fact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def board() -> HexBattlefield:
    return HexBattlefield(
        id="dock",
        location_id="dock",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id=BASELINE_ID,
        cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(-2, 7) for r in range(-2, 3)),
    )


def shield_profile(*, can_rush: bool = True) -> EquipmentProfile:
    return EquipmentProfile(
        definition_id="equipment:rush-shield",
        provenance=LITE_SOURCE,
        weight_millipounds=15000,
        price=60,
        technology_level=1,
        slot="shield",
        shield=Shield(skill_id="skill:shield-standard", defense_bonus=2, can_rush=can_rush),
        modes=(
            MeleeMode(
                id="shield-rush",
                skill_id="skill:shield-standard",
                minimum_st=6,
                damage=Damage(basis="thrust", damage_type="cr"),
                reach=(1,),
                shield_attack=True,
            ),
        ),
        durability=ObjectProfile(construction="homogenous", hp=20, dr=0, ht=12),
    )


SHIELD_SKILL = RuleDefinition(
    "skill:shield-standard",
    DefinitionKind.SKILL,
    "Shield (Standard)",
    "sjg:basic-set-characters-4e-2004",
    None,
    ImplementationStatus.IMPLEMENTED,
    hooks=("character.gurps-skill", "check.target"),
    skill=SkillSpec(ControllingAttribute.DX, Difficulty.EASY, "B220"),
)


async def rush_setup(
    tmp_path: Path,
    *,
    can_rush: bool = True,
    target: Hex | None = None,
) -> tuple[str, PlayService]:
    test_world = world()
    test_world = replace(
        test_world,
        facts=test_world.facts
        + (Fact("seen-a", "a", "visible", "yes"), Fact("seen-b", "b", "visible", "yes")),
        knowledge=test_world.knowledge + (("a", "seen-b"), ("b", "seen-a")),
    )
    return await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        runtime_world=test_world,
        battlefield=board(),
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=target or Hex(q=5, r=0), hex_facing=3),
        ),
        extra_definitions=(SHIELD_SKILL,),
        extra_purchases=(Purchase(definition_id="skill:shield-standard", amount=4),),
        extra_equipment=(shield_profile(can_rush=can_rush),),
        extra_items=(
            Item(
                id="rush-shield",
                definition_id="equipment:rush-shield",
                owner_id="a",
                equipped=True,
                ready=True,
                condition=ObjectCondition(hp=20),
            ),
        ),
        extra_attacker_hands=(("rush-shield", "left-hand"),),
    )


def rush(*, revision: int = 1, path: tuple[Hex, ...] | None = None) -> TakeCombatTurn:
    return TakeCombatTurn(
        id="rush",
        actor_id="a",
        expected_revision=revision,
        encounter_id="fight",
        maneuver="move_and_attack",
        item_id="rush-shield",
        target_id="b",
        hex_path=path or tuple(Hex(q=q, r=0) for q in range(1, 6)),
        enter_close_combat=True,
        shield_rush=True,
    )


def defend(*, command_id: str = "defend", revision: int = 2) -> ChooseDefense:
    return ChooseDefense(
        id=command_id,
        actor_id="b",
        expected_revision=revision,
        encounter_id="fight",
        defense="none",
    )


@given(st.integers(0, 100), st.integers(0, 100))
def test_shield_rush_fall_comparison_is_mutually_exclusive(raw: int, reciprocal: int) -> None:
    result = fall_resolution(raw, reciprocal)
    if reciprocal >= 2 * raw and reciprocal > 0:
        assert result == "attacker"
    elif raw < reciprocal:
        assert result == "none"
    elif raw >= 2 * reciprocal and raw > 0:
        assert result == "defender"
    else:
        assert result == "check-defender"


async def test_shield_rush_uses_slam_damage_knockdown_and_durable_receipts(
    tmp_path: Path,
) -> None:
    cid, play = await rush_setup(tmp_path)
    service = CombatService(play)
    declaration = rush()
    opened = await service.execute(cid, declaration, principal_id="a")
    assert opened.code == "combat.defense_required"
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    assert pending.shield_rush and pending.collision_velocity == 5
    assert pending.mode_id == "shield-rush"

    # Attack succeeds; both HP-based collision rolls are 1d-2. DB makes the
    # attacker's 3 into 3 damage, the target's 4 inflicts 2 on the shield, and
    # exactly one DX roll resolves the target's knockdown.
    assert isinstance(play.store, AsyncSQLiteStore)
    dice = RecordedDice([3, 3, 3, 3, 4, 6, 6, 6])
    restarted = PlayService(
        AsyncSQLiteStore(play.store.path),
        play.engine,
        rng=dice,
    )
    result = await CombatService(restarted).execute(cid, defend(), principal_id="b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 12
    assert result.injury.damage_dice == (3,)
    assert result.injury.effect_dice == (4, 6, 6, 6)
    assert result.injury.basic_damage == 3 and result.injury.injury == 3
    assert dice.exhausted()
    state = restarted._load(await restarted.store.read(cid))
    encounter = state.encounters[0]
    participants = {p.actor_id: p for p in encounter.participants}
    assert participants["a"].position == participants["b"].position == Hex(q=5, r=0)
    assert participants["a"].posture == "standing" and participants["b"].posture == "prone"
    shield = next(i for i in state.resources.items if i.id == "rush-shield")
    assert shield.condition is not None and shield.condition.hp == 18

    restarted.rng = RecordedDice([])
    assert await CombatService(restarted).execute(cid, defend(), principal_id="b") == result
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)
    with pytest.raises(ConflictError):
        await CombatService(restarted).execute(
            cid, defend(command_id="stale", revision=2), principal_id="b"
        )


async def test_shield_rush_geometry_and_capability_fail_before_entropy(tmp_path: Path) -> None:
    cid, play = await rush_setup(tmp_path, target=Hex(q=-1, r=1))
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="front or shield-side"):
        await CombatService(play).execute(
            cid,
            rush(path=(Hex(q=-1, r=1),)),
            principal_id="a",
        )
    assert play.rng.exhausted()

    cid, play = await rush_setup(tmp_path / "buckler", can_rush=False)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="ready, equipped rushing shield"):
        await CombatService(play).execute(cid, rush(), principal_id="a")
    assert play.rng.exhausted()


async def test_shield_rush_records_critical_attack_table_without_collision(tmp_path: Path) -> None:
    cid, play = await rush_setup(tmp_path)
    await CombatService(play).execute(cid, rush(), principal_id="a")
    play.rng = RecordedDice([6, 6, 6, 2, 3, 4])
    result = await CombatService(play).execute(cid, defend(), principal_id="b")
    assert result.injury is not None
    assert result.injury.attack.outcome is Outcome.CRITICAL_FAILURE
    assert result.injury.critical_table == (2, 3, 4)
    assert result.injury.adjudication_required == "basic-critical-miss:9"
    assert result.injury.damage_dice == () and result.injury.effect_dice == ()
    assert result.injury.injury == 0
    assert play.rng.exhausted()


async def test_shield_rush_critical_success_uses_existing_damage_table(tmp_path: Path) -> None:
    cid, play = await rush_setup(tmp_path)
    await CombatService(play).execute(cid, rush(), principal_id="a")
    dice = RecordedDice([1, 1, 1, 2, 2, 2, 4, 3, 3, 3])
    play.rng = dice
    result = await CombatService(play).execute(cid, defend(), principal_id="b")
    assert result.injury is not None
    assert result.injury.attack.outcome is Outcome.CRITICAL_SUCCESS
    assert result.injury.critical_table == (2, 2, 2)
    assert result.injury.damage_dice == ()
    assert result.injury.basic_damage == 6 and result.injury.injury == 6
    assert result.injury.adjudication_required is None
    assert dice.exhausted()
