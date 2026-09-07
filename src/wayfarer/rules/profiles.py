"""Versioned, selectable rules profiles with fail-closed capability support.

A registered profile binds exact package pins and a campaign policy to an optional
GURPS conformance target. Registration is metadata only: it never reproduces
rulebook text and never implements a second mechanics engine. The prototype pin
is registered unchanged; GURPS profiles remain unsupported until every required
capability has independent verified evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Final

from wayfarer.errors import ValidationError
from wayfarer.models import RulesReference
from wayfarer.rules import conformance, gurps_characters
from wayfarer.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    PROTOTYPE_PACKAGE,
    CampaignPolicy,
    CampaignRules,
    PackagePin,
    RulesCatalog,
    RulesPackage,
    SourceReference,
    reference,
)


@dataclass(frozen=True, slots=True)
class RegisteredProfile:
    """One exact, immutable rules selection. Updates require a new version."""

    id: str
    version: int
    title: str
    rules: CampaignRules
    policy: CampaignPolicy
    packages: tuple[RulesPackage, ...]
    conformance_profile_id: str | None = None
    required_capabilities: frozenset[str] = frozenset()
    optional_rules: tuple[str, ...] = ()

    @property
    def catalog(self) -> RulesCatalog:
        return RulesCatalog(self.packages)

    @property
    def reference(self) -> RulesReference:
        return reference(self.rules)

    @property
    def unverified_capabilities(self) -> tuple[str, ...]:
        return tuple(
            identifier
            for identifier in sorted(self.required_capabilities)
            if conformance.capability(identifier).status is not conformance.CoverageStatus.VERIFIED
        )

    @property
    def supported(self) -> bool:
        return not self.unverified_capabilities

    @property
    def digest(self) -> str:
        payload = {
            "id": self.id,
            "version": self.version,
            "rules": asdict(self.rules),
            "policy": {
                **asdict(self.policy),
                "permitted_sources": sorted(self.policy.permitted_sources),
                "allowed_equipment": sorted(self.policy.allowed_equipment),
            },
            "conformance_profile_id": self.conformance_profile_id,
            "required_capabilities": sorted(self.required_capabilities),
            "optional_rules": list(self.optional_rules),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def _validate(profile: RegisteredProfile) -> None:
    if profile.version < 1:
        raise ValidationError(f"Profile version must be positive: {profile.id}")
    if profile.optional_rules:
        raise ValidationError(f"Optional rules require a reviewed profile revision: {profile.id}")
    rules, policy = profile.rules, profile.policy
    if (rules.policy_id, rules.policy_version) != (policy.id, policy.version):
        raise ValidationError(f"Profile policy pin does not resolve: {profile.id}")
    if not rules.packages or len(set(rules.packages)) != len(rules.packages):
        raise ValidationError(f"Profile needs distinct package pins: {profile.id}")
    catalog = profile.catalog
    pinned = tuple(catalog.package(pin) for pin in rules.packages)
    if {(p.id, p.version) for p in pinned} != {(p.id, p.version) for p in profile.packages}:
        raise ValidationError(f"Profile packages and pins differ: {profile.id}")
    if any(package.edition != rules.edition for package in pinned):
        raise ValidationError(f"Profile package edition mismatch: {profile.id}")
    pinned_ids = {package.id for package in pinned}
    for package in pinned:
        missing = [dep for dep in package.dependencies if dep not in pinned_ids]
        if missing:
            raise ValidationError(
                f"Profile {profile.id} does not pin dependency {missing[0]} of {package.id}"
            )
        for source in package.sources:
            if source.id not in policy.permitted_sources:
                raise ValidationError(
                    f"Profile {profile.id} policy does not permit source {source.id}"
                )
    if profile.conformance_profile_id is None:
        if profile.required_capabilities:
            raise ValidationError(f"Capabilities require a conformance profile: {profile.id}")
        return
    target = conformance.profile(profile.conformance_profile_id)
    declared = target.required_capabilities | target.optional_capabilities
    for identifier in sorted(profile.required_capabilities):
        conformance.capability(identifier)
        if identifier not in declared:
            raise ValidationError(
                f"Capability outside conformance profile {target.id}: {identifier}"
            )
    if not target.required_capabilities <= profile.required_capabilities:
        raise ValidationError(f"Profile {profile.id} omits required {target.id} capabilities")


class ProfileRegistry:
    """Exact-match registry. No latest-version lookup and no fuzzy resolution."""

    def __init__(self, profiles: tuple[RegisteredProfile, ...]) -> None:
        self._profiles: dict[tuple[str, int], RegisteredProfile] = {}
        self._by_reference: dict[str, RegisteredProfile] = {}
        for profile in profiles:
            _validate(profile)
            key = (profile.id, profile.version)
            if key in self._profiles:
                raise ValidationError(f"Duplicate profile registration: {profile.id}")
            encoded = _encode(profile.reference)
            if encoded in self._by_reference:
                raise ValidationError(f"Profile pins are already registered: {profile.id}")
            self._profiles[key] = profile
            self._by_reference[encoded] = profile

    @property
    def profiles(self) -> tuple[RegisteredProfile, ...]:
        return tuple(self._profiles.values())

    def get(self, profile_id: str, version: int) -> RegisteredProfile:
        profile = self._profiles.get((profile_id, version))
        if profile is None:
            raise ValidationError(f"Unknown rules profile: {profile_id}@{version}")
        return profile

    def require_supported(self, profile_id: str, version: int) -> RegisteredProfile:
        profile = self.get(profile_id, version)
        unverified = profile.unverified_capabilities
        if unverified:
            raise ValidationError(
                f"Rules profile is not supported: {profile_id}@{version} "
                f"(unverified capabilities: {', '.join(unverified)})"
            )
        return profile

    def find(self, rules: RulesReference | None) -> RegisteredProfile | None:
        if rules is None:
            return None
        return self._by_reference.get(_encode(rules))

    def resolve(self, rules: RulesReference | None) -> RegisteredProfile:
        profile = self.find(rules)
        if profile is None:
            raise ValidationError("Campaign rules do not match a registered profile")
        return profile


def _encode(rules: RulesReference) -> str:
    return json.dumps(rules, sort_keys=True, separators=(",", ":"))


def _pin(package: RulesPackage) -> PackagePin:
    return PackagePin(id=package.id, version=package.version, digest=package.digest)


PROTOTYPE_PROFILE: Final = RegisteredProfile(
    id="profile:wayfarer-lite",
    version=1,
    title="Wayfarer prototype rules",
    rules=DEFAULT_RULES,
    policy=DEFAULT_POLICY,
    packages=(PROTOTYPE_PACKAGE,),
)

GURPS_EDITION: Final = "gurps-4e-2004"
GURPS_LITE_SOURCE: Final = SourceReference(
    id="sjg:gurps-lite-4e-2004",
    title="GURPS Lite, Fourth Edition",
    rights="user-supplied-reference",
    citation="Steve Jackson Games, August 2004 electronic edition, Rev. 07/12/04",
)
GURPS_CHARACTERS_SOURCE: Final = SourceReference(
    id="sjg:basic-set-characters-4e-2004",
    title="GURPS Basic Set: Characters",
    rights="user-supplied-reference",
    citation="Steve Jackson Games, 2004 Fourth Edition, first printing; errata 2007-01-26",
)
GURPS_CAMPAIGNS_SOURCE: Final = SourceReference(
    id="sjg:basic-set-campaigns-4e-2004",
    title="GURPS Basic Set: Campaigns",
    rights="user-supplied-reference",
    citation="Steve Jackson Games, 2004 Fourth Edition, first printing; errata 2007-01-26",
)

# Packages register identity, edition, provenance and dependencies. Definitions
# arrive with the mechanics issues that own them as new package versions: 0.2.0
# carries the #97 attributes and secondary characteristics (identifiers and
# costs only); later issues add skills, traits and equipment.
GURPS_LITE_PACKAGE: Final = RulesPackage(
    id="package:gurps-lite-4e-2004",
    version="0.2.0",
    edition=GURPS_EDITION,
    sources=(GURPS_LITE_SOURCE,),
    definitions=gurps_characters.definitions("gurps-lite-4e-2004"),
)
GURPS_CHARACTERS_PACKAGE: Final = RulesPackage(
    id="package:gurps-basic-set-characters-4e-2004",
    version="0.2.0",
    edition=GURPS_EDITION,
    sources=(GURPS_CHARACTERS_SOURCE,),
    definitions=gurps_characters.definitions("gurps-basic-set-4e-2004"),
)
GURPS_CAMPAIGNS_PACKAGE: Final = RulesPackage(
    id="package:gurps-basic-set-campaigns-4e-2004",
    version="0.2.0",
    edition=GURPS_EDITION,
    sources=(GURPS_CAMPAIGNS_SOURCE,),
    definitions=(),
    dependencies=(GURPS_CHARACTERS_PACKAGE.id,),
)

# Budgets and ceilings are campaign policy defaults, not published rules; attribute
# and secondary costs come from the package definitions and gurps_characters.
GURPS_LITE_POLICY: Final = CampaignPolicy(
    id="policy:gurps-lite-4e-2004",
    version=1,
    point_budget=DEFAULT_POLICY.point_budget,
    disadvantage_limit=DEFAULT_POLICY.disadvantage_limit,
    attribute_ceiling=DEFAULT_POLICY.attribute_ceiling,
    skill_ceiling=DEFAULT_POLICY.skill_ceiling,
    permitted_sources=frozenset({GURPS_LITE_SOURCE.id}),
)
GURPS_BASIC_POLICY: Final = CampaignPolicy(
    id="policy:gurps-basic-set-4e-2004",
    version=1,
    point_budget=DEFAULT_POLICY.point_budget,
    disadvantage_limit=DEFAULT_POLICY.disadvantage_limit,
    attribute_ceiling=DEFAULT_POLICY.attribute_ceiling,
    skill_ceiling=DEFAULT_POLICY.skill_ceiling,
    permitted_sources=frozenset({GURPS_CHARACTERS_SOURCE.id, GURPS_CAMPAIGNS_SOURCE.id}),
)

GURPS_LITE_PROFILE: Final = RegisteredProfile(
    id="profile:gurps-lite-4e-2004",
    version=2,
    title="GURPS Lite, Fourth Edition (2004)",
    rules=CampaignRules(
        edition=GURPS_EDITION,
        packages=(_pin(GURPS_LITE_PACKAGE),),
        policy_id=GURPS_LITE_POLICY.id,
        policy_version=GURPS_LITE_POLICY.version,
    ),
    policy=GURPS_LITE_POLICY,
    packages=(GURPS_LITE_PACKAGE,),
    conformance_profile_id="gurps-lite-4e-2004",
    required_capabilities=conformance.PROFILES["gurps-lite-4e-2004"].required_capabilities,
)
GURPS_BASIC_PROFILE: Final = RegisteredProfile(
    id="profile:gurps-basic-set-4e-2004",
    version=2,
    title="GURPS Basic Set, Fourth Edition (2004, first printing)",
    rules=CampaignRules(
        edition=GURPS_EDITION,
        packages=(_pin(GURPS_CHARACTERS_PACKAGE), _pin(GURPS_CAMPAIGNS_PACKAGE)),
        policy_id=GURPS_BASIC_POLICY.id,
        policy_version=GURPS_BASIC_POLICY.version,
    ),
    policy=GURPS_BASIC_POLICY,
    packages=(GURPS_CHARACTERS_PACKAGE, GURPS_CAMPAIGNS_PACKAGE),
    conformance_profile_id="gurps-basic-set-4e-2004",
    required_capabilities=conformance.PROFILES["gurps-basic-set-4e-2004"].required_capabilities,
)

DEFAULT_REGISTRY: Final = ProfileRegistry(
    (PROTOTYPE_PROFILE, GURPS_LITE_PROFILE, GURPS_BASIC_PROFILE)
)
GURPS_PROFILES: Final = MappingProxyType(
    {profile.id: profile for profile in (GURPS_LITE_PROFILE, GURPS_BASIC_PROFILE)}
)
