"""Independent arithmetic: Basic Set Characters 4e (2004), B101-102/B120-121.

Frozen first printing + 2007-01-26 errata; synthetic representative constructions,
not a claim that the mundane/supernatural catalogs or runtime are complete.
"""

from dataclasses import replace
from typing import Literal

import pytest
from pydantic import ValidationError as SchemaError
from test_statistics import BASIC, LITE, gurps_draft, profile_compiler

from wayfarer.character.compiler import Purchase
from wayfarer.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.traits import TraitModifier, TraitOptions, TraitParameter, TraitRules, cost


def definition() -> RuleDefinition:
    result = RuleDefinition(
        "trait:example",
        DefinitionKind.TRAIT,
        "Example",
        "sjg:basic-set-characters-4e-2004",
        5,
        ImplementationStatus.IMPLEMENTED,
        trait_rules=TraitRules(BASIC, maximum_level=10),
    )
    return result


@pytest.mark.parametrize(("rating", "expected"), [(6, -10), (9, -7), (12, -5), (15, -2)])
def test_self_control_rounds_toward_higher_cost(
    rating: Literal[6, 9, 12, 15], expected: int
) -> None:
    assert (
        cost(-5, 1, TraitOptions(self_control=rating), TraitRules(BASIC, self_control=True))
        == expected
    )


@pytest.mark.parametrize(
    ("base", "levels", "percents", "expected"),
    [
        (5, 3, (50, -20), 20),  # 15 * 1.30 = 19.5 -> 20; round only after levels
        (5, 1, (10,), 6),
        (10, 1, (-90,), 2),  # -80% floor
        (10, 1, (100, -90), 11),  # floor applies to net modifier
        (10, 1, (50, 50), 20),  # additive, not multiplicative
    ],
)
def test_modified_cost(base: int, levels: int, percents: tuple[int, ...], expected: int) -> None:
    modifiers = tuple(TraitModifier(str(i), percent) for i, percent in enumerate(percents))
    assert (
        cost(
            base,
            levels,
            TraitOptions(modifiers=tuple(m.id for m in modifiers)),
            TraitRules(BASIC, maximum_level=10, modifiers=modifiers),
        )
        == expected
    )


def test_typed_parameters_roundtrip_and_reject_formula() -> None:
    rules = TraitRules(
        BASIC, parameters=(TraitParameter("specialty", "text", ("vision", "hearing")),)
    )
    options = TraitOptions(parameters=(("specialty", "vision"),))
    assert TraitOptions.model_validate(options.model_dump(mode="json")) == options
    assert cost(2, 1, options, rules) == 2
    for bad in (
        TraitOptions(),
        TraitOptions(parameters=(("specialty", 1),)),
        TraitOptions(parameters=(("specialty", "vision"), ("specialty", "hearing"))),
    ):
        with pytest.raises(ValidationError):
            cost(2, 1, bad, rules)
    with pytest.raises(SchemaError):
        TraitOptions.model_validate({"formula": "base * 0.1"})
    engine = profile_compiler(BASIC, definition())
    value = gurps_draft().model_dump(mode="json")
    value["purchases"].append({"definition_id": "trait:example", "trait": {"formula": "1"}})
    assert engine.compile(value).diagnostics[0].code == "draft.schema"


def test_modifier_validation_and_profile() -> None:
    rules = TraitRules(BASIC, modifiers=(TraitModifier("a", 50, ("b",)), TraitModifier("b", -20)))
    for modifiers in (("a", "a"), ("missing",), ("a", "b")):
        with pytest.raises(ValidationError):
            cost(5, 1, TraitOptions(modifiers=modifiers), rules)
    with pytest.raises(ValidationError):
        cost(5, 1, TraitOptions(modifiers=("a",)), replace(rules, profile_id=LITE))
    with pytest.raises(ValidationError):
        cost(5, 2, TraitOptions(), rules)


def test_compiler_prerequisites_exclusions_and_runtime() -> None:
    parent = definition()
    child = replace(parent, id="trait:child", prerequisites=(parent.id,))
    engine = profile_compiler(BASIC, parent, child)
    purchase = Purchase(definition_id=child.id, amount=2)
    assert "purchase.prerequisite" in {
        d.code for d in engine.compile(gurps_draft(purchase)).diagnostics
    }
    result = engine.compile(gurps_draft(Purchase(definition_id=parent.id), purchase))
    assert result.legal and result.spent == 15
    assert not engine.compile(gurps_draft(purchase, purchase)).legal
    engine = profile_compiler(BASIC, parent, replace(child, exclusions=(parent.id,)))
    assert not engine.compile(gurps_draft(Purchase(definition_id=parent.id), purchase)).legal
    unavailable = replace(parent, trait_rules=TraitRules(BASIC, runtime_hooks=("trait.flight",)))
    result = profile_compiler(BASIC, unavailable).compile(
        gurps_draft(Purchase(definition_id=parent.id))
    )
    assert "trait.runtime_unavailable" in {d.code for d in result.diagnostics}
    wrong = replace(parent, trait_rules=TraitRules(LITE))
    assert (
        not profile_compiler(BASIC, wrong)
        .compile(gurps_draft(Purchase(definition_id=parent.id)))
        .legal
    )


def test_edited_ability_invalidates_approval_even_when_cost_unchanged() -> None:
    metadata = TraitRules(BASIC, modifiers=(TraitModifier("a", 10), TraitModifier("b", 20)))
    engine = profile_compiler(BASIC, replace(definition(), point_cost=1, trait_rules=metadata))
    reviewer = PowerReviewer(engine, PowerPolicy(id="test", version=1), frozenset({"gm"}))
    first = CharacterProposal(
        draft=gurps_draft(
            Purchase(definition_id="trait:example", trait=TraitOptions(modifiers=("a",)))
        )
    )
    second = CharacterProposal(
        draft=gurps_draft(
            Purchase(definition_id="trait:example", trait=TraitOptions(modifiers=("b",)))
        )
    )
    approval = reviewer.approve(first, campaign_id="c", actor_id="a", revision=1)
    reviewer.activate(first, approval, campaign_id="c", actor_id="a")
    assert engine.compile(first.draft).spent == engine.compile(second.draft).spent == 2
    with pytest.raises(ValidationError, match="stale"):
        reviewer.activate(second, approval, campaign_id="c", actor_id="a")


def test_self_control_and_modifier_runtime_hooks_fail_closed() -> None:
    for metadata, options in (
        (TraitRules(BASIC, self_control=True), TraitOptions(self_control=12)),
        (
            TraitRules(BASIC, modifiers=(TraitModifier("a", 10, runtime_hook="trait.a"),)),
            TraitOptions(modifiers=("a",)),
        ),
    ):
        trait = replace(
            definition(), point_cost=-5 if metadata.self_control else 5, trait_rules=metadata
        )
        result = profile_compiler(BASIC, trait).compile(
            gurps_draft(Purchase(definition_id=trait.id, trait=options))
        )
        assert "trait.runtime_unavailable" in {d.code for d in result.diagnostics}


def test_trait_options_are_preserved_and_metadata_changes_pin() -> None:
    metadata = TraitRules(BASIC, parameters=(TraitParameter("type", "text", ("a", "b")),))
    trait = replace(definition(), trait_rules=metadata)
    engine = profile_compiler(BASIC, trait)
    purchase = Purchase(
        definition_id=trait.id, amount=1, trait=TraitOptions(parameters=(("type", "a"),))
    )
    build = engine.compile(gurps_draft(purchase)).build
    assert build is not None and build.trait_purchases == (purchase,)
    other = profile_compiler(BASIC, replace(trait, trait_rules=replace(metadata, maximum_level=2)))
    assert engine.rules.packages != other.rules.packages
