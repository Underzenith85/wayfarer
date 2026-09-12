"""Independent Basic Set fixtures for #415; expected values are transcribed here."""

from decimal import Decimal

import pytest

from wayfarer.engine.rules.checks import draw_dice, draw_index
from wayfarer.engine.rules.combat_tables import (
    minimum_strength_penalty,
    shield_cover_dr,
    shield_defense_bonus,
    strong_damage_bonus,
    weapon_target_penalty,
)
from wayfarer.engine.rules.physical import climbing_default
from wayfarer.engine.rules.unarmed_tables import UNARMED_SKILLS, unarmed_critical_miss
from wayfarer.engine.simulation.gurps_equipment import Armor
from wayfarer.engine.simulation.hit_locations import armor_resistance, effective_dr
from wayfarer.errors import ValidationError


@pytest.mark.parametrize("minimum,current,expected", [(10, 8, 2), (10, 10, 0), (10, 13, 0)])
def test_b270_minimum_strength(minimum: int, current: int, expected: int) -> None:
    assert minimum_strength_penalty(minimum, current) == expected


@pytest.mark.parametrize("dice,expected", [(1, 2), (2, 2), (3, 3), (6, 6)])
def test_b365_strong(dice: int, expected: int) -> None:
    assert strong_damage_bonus(dice) == expected


@pytest.mark.parametrize("reach,expected", [(0, -5), (1, -4), (2, -3), (4, -3)])
def test_b400_weapon_targets(reach: int, expected: int) -> None:
    assert weapon_target_penalty(reach) == expected


def test_b408_b484_cover_and_b421_crippled_shield_arm() -> None:
    assert shield_cover_dr(9, 60, Decimal(1)) == 24
    assert shield_cover_dr(9, 60, Decimal(2)) == 12
    assert shield_cover_dr(4, 17, Decimal(2)) == 4
    assert shield_cover_dr(4, 17, Decimal("0.5")) == 16
    assert shield_defense_bonus(2, False) == 2
    assert shield_defense_bonus(2, True) == 1
    assert shield_defense_bonus(1, True) == 0


def test_b183_b349_climbing_default() -> None:
    assert climbing_default(10) == 5
    assert climbing_default(14) == 9


def test_b370_b403_unarmed_skill_permissions() -> None:
    assert UNARMED_SKILLS == {
        "punch": {"attribute:dx", "skill:brawling", "skill:boxing", "skill:karate"},
        "kick": {"attribute:dx", "skill:brawling", "skill:karate"},
        "grapple": {"attribute:dx", "skill:judo", "skill:wrestling", "skill:sumo-wrestling"},
        "arm_lock": {"skill:judo", "skill:wrestling"},
    }


@pytest.mark.parametrize(
    "total,effect",
    [
        (3, "unconscious"),
        (4, "strain"),
        (5, "self-hit"),
        (6, "self-hit"),
        (7, "stumble"),
        (8, "fall"),
        (9, "balance"),
        (10, "balance"),
        (11, "balance"),
        (12, "trip"),
        (13, "guard"),
        (14, "stumble"),
        (15, "tear"),
        (16, "self-hit"),
        (17, "strain"),
        (18, "unconscious"),
    ],
)
def test_b557_unarmed_critical_miss_rows(total: int, effect: str) -> None:
    assert unarmed_critical_miss(total).effect == effect


def test_b557_critical_miss_parameters() -> None:
    assert unarmed_critical_miss(4).strain_seconds == 1800
    assert unarmed_critical_miss(17).strain_seconds == 1800
    assert unarmed_critical_miss(5).half_damage is False
    assert unarmed_critical_miss(6).half_damage is True
    assert unarmed_critical_miss(16).half_damage is False
    for total in (9, 10, 11, 13):
        assert unarmed_critical_miss(total).defense_penalty == -2
    assert unarmed_critical_miss(12).trip_kick_penalty == -4
    assert unarmed_critical_miss(15).defense_penalty == -1
    for total in (2, 19):
        with pytest.raises(ValidationError):
            unarmed_critical_miss(total)


def test_b282_b400_armor_selection_precedes_b399_injury_dr() -> None:
    armors = (
        Armor(locations=("skull",), dr=3),
        Armor(locations=("skull",), dr=5, flexible=True),
        Armor(locations=("torso",), dr=9),
    )
    assert armor_resistance(armors, "skull") == 5
    assert armor_resistance(armors, "skull", rigid_only=True) == 3
    assert armor_resistance(armors, "face") == 0
    assert armor_resistance((), "skull") == 0
    # Bone DR and the attack divisor are applied once, after equipment selection.
    assert effective_dr(5, Decimal(2), location="skull", damage_type="cr") == 3


class OrderedRandom:
    def __init__(self) -> None:
        self.bounds: list[int] = []
        self.values = iter((0, 5, 2, 4, 1, 3, 0))

    def randbelow(self, bound: int, /) -> int:
        self.bounds.append(bound)
        return next(self.values)


def test_draw_api_preserves_count_order_and_selection_bounds() -> None:
    rng = OrderedRandom()
    assert draw_dice(rng) == (1, 6, 3)
    assert draw_dice(rng, 0) == ()
    assert draw_dice(rng, 1) == (5,)
    assert draw_dice(rng, 2) == (2, 4)
    assert draw_index(rng, 8) == 0
    assert rng.bounds == [6, 6, 6, 6, 6, 6, 8]
    with pytest.raises(ValidationError):
        draw_dice(rng, -1)
    with pytest.raises(ValidationError):
        draw_index(rng, 0)
    assert rng.bounds == [6, 6, 6, 6, 6, 6, 8]
