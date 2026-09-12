"""Package selection and profile migration contracts, additive to frozen play v1."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from wayfarer.engine.simulation.campaign.advancement import BuildDiff
from wayfarer.models import Id, Record


class ProfileSelection(Record):
    """An exact registered profile. There is no latest-version selection."""

    id: Id
    version: int = Field(ge=1)


class PackageView(Record):
    id: Id
    version: str = Field(min_length=1, max_length=100)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependencies: tuple[Id, ...] = ()
    source_ids: tuple[Id, ...] = ()


class ProfileView(Record):
    id: Id
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    edition: str = Field(min_length=1, max_length=100)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    supported: bool
    conformance_profile_id: str | None = None
    packages: tuple[PackageView, ...] = Field(min_length=1)
    policy_id: Id
    policy_version: int = Field(ge=1)
    required_capabilities: tuple[str, ...] = ()
    unverified_capabilities: tuple[str, ...] = ()
    optional_rules: tuple[str, ...] = ()


IncompatibilityKind = Literal["character", "resource", "scenario"]


class Incompatibility(Record):
    kind: IncompatibilityKind
    reference: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)


class ProfileMigrationPreview(Record):
    campaign_id: Id
    revision: int = Field(ge=0)
    from_profile: ProfileSelection
    to_profile: ProfileSelection
    from_digest: str
    to_digest: str | None = None
    actor_diffs: tuple[BuildDiff, ...] = ()
    incompatibilities: tuple[Incompatibility, ...] = ()

    @property
    def applicable(self) -> bool:
        return not self.incompatibilities and self.to_digest is not None


class MigrateProfile(Record):
    """Explicit host approval. Retries with the same ID replay the original receipt."""

    id: Id
    expected_revision: int = Field(ge=0)
    profile: ProfileSelection
    expected_from_digest: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
