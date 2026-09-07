"""Declared GURPS capability coverage and fail-closed conformance helpers.

This module contains identifiers and coverage metadata only. It deliberately does
not reproduce rules text or implement a second mechanics engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from wayfarer.errors import ValidationError


class CoverageStatus(StrEnum):
    """Evidence state for one declared rules capability."""

    ABSENT = "absent"
    PARTIAL = "partial"
    MANUAL = "manual"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    lite_required: bool
    basic_required: bool
    status: CoverageStatus
    owner_issue: int | None


_CAPABILITIES: Final = (
    Capability("gurps.character.primary_attributes", True, True, CoverageStatus.PARTIAL, 97),
    Capability("gurps.character.secondary_characteristics", True, True, CoverageStatus.ABSENT, 97),
    Capability("gurps.character.skill_difficulty", True, True, CoverageStatus.PARTIAL, 98),
    Capability("gurps.character.skill_defaults", True, True, CoverageStatus.ABSENT, 98),
    Capability("gurps.character.specialties", False, True, CoverageStatus.ABSENT, 98),
    Capability("gurps.character.techniques", False, True, CoverageStatus.ABSENT, 98),
    Capability("gurps.character.traits", True, True, CoverageStatus.MANUAL, 100),
    Capability("gurps.character.self_control", True, True, CoverageStatus.ABSENT, 100),
    Capability("gurps.character.ability_modifiers", False, True, CoverageStatus.ABSENT, 100),
    Capability("gurps.check.success", True, True, CoverageStatus.PARTIAL, 99),
    Capability("gurps.check.margin", True, True, CoverageStatus.PARTIAL, 99),
    Capability("gurps.check.critical", True, True, CoverageStatus.PARTIAL, 99),
    Capability("gurps.check.quick_contest", True, True, CoverageStatus.ABSENT, 99),
    Capability("gurps.check.regular_contest", False, True, CoverageStatus.ABSENT, 99),
    Capability("gurps.check.resistance", True, True, CoverageStatus.ABSENT, 99),
    Capability("gurps.social.reaction", True, True, CoverageStatus.ABSENT, 111),
    Capability("gurps.social.influence", True, True, CoverageStatus.ABSENT, 111),
    Capability("gurps.social.fright", False, True, CoverageStatus.ABSENT, 111),
    Capability("gurps.equipment.weapon_profiles", True, True, CoverageStatus.PARTIAL, 101),
    Capability("gurps.equipment.armor_profiles", True, True, CoverageStatus.PARTIAL, 101),
    Capability("gurps.equipment.catalog", True, True, CoverageStatus.PARTIAL, 114),
    Capability("gurps.equipment.object_durability", False, True, CoverageStatus.ABSENT, 114),
    Capability("gurps.injury.damage_types", True, True, CoverageStatus.PARTIAL, 102),
    Capability("gurps.injury.damage_resistance", True, True, CoverageStatus.PARTIAL, 102),
    Capability("gurps.injury.hp_thresholds", True, True, CoverageStatus.PARTIAL, 102),
    Capability("gurps.injury.hit_locations", False, True, CoverageStatus.ABSENT, 107),
    Capability("gurps.injury.armor_divisors", False, True, CoverageStatus.ABSENT, 107),
    Capability("gurps.injury.lasting_wounds", False, True, CoverageStatus.ABSENT, 107),
    Capability("gurps.combat.melee_attack", True, True, CoverageStatus.PARTIAL, 103),
    Capability("gurps.combat.active_defense", True, True, CoverageStatus.PARTIAL, 103),
    Capability("gurps.combat.maneuvers", True, True, CoverageStatus.PARTIAL, 104),
    Capability("gurps.combat.turn_timing", True, True, CoverageStatus.PARTIAL, 104),
    Capability("gurps.combat.ranged_attack", True, True, CoverageStatus.PARTIAL, 106),
    Capability("gurps.combat.aim", True, True, CoverageStatus.ABSENT, 106),
    Capability("gurps.combat.ammunition", True, True, CoverageStatus.ABSENT, 106),
    Capability("gurps.combat.rapid_fire", False, True, CoverageStatus.ABSENT, 106),
    Capability("gurps.combat.unarmed", True, True, CoverageStatus.PARTIAL, 108),
    Capability("gurps.combat.grappling", True, True, CoverageStatus.ABSENT, 108),
    Capability("gurps.tactical.hex_movement", False, True, CoverageStatus.ABSENT, 105),
    Capability("gurps.tactical.facing", False, True, CoverageStatus.ABSENT, 105),
    Capability("gurps.tactical.visibility", False, True, CoverageStatus.ABSENT, 105),
    Capability("gurps.recovery.fatigue", True, True, CoverageStatus.PARTIAL, 109),
    Capability("gurps.recovery.healing", True, True, CoverageStatus.PARTIAL, 109),
    Capability("gurps.recovery.medical_treatment", False, True, CoverageStatus.ABSENT, 109),
    Capability("gurps.world.physical_feats", True, True, CoverageStatus.PARTIAL, 110),
    Capability("gurps.world.environmental_hazards", True, True, CoverageStatus.ABSENT, 110),
    Capability("gurps.magic.spellcasting", False, True, CoverageStatus.ABSENT, 117),
    Capability("gurps.supernatural.abilities", False, True, CoverageStatus.ABSENT, 118),
    Capability("gurps.vehicles.movement", False, True, CoverageStatus.ABSENT, 120),
    Capability("gurps.vehicles.combat", False, True, CoverageStatus.ABSENT, 120),
)

CAPABILITIES: Final = MappingProxyType({declared.id: declared for declared in _CAPABILITIES})


def capability(capability_id: str) -> Capability:
    """Resolve a declared capability or reject it.

    Scenario generation, character generation and LLM action proposals must use
    this helper (or an equivalent registry lookup) instead of assuming that an
    unknown mechanic exists.
    """

    result = CAPABILITIES.get(capability_id)
    if result is None:
        raise ValidationError(f"Unknown rules capability: {capability_id}")
    return result


def require_verified(capability_id: str) -> Capability:
    """Return a capability only when executable conformance evidence exists."""

    result = capability(capability_id)
    if result.status is not CoverageStatus.VERIFIED:
        raise ValidationError(
            f"Rules capability is not verified: {capability_id} ({result.status.value})"
        )
    return result


@dataclass(frozen=True, slots=True)
class RulesProfile:
    """A conformance target, not a selectable persisted campaign rules package."""

    id: str
    source_ids: tuple[str, ...]
    required_capabilities: frozenset[str]
    optional_capabilities: frozenset[str] = frozenset()


PROFILES: Final = MappingProxyType(
    {
        "gurps-lite-4e-2004": RulesProfile(
            "gurps-lite-4e-2004",
            ("sjg:gurps-lite-4e-2004",),
            frozenset(entry.id for entry in _CAPABILITIES if entry.lite_required),
        ),
        "gurps-basic-set-4e-2004": RulesProfile(
            "gurps-basic-set-4e-2004",
            ("sjg:basic-set-characters-4e-2004", "sjg:basic-set-campaigns-4e-2004"),
            frozenset(entry.id for entry in _CAPABILITIES if entry.basic_required),
        ),
    }
)


def profile(profile_id: str) -> RulesProfile:
    result = PROFILES.get(profile_id)
    if result is None:
        raise ValidationError(f"Unknown rules profile: {profile_id}")
    return result


def require_capabilities(profile_id: str, capability_ids: tuple[str, ...]) -> None:
    """Validate all requirements before a validator accepts a generated proposal.

    Empty requirements still validate the profile. No fuzzy matches, prototype
    fallback, optional-rule activation or campaign mutation occurs here.
    """
    selected = profile(profile_id)
    for identifier in capability_ids:
        capability(identifier)
        if identifier not in selected.required_capabilities:
            raise ValidationError(f"Rules capability outside profile: {identifier}")
        require_verified(identifier)
