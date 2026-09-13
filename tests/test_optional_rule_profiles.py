"""Selected-printing decisions for named Basic Set optional rules."""

from dataclasses import replace

import pytest

from wayfarer.engine.rules.profiles import (
    BASIC_SET_OPTIONAL_RULE_DEFINITIONS,
    BASIC_SET_OPTIONAL_RULE_SELECTIONS,
    DEFAULT_REGISTRY,
    GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE,
    GURPS_OPTIONAL_RULES_PROFILE,
    OptionalRuleSelection,
    ProfileRegistry,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.profiles import view

EXPECTED = {
    "gurps.optional.limited-enhancements": "B111",
    "gurps.optional.wildcard-skills": "B175",
    "gurps.optional.modifying-dice-adds": "B269",
    "gurps.optional.malfunction": "B279",
    "gurps.optional.maintaining-skills": "B294",
    "gurps.optional.influencing-success-rolls": "B347",
    "gurps.optional.detailed-jumping": "B352",
    "gurps.optional.posture-in-armor": "B395",
    "gurps.optional.injury.bleeding": "B420",
    "gurps.optional.injury.accumulated-wounds": "B420",
    "gurps.optional.injury.last-wounds": "B420",
}


def test_exact_profile_records_every_named_rule_disabled() -> None:
    profile = GURPS_OPTIONAL_RULES_PROFILE
    assert profile.version == 9
    assert {
        definition.id: definition.source_ref for definition in BASIC_SET_OPTIONAL_RULE_DEFINITIONS
    } == EXPECTED
    assert profile.named_optional_rules == BASIC_SET_OPTIONAL_RULE_SELECTIONS
    assert not any(selection.enabled for selection in profile.named_optional_rules)
    assert (
        DEFAULT_REGISTRY.get(
            GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE.id,
            GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE.version,
        ).named_optional_rules
        == profile.named_optional_rules
    )
    assert {
        selection.id: (selection.enabled, selection.source_ref, selection.available)
        for selection in view(profile).named_optional_rules
    } == {identifier: (False, source, False) for identifier, source in EXPECTED.items()}


@pytest.mark.parametrize("identifier", tuple(EXPECTED))
def test_disabled_and_enabled_unavailable_paths_both_fail_closed(identifier: str) -> None:
    profile = GURPS_OPTIONAL_RULES_PROFILE
    with pytest.raises(ValidationError, match="is disabled"):
        profile.require_optional_rule(identifier)

    selections = tuple(
        replace(selection, enabled=selection.id == identifier)
        for selection in profile.named_optional_rules
    )
    enabled = replace(profile, named_optional_rules=selections)
    assert enabled.digest != profile.digest
    assert enabled.unavailable_optional_rules == (identifier,)
    assert not enabled.supported
    with pytest.raises(ValidationError, match="is not implemented"):
        enabled.require_optional_rule(identifier)
    with pytest.raises(ValidationError, match="unavailable optional rules"):
        ProfileRegistry((enabled,)).require_supported(enabled.id, enabled.version)


def test_latest_basic_profile_requires_unique_complete_canonical_decisions() -> None:
    profile = GURPS_OPTIONAL_RULES_PROFILE
    with pytest.raises(ValidationError, match="lacks exact optional-rule dispositions"):
        ProfileRegistry((replace(profile, named_optional_rules=profile.named_optional_rules[:-1]),))
    duplicate = (*profile.named_optional_rules[:-1], profile.named_optional_rules[0])
    with pytest.raises(ValidationError, match="Invalid named optional rules"):
        ProfileRegistry((replace(profile, named_optional_rules=duplicate),))
    invented = (
        *profile.named_optional_rules[:-1],
        OptionalRuleSelection("gurps.optional.invented", False),
    )
    with pytest.raises(ValidationError, match="Invalid named optional rules"):
        ProfileRegistry((replace(profile, named_optional_rules=invented),))


def test_unknown_runtime_lookup_rejects_without_default_enablement() -> None:
    with pytest.raises(ValidationError, match="Unknown named optional rule"):
        GURPS_OPTIONAL_RULES_PROFILE.require_optional_rule("gurps.optional.invented")
