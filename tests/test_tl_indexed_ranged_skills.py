"""TL-indexed personal firearm and beam weapon skills (#355).

Guns B198 and Beam Weapons B179 are TL-indexed families. Their concrete
specialties come from the B301-B304 index; the weapon statistics here are
test-only pinned metadata and claim no catalog completeness. A weapon outside
the campaign's own era fails closed rather than resolving with an invented
familiarity penalty.
"""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.ranged import PROCEDURES, definitions, require_mode
from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.rules.types.skill import DefaultConditionKind
from wayfarer.engine.simulation.combat.combat import RangedSituation
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService

BASIC = "gurps-basic-set-4e-2004"
GUNS = (
    "skill:guns-pistol",
    "skill:guns-rifle",
    "skill:guns-shotgun",
    "skill:guns-submachine-gun",
    "skill:guns-light-machine-gun",
    "skill:guns-musket",
    "skill:guns-grenade-launcher",
    "skill:guns-light-anti-armor-weapon",
)
BEAMS = ("skill:beam-weapons-pistol", "skill:beam-weapons-rifle", "skill:beam-weapons-projector")


def gun(skill_id: str, **changes: object) -> RangedMode:
    fields: dict[str, object] = {
        "id": "shot",
        "skill_id": skill_id,
        "minimum_st": 9,
        "hands": 1,
        "damage": Damage(basis="fixed", dice=1, damage_type="pi"),
        "accuracy": 2,
        "range_basis": "yards",
        "maximum_range": 200,
        "half_damage_range": 50,
        "shots": 8,
        "rate_of_fire": 3,
        "recoil": 2,
        "reload_seconds": 3,
        "bulk": -2,
        "ammunition_id": "equipment:ammo",
    }
    if skill_id.startswith("skill:beam-weapons-"):
        fields |= {
            "damage": Damage(basis="fixed", dice=1, damage_type="burn", tight_beam=True),
            "recoil": 1,
        }
    return RangedMode.model_validate(fields | changes)


def scene() -> tuple[RangedSituation, ...]:
    return (RangedSituation(attacker_id="a", defender_id="b", distance_yards=2, size_modifier=0),)


async def campaign(
    tmp_path: Path, skill_id: str, *, technology_level: int | None = 2, **changes: object
) -> tuple[str, PlayService]:
    return await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=gun(skill_id, **changes),
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id=skill_id, amount=4),),
        campaign_technology_level=technology_level,
    )


def test_both_families_expand_into_their_indexed_specialties() -> None:
    entries = {e.id: e for e in inventory()}
    for family, expanded in (("skill:guns", GUNS), ("skill:beam-weapons", BEAMS)):
        procedure = PROCEDURES[family]
        assert procedure.implemented and not procedure.dispatchable
        assert procedure.specialties == expanded
        # A family that is fully expanded owns none of its blockers any more.
        assert procedure.blockers == () and entries[family].owners == (344,)
    dispatched = {d.id for d in definitions()}
    assert set(GUNS) | set(BEAMS) <= dispatched
    for identifier in (*GUNS, *BEAMS):
        entry = entries[identifier]
        assert entry.bound and entry.dispatch == "combat.ranged-attack"
        assert entry.tl_required and "technology-level-context" not in entry.blockers
        assert entry.definition is not None and entry.definition.skill is not None
        specialty = entry.definition.skill.specialty
        assert specialty is not None and specialty.optional_parent is None
        defaults = entry.definition.skill.defaults
        assert (defaults[0].target, defaults[0].modifier) == ("attribute:dx", -4)
        assert "conditional-or-skill-defaults" not in entry.blockers
        assert all(
            default.conditions[0].kind is DefaultConditionKind.MATCHING_TECHNOLOGY_LEVEL
            for default in defaults[1:]
        )

    # B199: ordinary Guns specialties cross-default at -2; any edge involving
    # Grenade Launcher or LAW is -4. B179 also links matching Pistol/Rifle types.
    pistol = PROCEDURES["skill:guns-pistol"].defaults
    assert {(d.target, d.modifier) for d in pistol[1:]} >= {
        ("skill:guns-rifle", -2),
        ("skill:guns-grenade-launcher", -4),
        ("skill:guns-light-anti-armor-weapon", -4),
        ("skill:beam-weapons-pistol", -4),
    }
    projector = PROCEDURES["skill:beam-weapons-projector"].defaults
    assert {(d.target, d.modifier) for d in projector[1:]} == {
        ("skill:beam-weapons-pistol", -4),
        ("skill:beam-weapons-rifle", -4),
    }


@pytest.mark.parametrize("identifier", [*GUNS, *BEAMS])
async def test_each_specialty_dispatches_its_own_shot(tmp_path: Path, identifier: str) -> None:
    cid, play = await campaign(tmp_path, identifier)
    for _ in range(3):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="shot",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot")
    play.rng = RecordedDice([3, 3, 4, 3])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    # DX 10 easy with four points is 12; two yards is inside the first band.
    assert result.injury.attack.effective_target == 12
    assert result.injury.hits == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


async def fire(cid: str, play: PlayService) -> None:
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot")


async def test_a_weapon_outside_the_campaign_era_fails_closed(tmp_path: Path) -> None:
    """The fixture's weapon is a TL2 entry; a TL8 campaign is a different era."""
    cid, play = await campaign(tmp_path, "skill:guns-pistol", technology_level=8)
    with pytest.raises(ValidationError, match="outside the campaign's era"):
        await fire(cid, play)


async def test_a_tl_indexed_skill_needs_a_pinned_campaign_era(tmp_path: Path) -> None:
    cid, play = await campaign(tmp_path, "skill:guns-rifle", technology_level=None)
    with pytest.raises(ValidationError, match="pinned campaign technology level"):
        await fire(cid, play)


def test_beams_and_guns_do_not_borrow_each_others_weapons() -> None:
    def check(skill_id: str, **changes: object) -> str:
        fields: dict[str, object] = {
            "ranged": True,
            "thrown": False,
            "ammunition": True,
            "rate_of_fire": 3,
            "recoil": 2,
            "hands": 1,
            "tight_beam": False,
            "conventional_firearm": False,
        }
        with pytest.raises(ValidationError) as error:
            require_mode(BASIC, skill_id, **(fields | changes))  # type: ignore[arg-type]
        return str(error.value)

    # A tight beam is only resolvable by the beam rows.
    assert "cannot resolve this weapon mode" in check("skill:guns-rifle", tight_beam=True)
    # Conventional firearm metadata (#372) never belongs to a beam weapon.
    assert "Firearm metadata is outside" in check(
        "skill:beam-weapons-rifle", tight_beam=True, conventional_firearm=True
    )
    # A muscle-powered launcher cannot become a rapid-fire firearm.
    assert "Rapid fire and recoil are outside" in check("skill:bow", hands=2)


def test_conventional_firearm_metadata_is_accepted_by_the_gun_rows() -> None:
    assert (
        require_mode(
            BASIC,
            "skill:guns-pistol",
            ranged=True,
            thrown=False,
            ammunition=True,
            rate_of_fire=3,
            recoil=2,
            hands=1,
            tight_beam=False,
            conventional_firearm=True,
        )
        is not None
    )
    assert FirearmSpec(technology_level=6, action="repeating").malfunction_number == 17
