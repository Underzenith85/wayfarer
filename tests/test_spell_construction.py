"""Selected-printing B235/B247-248/B480 construction behavior.

Characters third printing and Campaigns fourth printing were reopened for these
expectations. Synthetic college members isolate the distinct-college mechanism;
they do not certify construction or execution of other inventory spells.
"""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Compilation, Purchase
from wayfarer.engine.rules.catalog import RulesCatalog, RulesPackage
from wayfarer.engine.rules.magic.colleges import (
    PROFILE,
    CollegeSpellBinding,
    college_package,
    learning_spec,
)
from wayfarer.engine.rules.magic.enchantment import package as enchantment
from wayfarer.engine.rules.magic.healing import package as healing
from wayfarer.engine.rules.magic.necromantic import plane_bindings
from wayfarer.engine.rules.types.skill import Difficulty

HEALING_CHAIN = ("lend-energy", "lend-vitality", "minor-healing", "major-healing")


def compile_spells(
    packages: tuple[RulesPackage, ...], spells: tuple[tuple[str, int], ...], *, magery: int = 3
) -> Compilation:
    definitions = {d.id: d for p in packages for d in p.definitions}
    base = profile_package(PROFILE, *definitions.values())
    sources = {s.id: s for p in (base, *packages) for s in p.sources}
    combined = replace(base, sources=tuple(sources.values()))
    compiler = profile_compiler(PROFILE, package=combined)
    compiler = CharacterCompiler(
        RulesCatalog((combined,)),
        compiler.rules,
        replace(compiler.policy, point_budget=1000, skill_ceiling=40, allow_supernatural=True),
        statistics_profile=PROFILE,
    )
    purchases: tuple[Purchase, ...] = (Purchase(definition_id="trait:magery-0"),)
    if magery:
        purchases += (Purchase(definition_id="trait:magery", amount=magery),)
    value = gurps_draft(*purchases, *(Purchase(definition_id=k, amount=n) for k, n in spells))
    value = value.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 12}) if p.definition_id == "attribute:iq" else p
                for p in value.purchases
            )
        }
    )
    return compiler.compile(value)


@pytest.mark.parametrize("points,expected", [(1, 12), (2, 13), (4, 14), (8, 15)])
def test_great_healing_is_very_hard_and_magery_applies_once(points: int, expected: int) -> None:
    # B235 adds Magery to learning IQ; B170 Very Hard starts at IQ-3.
    spells = tuple(("spell:" + key, 1) for key in HEALING_CHAIN) + (
        ("spell:great-healing", points),
    )
    result = compile_spells((healing(),), spells)
    assert result.build is not None, result.diagnostics
    values = {v.target: int(v.value) for v in result.build.sheet.values}
    assert values["spell:great-healing"] == expected
    assert values["spell:major-healing"] == 12
    assert values["spell:minor-healing"] == 13


@pytest.mark.parametrize("missing", HEALING_CHAIN)
def test_great_healing_requires_entire_purchased_chain(missing: str) -> None:
    spells = tuple(
        ("spell:" + key, 1) for key in (*HEALING_CHAIN, "great-healing") if key != missing
    )
    result = compile_spells((healing(),), spells)
    assert not result.legal and result.build is None


@pytest.mark.parametrize("magery", [0, 1, 2])
def test_great_healing_requires_magery_three_despite_high_skill_points(magery: int) -> None:
    spells = tuple(("spell:" + key, 16) for key in (*HEALING_CHAIN, "great-healing"))
    assert not compile_spells((healing(),), spells, magery=magery).legal


def college_fixtures(count: int) -> tuple[RulesPackage, ...]:
    return tuple(
        college_package(
            745,
            "test-college-" + str(i),
            (
                CollegeSpellBinding(
                    "test-college-" + str(i),
                    "Test spell",
                    235,
                    "test-college-" + str(i),
                    learning=learning_spec(235),
                ),
            ),
        )
        for i in range(count)
    )


def test_enchant_needs_ten_other_colleges_and_magery_two() -> None:
    packages = college_fixtures(10) + (enchantment(),)
    prerequisites = tuple(("spell:test-college-" + str(i), 1) for i in range(10))
    result = compile_spells(packages, prerequisites + (("spell:enchant", 1),), magery=2)
    assert result.build is not None, result.diagnostics
    assert next(v.value for v in result.build.sheet.values if v.target == "spell:enchant") == 11
    assert not compile_spells(packages, prerequisites + (("spell:enchant", 16),), magery=1).legal
    assert not compile_spells(packages, prerequisites[:-1] + (("spell:enchant", 16),)).legal


def test_same_college_spells_do_not_inflate_enchant_count() -> None:
    duplicate = college_package(
        745,
        "test-college-0",
        (
            CollegeSpellBinding(
                "duplicate", "Duplicate college", 235, "test-college-0", learning=learning_spec(235)
            ),
        ),
    )
    packages = college_fixtures(9) + (duplicate, enchantment())
    spells = tuple(("spell:test-college-" + str(i), 1) for i in range(9))
    result = compile_spells(packages, spells + (("spell:duplicate", 8), ("spell:enchant", 4)))
    assert not result.legal
    assert any(d.code == "spell.prerequisite" for d in result.diagnostics)


def test_plane_shift_requires_summons_for_that_plane() -> None:
    planes = college_package(745, "gate", plane_bindings("home") + plane_bindings("other"))
    packages = college_fixtures(10) + (planes,)
    spells = tuple(("spell:test-college-" + str(i), 1) for i in range(10))
    correct = compile_spells(
        packages, spells + (("spell:planar-summons:home", 1), ("spell:plane-shift:home", 1))
    )
    assert correct.build is not None, correct.diagnostics
    assert (
        next(v.value for v in correct.build.sheet.values if v.target == "spell:plane-shift:home")
        == 12
    )
    wrong = compile_spells(
        packages, spells + (("spell:planar-summons:other", 1), ("spell:plane-shift:home", 4))
    )
    assert not wrong.legal


def test_unreviewed_learning_has_no_hard_or_empty_prerequisite_fallback() -> None:
    package = healing()
    unreviewed = college_package(
        224, "healing", (CollegeSpellBinding("unreviewed", "Unreviewed learning", 248, "healing"),)
    )
    definition = next(d for d in unreviewed.definitions if d.id == "spell:unreviewed")
    assert definition.skill is None
    result = compile_spells((unreviewed,), (("spell:unreviewed", 4),))
    assert not result.legal and result.build is None
    assert any(d.code == "definition.not_implemented" for d in result.diagnostics)
    assert (
        next(
            d.skill.difficulty
            for d in package.definitions
            if d.id == "spell:major-healing" and d.skill
        )
        is Difficulty.VERY_HARD
    )
