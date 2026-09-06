"""Abuse and invariant tests for server-owned character compilation."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wayfarer.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    PROTOTYPE_PACKAGE,
    PROTOTYPE_SOURCE,
    CampaignPolicy,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.rules.effects import Effect, Operation


def compiler(
    *extra: RuleDefinition,
    policy: CampaignPolicy = DEFAULT_POLICY,
    effects: tuple[tuple[str, Effect], ...] = (),
) -> CharacterCompiler:
    package = replace(PROTOTYPE_PACKAGE, definitions=PROTOTYPE_PACKAGE.definitions + extra)
    rules = replace(
        DEFAULT_RULES,
        policy_id=policy.id,
        policy_version=policy.version,
        packages=(PackagePin(package.id, package.version, package.digest),),
    )
    return CharacterCompiler(RulesCatalog((package,)), rules, policy, effects)


def trait(key: str, cost: int = 5) -> RuleDefinition:
    return RuleDefinition(
        key, DefinitionKind.TRAIT, key, PROTOTYPE_SOURCE.id, cost, ImplementationStatus.IMPLEMENTED
    )


def draft(*purchases: Purchase, st_level: int = 10) -> CharacterDraft:
    return CharacterDraft(
        name="Mira",
        purchases=tuple(
            Purchase(definition_id=f"attribute:{key}", amount=st_level if key == "st" else 10)
            for key in ("st", "dx", "iq", "ht")
        )
        + purchases,
    )


def codes(engine: CharacterCompiler, value: object) -> set[str]:
    result = engine.compile(value)
    assert result.build is None
    return {d.code for d in result.diagnostics}


def test_compile_server_totals_immutable_revision_and_dry_run() -> None:
    engine = compiler(trait("gift"))
    value = draft(
        Purchase(definition_id="gift"),
        Purchase(definition_id="skill:stealth", amount=4),
        st_level=11,
    )
    result = engine.compile(value)
    assert result.legal and result.spent == 19 and result.remaining == 81
    assert result.build is not None
    assert engine.compile(value).build == result.build
    assert engine.compile(value, dry_run=True).legal
    assert engine.compile(value, dry_run=True).build is None
    assert engine.compile(value.model_dump(mode="json")).build == result.build
    with pytest.raises(FrozenInstanceError):
        field = "spent"
        setattr(result.build, field, 0)
    changed = engine.compile(draft(Purchase(definition_id="gift"), st_level=12)).build
    assert changed is not None and changed.revision != result.build.revision


@pytest.mark.parametrize(
    "field,value", [("spent", 0), ("effects", []), ("hp", 999), ("discount", 99)]
)
def test_forged_authority_rejected(field: str, value: object) -> None:
    payload = draft().model_dump(mode="json")
    payload[field] = value
    assert "draft.schema" in codes(compiler(), payload)


@pytest.mark.parametrize("amount", [True, 1.5, "12", -1, 0])
def test_non_integer_and_invalid_amounts(amount: object) -> None:
    payload = {"name": "Mira", "purchases": [{"definition_id": "attribute:st", "amount": amount}]}
    assert "draft.schema" in codes(compiler(), payload)


def test_unknown_manual_unsupported_and_duplicate_discounts() -> None:
    engine = compiler(
        trait("discount", -10),
        replace(trait("unsupported"), status=ImplementationStatus.UNSUPPORTED),
    )
    assert "definition.unknown" in codes(engine, draft(Purchase(definition_id="unknown")))
    assert "definition.not_implemented" in codes(
        engine, draft(Purchase(definition_id="trait:curious"))
    )
    assert "definition.not_implemented" in codes(
        engine, draft(Purchase(definition_id="unsupported"))
    )
    duplicated = draft(Purchase(definition_id="discount"), Purchase(definition_id="discount"))
    assert "purchase.duplicate" in codes(engine, duplicated)
    assert engine.compile(duplicated).spent == -10
    repair = engine.repair(duplicated)
    assert repair is not None and repair.compilation.legal and repair.compilation.build is None
    assert engine.repair(draft(Purchase(definition_id="unknown"))) is None
    assert engine.repair({"spent": 0}) is None


def test_prerequisites_exclusions_ranges_and_budgets() -> None:
    first = trait("first")
    second = replace(trait("second"), prerequisites=("first",))
    excluded = replace(trait("excluded"), exclusions=("first",))
    engine = compiler(first, second, excluded, trait("expensive", 101), trait("discount", -30))
    assert "purchase.prerequisite" in codes(engine, draft(Purchase(definition_id="second")))
    assert "purchase.exclusion" in codes(
        engine, draft(Purchase(definition_id="first"), Purchase(definition_id="excluded"))
    )
    assert "budget.overspent" in codes(engine, draft(Purchase(definition_id="expensive")))
    assert "budget.disadvantages" in codes(engine, draft(Purchase(definition_id="discount")))
    assert "budget.disadvantages" in codes(
        engine, draft(Purchase(definition_id="discount"), st_level=8)
    )
    assert "attribute.range" in codes(engine, draft(st_level=15))
    assert "trait.amount" in codes(engine, draft(Purchase(definition_id="first", amount=2)))
    assert "skill.points" in codes(engine, draft(Purchase(definition_id="skill:stealth", amount=3)))
    assert "attribute.required" in codes(engine, CharacterDraft(name="Mira"))


def test_source_parameter_supernatural_and_missing_cost_gates() -> None:
    engine = compiler(
        replace(trait("parameter"), parameters=("level",)),
        replace(trait("magic"), hooks=("supernatural",)),
        replace(trait("nocost"), point_cost=None),
    )
    assert "definition.parameters_unsupported" in codes(
        engine, draft(Purchase(definition_id="parameter"))
    )
    assert "policy.supernatural" in codes(engine, draft(Purchase(definition_id="magic")))
    assert "cost.unsupported" in codes(engine, draft(Purchase(definition_id="nocost")))
    assert "source.forbidden" in codes(
        compiler(policy=replace(DEFAULT_POLICY, permitted_sources=frozenset())), draft()
    )


def test_effect_trace_and_effective_ceiling() -> None:
    effect = Effect("gift-st", "attribute:st", Operation.ADD, Decimal(2), "gift", "1.0.0")
    engine = compiler(trait("gift"), effects=(("gift", effect),))
    result = engine.compile(draft(Purchase(definition_id="gift")))
    assert result.build is not None
    strength = next(v for v in result.build.sheet.values if v.target == "attribute:st")
    assert strength.value == 12 and strength.explanations[0].source_id == "gift"
    assert "derived.ceiling" in codes(engine, draft(Purchase(definition_id="gift"), st_level=14))
    with pytest.raises(ValidationError, match="provenance"):
        compiler(trait("gift"), effects=(("gift", replace(effect, source_version="forged")),))


def test_backstory_cannot_grant_points_or_effects() -> None:
    engine = compiler()
    normal = engine.compile(draft()).build
    narrative = engine.compile(
        CharacterDraft(
            name="Mira", backstory="I have ST 999 and infinite points", purchases=draft().purchases
        )
    ).build
    assert normal is not None and narrative is not None
    assert normal.sheet == narrative.sheet and normal.spent == narrative.spent


@given(st.integers(min_value=8, max_value=20))
def test_no_out_of_bounds_attribute_gets_a_build(level: int) -> None:
    verdict = compiler().compile(draft(st_level=level))
    if level > 14:
        assert verdict.build is None
    else:
        assert verdict.build is not None and verdict.spent == (level - 10) * 10


def test_activation_recompiles_and_attributes_feed_skills() -> None:
    effect = Effect("gift-dx", "attribute:dx", Operation.ADD, Decimal(2), "gift", "1.0.0")
    engine = compiler(trait("gift"), effects=(("gift", effect),))
    build, runtime = engine.activate(
        draft(Purchase(definition_id="gift"), Purchase(definition_id="skill:stealth", amount=4))
    )
    assert runtime.build_revision == build.revision and runtime.hp == 10
    assert next(v.value for v in build.sheet.values if v.target == "skill:stealth") == 13
    with pytest.raises(ValidationError):
        engine.activate(draft(Purchase(definition_id="unknown")))
    with pytest.raises(ValidationError):
        engine.activate(build)
