"""Independent B176-B226 fixtures for campaign- and action-scoped defaults (#476)."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from wayfarer.engine.character.skills import (
    DefaultContext,
    SkillCompiler,
    SkillError,
    SkillLevel,
    materialize_open_specialty,
)
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.types.skill import (
    BiographicalDefault,
    CampaignDefaultSelection,
    CampaignSkillSpecialty,
    DefaultActionMode,
    DefaultCondition,
    DefaultVessel,
    SkillDefault,
    SkillSpec,
    VariableFamily,
)
from wayfarer.engine.rules.types.skill import (
    ControllingAttribute as A,
)
from wayfarer.engine.rules.types.skill import (
    DefaultConditionKind as C,
)
from wayfarer.engine.rules.types.skill import (
    Difficulty as D,
)

BASIC = "gurps-basic-set-4e-2004"
ATTRIBUTES = {
    "attribute:st": Decimal(10),
    "attribute:dx": Decimal(12),
    "attribute:iq": Decimal(12),
    "attribute:ht": Decimal(10),
    "secondary:will": Decimal(10),
    "secondary:per": Decimal(10),
}
FIXTURE = Path(__file__).parent / "fixtures" / "gurps" / "contextual_default_predicates.json"


def skill(identifier: str, spec: SkillSpec) -> RuleDefinition:
    return RuleDefinition(
        identifier,
        DefinitionKind.SKILL,
        identifier,
        "sjg:basic-set-characters-4e-2004",
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill",),
        skill=spec,
    )


def levels(
    definitions: tuple[RuleDefinition, ...],
    points: dict[str, int],
    context: DefaultContext | None = None,
) -> dict[str, SkillLevel]:
    compiler = SkillCompiler(BASIC, {definition.id: definition for definition in definitions})
    return {
        result.target: result
        for result in compiler.compile(
            points, ATTRIBUTES, default_context=context or DefaultContext.empty()
        )
    }


def test_inventory_matches_each_independent_predicate_fixture() -> None:
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["owner"] == 476
    recorded = {entry.id: entry for entry in inventory()}
    for identifier, expected in fixture["cases"].items():
        entry = recorded[identifier]
        assert entry.reference == expected["reference"]
        assert entry.definition is not None and entry.definition.skill is not None
        actual = [
            (
                default.target,
                default.modifier,
                [(condition.kind.value, condition.value) for condition in default.conditions],
            )
            for default in entry.definition.skill.defaults
        ]
        assert (
            expected["target"],
            expected["modifier"],
            [tuple(condition) for condition in expected["conditions"]],
        ) in actual


def test_biographical_default_applies_only_to_the_selected_subject() -> None:
    parent = skill(
        "skill:area-knowledge",
        SkillSpec(
            A.IQ,
            D.EASY,
            "B176",
            (SkillDefault(A.IQ, -4, (DefaultCondition(C.BIOGRAPHICAL, "home-area"),)),),
        ),
    )
    family = VariableFamily("one geographic area", "chosen-with-subject")
    home = materialize_open_specialty(
        parent,
        family,
        CampaignSkillSpecialty(
            "skill:area-knowledge", "skill:area-knowledge-paris", "Area Knowledge (Paris)", "paris"
        ),
    )
    away = materialize_open_specialty(
        parent,
        family,
        CampaignSkillSpecialty(
            "skill:area-knowledge", "skill:area-knowledge-rome", "Area Knowledge (Rome)", "rome"
        ),
    )
    results = levels(
        (home, away),
        {},
        DefaultContext(
            {},
            frozenset(),
            biographical_subjects={BiographicalDefault.HOME_AREA: frozenset({"paris"})},
        ),
    )
    assert set(results) == {home.id}
    assert parent.skill is not None
    assert results[home.id].default_conditions == parent.skill.defaults[0].conditions


def test_campaign_selects_a_concrete_target_but_not_a_modifier() -> None:
    guitar = skill("skill:musical-instrument-guitar", SkillSpec(A.IQ, D.HARD, "B211"))
    connoisseur = skill(
        "skill:connoisseur-music",
        SkillSpec(
            A.IQ,
            D.AVERAGE,
            "B185",
            (SkillDefault("skill:any", -3, (DefaultCondition(C.CAMPAIGN_SELECTED),)),),
        ),
    )
    selection = CampaignDefaultSelection(connoisseur.id, "skill:any", guitar.id)
    result = levels(
        (guitar, connoisseur),
        {guitar.id: 8},
        DefaultContext({}, frozenset(), campaign_defaults=frozenset({selection})),
    )[connoisseur.id]
    assert result.default_from == guitar.id
    assert result.level == 10
    assert "skill:any" not in result.default_from
    assert not hasattr(selection, "modifier")


def test_unselected_or_unsupported_campaign_targets_fail_closed() -> None:
    source = skill(
        "skill:typing",
        SkillSpec(
            A.DX,
            D.EASY,
            "B228",
            (SkillDefault("skill:any", -3, (DefaultCondition(C.CAMPAIGN_SELECTED),)),),
        ),
    )
    unsupported = CampaignDefaultSelection(source.id, "skill:any", "skill:not-pinned")
    assert source.id not in levels((source,), {}, DefaultContext({}, frozenset()))
    assert source.id not in levels(
        (source,), {}, DefaultContext({}, frozenset(), campaign_defaults=frozenset({unsupported}))
    )


def test_minimum_tl_predicate_preserves_the_catalog_modifier() -> None:
    electronics = skill("skill:electronics-operation-sensors", SkillSpec(A.IQ, D.AVERAGE, "B189"))
    observer = skill(
        "skill:forward-observer",
        SkillSpec(
            A.IQ,
            D.AVERAGE,
            "B196",
            (
                SkillDefault(
                    electronics.id,
                    -5,
                    (DefaultCondition(C.MINIMUM_TECHNOLOGY_LEVEL, 7),),
                ),
            ),
        ),
    )
    low = levels(
        (electronics, observer),
        {electronics.id: 8},
        DefaultContext({}, frozenset(), campaign_technology_level=6),
    )
    high = levels(
        (electronics, observer),
        {electronics.id: 8},
        DefaultContext({}, frozenset(), campaign_technology_level=7),
    )
    assert observer.id not in low
    assert high[observer.id].level == 9
    assert observer.skill is not None
    assert high[observer.id].default_conditions == observer.skill.defaults[0].conditions


@pytest.mark.parametrize("mode", [DefaultActionMode.DISARM_TRAP, DefaultActionMode.RESET_TRAP])
def test_action_mode_predicate_gates_attribute_defaults(mode: DefaultActionMode) -> None:
    traps = skill(
        "skill:traps",
        SkillSpec(
            A.IQ,
            D.AVERAGE,
            "B226",
            (SkillDefault(A.DX, -5, (DefaultCondition(C.ACTION_MODE, mode.value),)),),
        ),
    )
    assert traps.id not in levels((traps,), {})
    result = levels((traps,), {}, DefaultContext({}, frozenset(), action_modes=frozenset({mode})))[
        traps.id
    ]
    assert result.level == 7
    assert traps.skill is not None
    assert result.default_conditions == traps.skill.defaults[0].conditions


def test_vessel_predicate_selects_only_the_applicable_ship_default() -> None:
    boating = skill("skill:boating-large-powerboat", SkillSpec(A.DX, D.AVERAGE, "B180"))
    shiphandling = skill(
        "skill:shiphandling-ship",
        SkillSpec(
            A.IQ,
            D.HARD,
            "B220",
            (
                SkillDefault(
                    boating.id,
                    -5,
                    (DefaultCondition(C.VESSEL, DefaultVessel.POWERED_SHIP.value),),
                ),
            ),
        ),
    )
    assert shiphandling.id not in levels((boating, shiphandling), {boating.id: 8})
    result = levels(
        (boating, shiphandling),
        {boating.id: 8},
        DefaultContext({}, frozenset(), vessel_facts=frozenset({DefaultVessel.POWERED_SHIP})),
    )[shiphandling.id]
    assert result.default_from == boating.id
    assert shiphandling.skill is not None
    assert result.default_conditions == shiphandling.skill.defaults[0].conditions


def test_invalid_context_types_and_open_specializations_are_rejected() -> None:
    base = skill("skill:area-knowledge", SkillSpec(A.IQ, D.EASY, "B176"))
    family = VariableFamily("one area", "chosen-with-subject")
    with pytest.raises(SkillError, match="open-family"):
        materialize_open_specialty(
            base,
            family,
            CampaignSkillSpecialty("skill:other", "skill:area-knowledge-home", "Home", "home"),
        )
    with pytest.raises(SkillError, match="Biographical context"):
        levels(
            (base,),
            {},
            replace(
                DefaultContext.empty(),
                biographical_subjects={
                    "home-area": frozenset({"home"})  # type: ignore[dict-item]
                },
            ),
        )


def test_issue_476_leaves_no_contextual_default_blocker() -> None:
    assert not [
        entry.id for entry in inventory() if "contextual-default-procedure" in entry.blockers
    ]
