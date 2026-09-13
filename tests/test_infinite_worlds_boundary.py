"""The generic Basic Set profile explicitly excludes Infinite Worlds."""

from dataclasses import replace

import pytest

from wayfarer.engine.rules.profiles import (
    BASIC_SET_CONTENT_BOUNDARIES,
    BASIC_SET_CONTENT_BOUNDARY_SELECTIONS,
    DEFAULT_REGISTRY,
    GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE,
    GURPS_OPTIONAL_RULES_PROFILE,
    INFINITE_WORLDS_CONTENT_ID,
    ContentBoundarySelection,
    ProfileRegistry,
)
from wayfarer.errors import ValidationError


def test_latest_basic_profile_records_an_exact_excluded_boundary() -> None:
    profile = GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE

    assert profile.version == 10
    assert "Infinite Worlds excluded" in profile.title
    assert profile.rules == GURPS_OPTIONAL_RULES_PROFILE.rules
    assert profile.packages == GURPS_OPTIONAL_RULES_PROFILE.packages
    assert profile.named_optional_rules == GURPS_OPTIONAL_RULES_PROFILE.named_optional_rules
    assert profile.content_boundaries == BASIC_SET_CONTENT_BOUNDARY_SELECTIONS
    assert BASIC_SET_CONTENT_BOUNDARIES[INFINITE_WORLDS_CONTENT_ID].source_ref == "B523-B546"
    assert not BASIC_SET_CONTENT_BOUNDARIES[INFINITE_WORLDS_CONTENT_ID].available
    assert DEFAULT_REGISTRY.get(profile.id, profile.version) is profile


def test_excluded_included_unavailable_and_unknown_content_fail_closed() -> None:
    profile = GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE
    with pytest.raises(ValidationError, match="Content is excluded"):
        profile.require_content(INFINITE_WORLDS_CONTENT_ID)

    included = replace(
        profile,
        content_boundaries=(ContentBoundarySelection(INFINITE_WORLDS_CONTENT_ID, True),),
    )
    assert included.digest != profile.digest
    assert included.unavailable_content == (INFINITE_WORLDS_CONTENT_ID,)
    assert not included.supported
    with pytest.raises(ValidationError, match="Content is not implemented"):
        included.require_content(INFINITE_WORLDS_CONTENT_ID)
    with pytest.raises(ValidationError, match="unavailable content"):
        ProfileRegistry((included,)).require_supported(included.id, included.version)

    with pytest.raises(ValidationError, match="Unknown content boundary"):
        profile.require_content("gurps.content.invented")


def test_latest_basic_profile_requires_unique_complete_canonical_boundary() -> None:
    profile = GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE
    with pytest.raises(ValidationError, match="lacks exact content-boundary dispositions"):
        ProfileRegistry((replace(profile, content_boundaries=()),))
    duplicate = (*profile.content_boundaries, profile.content_boundaries[0])
    with pytest.raises(ValidationError, match="Invalid content boundaries"):
        ProfileRegistry((replace(profile, content_boundaries=duplicate),))
    invented = (ContentBoundarySelection("gurps.content.invented", False),)
    with pytest.raises(ValidationError, match="Invalid content boundaries"):
        ProfileRegistry((replace(profile, content_boundaries=invented),))
