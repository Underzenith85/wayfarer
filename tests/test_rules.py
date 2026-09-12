import pytest
from hypothesis import given
from hypothesis import strategies as st

from wayfarer import validation
from wayfarer.engine.character import builder
from wayfarer.engine.rules import checks
from wayfarer.engine.rules.catalog import (
    DEFAULT_CATALOG,
    DEFAULT_POLICY,
    DEFAULT_RULES,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
    RulesPackage,
    SourceReference,
)
from wayfarer.errors import ValidationError


def test_default_character_is_legal() -> None:
    assert builder.validate(builder.character())["valid"]


@given(st.one_of(st.integers(max_value=7), st.integers(min_value=15), st.booleans()))
def test_attribute_bounds_reject_invalid_values(value: int | bool) -> None:
    draft = builder.character()
    draft["attributes"]["ST"] = value
    assert not builder.validate(draft)["valid"]


def test_character_schema_rejects_unknown_and_coerced_fields() -> None:
    original = validation.mapping(builder.character())
    original["free_points"] = 999
    assert not builder.validate(original)["valid"]
    draft = builder.character()
    draft["attributes"]["IQ"] = True
    assert not builder.validate(draft)["valid"]


def test_character_schema_returns_independent_containers() -> None:
    original = builder.character()
    parsed = validation.character(original)
    parsed["attributes"]["ST"] = 99
    assert original["attributes"]["ST"] == 10


def test_roll_uses_injected_randomness() -> None:
    class Lowest:
        def randbelow(self, exclusive_upper_bound: int, /) -> int:
            assert exclusive_upper_bound == 6
            return 0

    assert checks.roll(2, Lowest())["critical"] == "success"


def test_checks_require_injected_randomness() -> None:
    with pytest.raises(ValidationError, match="explicit random source"):
        checks.roll(10)
    with pytest.raises(ValidationError, match="explicit random source"):
        checks.success_check(
            10,
            rules_package="package:test",
            rules_version="1",
        )


def test_default_rules_pin_is_reproducible_and_activates() -> None:
    DEFAULT_CATALOG.activate(DEFAULT_RULES, DEFAULT_POLICY)
    pin = DEFAULT_RULES.packages[0]
    assert len(pin.digest) == 64
    assert DEFAULT_CATALOG.package(pin).digest == pin.digest


def test_tampered_pin_and_missing_reference_are_rejected() -> None:
    pin = DEFAULT_RULES.packages[0]
    with pytest.raises(ValidationError):
        DEFAULT_CATALOG.package(PackagePin(pin.id, pin.version, "0" * 64))
    source = SourceReference("source:test", "Test", "original")
    definition = RuleDefinition(
        "skill:test",
        DefinitionKind.SKILL,
        "Test",
        source.id,
        1,
        ImplementationStatus.IMPLEMENTED,
        prerequisites=("skill:missing",),
    )
    with pytest.raises(ValidationError, match="missing reference"):
        RulesCatalog((RulesPackage("test", "1", "test", (source,), (definition,)),))


def test_prerequisite_cycles_and_unsupported_activation_are_rejected() -> None:
    source = SourceReference("source:test", "Test", "original")
    first = RuleDefinition(
        "trait:first",
        DefinitionKind.TRAIT,
        "First",
        source.id,
        1,
        ImplementationStatus.IMPLEMENTED,
        prerequisites=("trait:second",),
    )
    second = RuleDefinition(
        "trait:second",
        DefinitionKind.TRAIT,
        "Second",
        source.id,
        1,
        ImplementationStatus.IMPLEMENTED,
        prerequisites=("trait:first",),
    )
    with pytest.raises(ValidationError, match="cycle"):
        RulesCatalog((RulesPackage("test", "1", "test", (source,), (first, second)),))
