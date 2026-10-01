"""Independent selected-printing construction cases for audit #834.

Expected numbers are transcribed from B10-17, B170-173 and B230, rather than
calculated with the rules helpers. Test definitions isolate construction rules
from catalog completeness; registered definitions exercise real compilation.
"""

from dataclasses import replace
from decimal import Decimal

import pytest
from test_skills import attrs, definition, skill_engine

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.skills import relative_level
from wayfarer.engine.character.statistics import (
    Encumbrance,
    encumbered_dodge,
    encumbered_move,
    encumbrance,
)
from wayfarer.engine.rules.profiles import GURPS_BASIC_PROFILE
from wayfarer.engine.rules.types.skill import (
    ControllingAttribute,
    Difficulty,
    SkillDefault,
    SkillSpec,
)

BASIC = "gurps-basic-set-4e-2004"


def construction_compiler() -> CharacterCompiler:
    profile = GURPS_BASIC_PROFILE
    return CharacterCompiler(
        profile.catalog,
        profile.rules,
        replace(
            profile.policy,
            point_budget=1000,
            disadvantage_limit=1000,
            attribute_ceiling=25,
            skill_ceiling=50,
        ),
        statistics_profile=profile.conformance_profile_id,
    )


def test_purchased_statistics_compose_in_actual_build() -> None:
    # B14-17: ST +1 costs 10, DX +2 costs 40, IQ -1 returns 20,
    # HT +1 costs 10. Independent secondaries cost 2, -5, 5, -3, 5, 5.
    purchases = {
        "attribute:st": 11,
        "attribute:dx": 12,
        "attribute:iq": 9,
        "attribute:ht": 11,
        "secondary:hp": 12,
        "secondary:will": 8,
        "secondary:per": 10,
        "secondary:fp": 10,
        "secondary:basic-speed": 24,
        "secondary:basic-move": 7,
    }
    engine = construction_compiler()
    result = engine.compile(
        CharacterDraft(
            name="Construction",
            purchases=tuple(
                Purchase(definition_id=key, amount=value) for key, value in purchases.items()
            ),
        )
    )
    assert result.legal and result.build is not None
    build = result.build
    assert build.spent == 49
    assert build.statistics is not None
    statistics = build.statistics
    assert (statistics.hp, statistics.will, statistics.per, statistics.fp) == (12, 8, 10, 10)
    assert (statistics.basic_speed, statistics.basic_move, statistics.dodge) == (Decimal(6), 7, 9)
    assert statistics.basic_lift == Decimal(24)
    assert (str(statistics.thrust), str(statistics.swing)) == ("1d-1", "1d+1")


@pytest.mark.parametrize(
    ("load", "band", "move", "dodge"),
    [
        ("24", Encumbrance.NONE, 7, 9),
        ("24.01", Encumbrance.LIGHT, 5, 8),
        ("48", Encumbrance.LIGHT, 5, 8),
        ("48.01", Encumbrance.MEDIUM, 4, 7),
        ("72", Encumbrance.MEDIUM, 4, 7),
        ("72.01", Encumbrance.HEAVY, 2, 6),
        ("144", Encumbrance.HEAVY, 2, 6),
        ("144.01", Encumbrance.EXTRA_HEAVY, 1, 5),
        ("240", Encumbrance.EXTRA_HEAVY, 1, 5),
    ],
)
def test_encumbrance_inclusive_edges(load: str, band: Encumbrance, move: int, dodge: int) -> None:
    # B17, BL 24: threshold products 24/48/72/144/240; fractions drop.
    assert encumbrance(BASIC, Decimal(24), Decimal(load)) is band
    assert encumbered_move(BASIC, 7, band) == move
    assert encumbered_dodge(9, band) == dodge
    assert encumbrance(BASIC, Decimal(24), Decimal("240.01")) is None


def test_encumbrance_never_reduces_dodge_below_one() -> None:
    # B17's minimum applies to Dodge as well as Move; low Basic Speed makes
    # the boundary reachable with a legal build rather than invalid input.
    assert encumbered_dodge(3, Encumbrance.HEAVY) == 1
    assert encumbered_dodge(3, Encumbrance.EXTRA_HEAVY) == 1
    assert encumbered_move(BASIC, 1, Encumbrance.EXTRA_HEAVY) == 1


@pytest.mark.parametrize(
    ("difficulty", "levels"),
    [
        (Difficulty.EASY, (0, 1, 2, 3, 4, 5)),
        (Difficulty.AVERAGE, (-1, 0, 1, 2, 3, 4)),
        (Difficulty.HARD, (-2, -1, 0, 1, 2, 3)),
        (Difficulty.VERY_HARD, (-3, -2, -1, 0, 1, 2)),
    ],
)
def test_skill_cost_table_independent_columns(
    difficulty: Difficulty, levels: tuple[int, ...]
) -> None:
    # B170's named point columns, including the +4-point progression.
    assert tuple(relative_level(difficulty, points) for points in (1, 2, 4, 8, 12, 16)) == levels


@pytest.mark.parametrize(("points", "karate", "kicking"), [(2, 9, 8), (3, 9, 9)])
def test_hard_technique_cost_and_parent_cap(points: int, karate: int, kicking: int) -> None:
    # B230/B232: Karate (Hard) 2 points at DX10 gives 9; Kicking default -2;
    # hard techniques cost 2 for default+1 and 3 for default+2.
    result = {
        row.target: row.level
        for row in skill_engine().compile(
            {"skill:karate": 2, "skill:kicking-karate": points}, attrs()
        )
    }
    assert result["skill:karate"] == karate
    assert result["skill:kicking-karate"] == kicking


@pytest.mark.parametrize(("points", "expected"), [(1, 11), (2, 12), (4, 14)])
def test_average_technique_cost(points: int, expected: int) -> None:
    # B230: Arm Lock (Judo) defaults to Judo, one point per +1, maximum +4.
    result = {
        row.target: row.level
        for row in skill_engine().compile({"skill:judo": 4, "skill:arm-lock-judo": points}, attrs())
    }
    assert result["skill:judo"] == 10
    assert result["skill:arm-lock-judo"] == expected


@pytest.mark.parametrize(("dx", "expected"), [(10, 5), (20, 15), (25, 15)])
def test_attribute_default_uses_twenty_ceiling(dx: int, expected: int) -> None:
    # B173: attribute-based defaults cap the attribute at 20 before modifiers.
    target = definition(
        "skill:default-case",
        SkillSpec(
            ControllingAttribute.DX,
            Difficulty.AVERAGE,
            "B173",
            defaults=(SkillDefault("attribute:dx", -5),),
        ),
    )
    result = skill_engine(target).compile({}, attrs(**{"attribute:dx": dx}))
    assert result[0].level == expected and result[0].points == 0
