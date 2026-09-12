"""Source-backed ranged defaults and the three remaining #362 target gaps."""

from decimal import Decimal

from wayfarer.engine.character.skills import BASIC, DefaultContext, SkillCompiler
from wayfarer.engine.rules.skills.mundane import audit_report, inventory
from wayfarer.engine.rules.skills.mundane.ranged import (
    CONDITIONAL_DEFAULTS,
    PROCEDURES,
    definitions,
    ranged_scope,
)
from wayfarer.engine.rules.types.skill import ControllingAttribute, DefaultConditionKind


def attributes() -> dict[str, Decimal]:
    return {attribute.value: Decimal(10) for attribute in ControllingAttribute}


def test_source_fixtures_record_family_specific_defaults() -> None:
    """B178/B179/B198/B199/B201/B205/B222/B226."""
    assert all(
        len(PROCEDURES[f"skill:artillery-{name}"].defaults) == 1
        for name in ("beams", "bombs", "cannon", "catapult", "guided-missile", "torpedoes")
    )
    assert {
        (default.target, default.modifier)
        for default in PROCEDURES["skill:innate-attack-beam"].defaults[1:]
    } == {
        ("skill:innate-attack-breath", -2),
        ("skill:innate-attack-gaze", -2),
        ("skill:innate-attack-projectile", -2),
    }
    assert {
        (default.target, default.modifier)
        for default in PROCEDURES["skill:liquid-projector-flamethrower"].defaults[1:]
    } == {
        ("skill:liquid-projector-sprayer", -4),
        ("skill:liquid-projector-squirt-gun", -4),
        ("skill:liquid-projector-water-cannon", -4),
    }
    assert [(d.target, d.modifier) for d in PROCEDURES["skill:spear-thrower"].defaults] == [
        ("attribute:dx", -5),
        ("skill:thrown-weapon-spear", -4),
    ]
    assert [(d.target, d.modifier) for d in PROCEDURES["skill:thrown-weapon-spear"].defaults] == [
        ("attribute:dx", -4),
        ("skill:spear-thrower", -4),
        ("skill:thrown-weapon-harpoon", -2),
    ]


def test_tl_defaults_fail_closed_then_select_the_best_satisfied_edge() -> None:
    rows = {definition.id: definition for definition in definitions()}
    engine = SkillCompiler(BASIC, rows)
    points = {"skill:guns-rifle": 20}

    without_tl = {result.target: result for result in engine.compile(points, attributes())}
    assert without_tl["skill:guns-pistol"].level == 6
    assert without_tl["skill:guns-pistol"].default_from == "attribute:dx"

    matching = DefaultContext({"skill:guns-rifle": 8, "skill:guns-pistol": 8}, frozenset())
    with_tl = {
        result.target: result
        for result in engine.compile(points, attributes(), default_context=matching)
    }
    pistol = with_tl["skill:guns-pistol"]
    assert pistol.level == 14
    assert pistol.default_from == "skill:guns-rifle"
    assert pistol.default_conditions[0].kind is DefaultConditionKind.MATCHING_TECHNOLOGY_LEVEL


def test_previously_unavailable_default_targets_are_recorded() -> None:
    blocked = {
        identifier
        for identifier, procedure in PROCEDURES.items()
        if CONDITIONAL_DEFAULTS in procedure.transferred
    }
    assert blocked == set()
    published = {
        identifier for identifier, scope in ranged_scope() if scope.id == "unrecorded-default"
    }
    assert published == blocked

    entries = {entry.id: entry for entry in inventory()}
    net = entries["skill:net"].definition
    assert net is not None and net.skill is not None
    assert [(d.target, d.modifier) for d in net.skill.defaults] == [("skill:cloak", -5)]
    for identifier in ("skill:thrown-weapon-dart", "skill:thrown-weapon-shuriken"):
        definition = entries[identifier].definition
        assert definition is not None and definition.skill is not None
        assert [(d.target, d.modifier) for d in definition.skill.defaults] == [
            ("attribute:dx", -4),
            ("skill:throwing", -2),
        ]


def test_remaining_gaps_reach_the_certification_report() -> None:
    scope = audit_report()["transferred_procedure_scope"]
    assert isinstance(scope, list)
    gaps = [row for row in scope if row["id"] == "unrecorded-default"]
    assert gaps == []
