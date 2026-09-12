"""Launcher-assisted throws (#360): Spear Thrower B222.

Launcher facts are test-only pinned catalog metadata and claim no catalog
completeness. Unlike every other thrown row, the projectile is not the only
item involved: a separate held launcher improves the throw without being
consumed by it, and without that launcher in hand the mode does not exist.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.ranged import definitions, require_mode
from wayfarer.engine.rules.types.launcher import LauncherSpec
from wayfarer.engine.simulation.combat.encounter import RangedSituation
from wayfarer.engine.simulation.equipment.catalog import (
    LITE_SOURCE,
    Damage,
    EquipmentProfile,
    RangedMode,
)
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService

BASIC = "gurps-basic-set-4e-2004"
# A thrower that reaches half again as far and adds a point of damage.
ATLATL = LauncherSpec(
    launcher_definition_id="equipment:spear-thrower",
    range_multiplier=Decimal("1.5"),
    damage_bonus=1,
)
THROWER = EquipmentProfile(
    definition_id="equipment:spear-thrower",
    provenance=LITE_SOURCE.model_copy(
        update={
            "source_id": "sjg:basic-set-characters-4e-2004",
            "edition": "Fourth Edition, third printing (February 2008)",
            "pages": (222,),
        }
    ),
    weight_millipounds=1000,
    price=20,
    technology_level=0,
    slot="hand",
)


def spear(**changes: object) -> RangedMode:
    fields: dict[str, object] = {
        "id": "hurl",
        "skill_id": "skill:spear-thrower",
        "minimum_st": 10,
        "hands": 1,
        "damage": Damage(basis="fixed", dice=1, damage_type="cr"),
        "accuracy": 2,
        "range_basis": "yards",
        "maximum_range": 20,
        "half_damage_range": 10,
        "shots": 1,
        "reload_seconds": 0,
        "bulk": -4,
        "thrown": True,
        "launcher": ATLATL,
    }
    return RangedMode.model_validate(fields | changes)


def scene(distance: float = 2) -> tuple[RangedSituation, ...]:
    return (
        RangedSituation(attacker_id="a", defender_id="b", distance_yards=distance, size_modifier=0),
    )


async def armed(
    tmp_path: Path, *, distance: float = 2, holding: bool = True
) -> tuple[str, PlayService]:
    return await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=spear(),
        ranged_scene=scene(distance),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id="skill:spear-thrower", amount=4),),
        campaign_technology_level=2,
        extra_equipment=(THROWER,),
        extra_items=(
            Item(
                id="thrower-a",
                definition_id="equipment:spear-thrower",
                owner_id="a",
                equipped=True,
                ready=holding,
            ),
        ),
    )


async def hurl(cid: str, play: PlayService) -> object:
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="hurl")
    play.rng = RecordedDice([3, 3, 4, 2])
    return await defend(cid, play, "b")


def test_the_row_is_bound_with_its_spear_specialty_default() -> None:
    entry = {e.id: e for e in inventory()}["skill:spear-thrower"]
    assert entry.bound and entry.dispatch == "combat.ranged-attack"
    assert entry.implementation == "implemented"
    assert entry.definition is not None and entry.definition.skill is not None
    # B222: DX/A, defaulting to DX-5 or Thrown Weapon (Spear)-4.
    assert entry.definition.skill.reference == "B222"
    assert entry.definition.skill.difficulty.value == "average"
    assert [(d.target, d.modifier) for d in entry.definition.skill.defaults] == [
        ("attribute:dx", -5),
        ("skill:thrown-weapon-spear", -4),
    ]
    assert entry.blockers == ()


async def test_the_launcher_improves_the_throw_and_is_not_consumed(tmp_path: Path) -> None:
    cid, play = await armed(tmp_path)
    result = await hurl(cid, play)
    assert result.injury is not None  # type: ignore[attr-defined]
    # The launcher's pinned +1 lands on top of the spear's own die.
    assert result.injury.per_hit_damage == (2 + 1,)  # type: ignore[attr-defined]
    state = play._load(await play.store.read(cid))
    items = {i.id for i in state.resources.items}
    # The spear is spent; the thrower stays in hand for the next one.
    assert "sword-a" not in items and "thrower-a" in items
    assert state.resources.expended_items[0].id == "sword-a"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_the_launcher_extends_the_throws_reach(tmp_path: Path) -> None:
    """Twenty yards is the bare maximum; the launcher reaches thirty."""
    cid, play = await armed(tmp_path, distance=28)
    result = await hurl(cid, play)
    assert result.injury is not None  # type: ignore[attr-defined]
    cid, play = await armed(tmp_path / "far", distance=31)
    with pytest.raises(ValidationError, match="maximum ranged weapon range"):
        await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="hurl")


async def test_without_the_launcher_in_hand_the_throw_is_refused(tmp_path: Path) -> None:
    cid, play = await armed(tmp_path, holding=False)
    with pytest.raises(ValidationError, match="requires its launcher in hand"):
        await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="hurl")


def test_launcher_facts_belong_only_to_the_spear_thrower_row() -> None:
    def check(skill_id: str, **changes: object) -> str:
        fields: dict[str, object] = {
            "ranged": True,
            "thrown": True,
            "ammunition": False,
            "rate_of_fire": 1,
            "recoil": 1,
            "hands": 1,
            "tight_beam": False,
            "launched": True,
        }
        with pytest.raises(ValidationError) as error:
            require_mode(BASIC, skill_id, **(fields | changes))  # type: ignore[arg-type]
        return str(error.value)

    # A bare thrown spear is Thrown Weapon (Spear), not this row.
    assert "Launcher facts are outside" in check("skill:spear-thrower", launched=False)
    assert "Launcher facts are outside" in check("skill:thrown-weapon-spear")


def test_a_launcher_only_assists_a_thrown_mode() -> None:
    with pytest.raises(SchemaError, match="assists a thrown weapon"):
        spear(thrown=False, ammunition_id="equipment:ammo")
    with pytest.raises(SchemaError, match="change the throw"):
        LauncherSpec(launcher_definition_id="equipment:spear-thrower", range_multiplier=Decimal(1))
