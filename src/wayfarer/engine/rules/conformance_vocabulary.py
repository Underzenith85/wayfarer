"""Shared identifiers and status vocabulary for conformance declarations and audits.

This leaf module deliberately owns no capability registry or executable gate.  Audit
packages can describe their evidence without importing the higher-level conformance
service that consumes that evidence.
"""

from enum import StrEnum
from typing import Final

from wayfarer.errors import ValidationError

BASELINE_ID: Final = "gurps-4e-characters-3p-2008+campaigns-4p-2008"
"""Selected source baseline recorded in tests/fixtures/gurps/conformance.json."""

LITE_PROFILE_ID: Final = "gurps-lite-4e-2004"
BASIC_PROFILE_ID: Final = "gurps-basic-set-4e-2004"
PROFILE_IDS: Final = frozenset((LITE_PROFILE_ID, BASIC_PROFILE_ID))


class CoverageStatus(StrEnum):
    """Evidence state for one declared rules capability."""

    ABSENT = "absent"
    PARTIAL = "partial"
    MANUAL = "manual"
    VERIFIED = "verified"


def require_profile_id(profile_id: str) -> str:
    """Return a known conformance profile identifier or reject it."""
    if profile_id not in PROFILE_IDS:
        raise ValidationError(f"Unknown rules profile: {profile_id}")
    return profile_id
