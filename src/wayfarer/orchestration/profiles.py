"""Profile dispatch and explicit, ledger-backed profile migration.

Every campaign stays bound to the exact profile matching its saved rules pins.
Selecting another profile is an explicit host-approved migration that reuses the
existing rules-migration ledger, command receipts and revision checks; nothing
here rewrites a saved campaign implicitly.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from pydantic import ValidationError as SchemaError

from wayfarer.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from wayfarer.models import Campaign
from wayfarer.orchestration.advancement import (
    AdvancementService,
    ApplyMigration,
    MigrationService,
    _diff,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.catalog import reference
from wayfarer.rules.checks import RandomSource
from wayfarer.rules.profiles import ProfileRegistry, RegisteredProfile
from wayfarer.simulation.action_engine import ActionEngine
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.advancement import BuildDiff, MigrationEntry
from wayfarer.simulation.profiles import (
    Incompatibility,
    MigrateProfile,
    PackageView,
    ProfileMigrationPreview,
    ProfileSelection,
    ProfileView,
)
from wayfarer.simulation.setup import Setup

EngineBuilder = Callable[[RegisteredProfile], ActionEngine]


def selection(profile: RegisteredProfile) -> ProfileSelection:
    return ProfileSelection(id=profile.id, version=profile.version)


def identity(profile: RegisteredProfile) -> str:
    return f"{profile.id}@{profile.version}"


def view(profile: RegisteredProfile) -> ProfileView:
    packages = {(p.id, p.version): p for p in profile.packages}
    return ProfileView(
        id=profile.id,
        version=profile.version,
        title=profile.title,
        edition=profile.rules.edition,
        digest=profile.digest,
        supported=profile.supported,
        conformance_profile_id=profile.conformance_profile_id,
        packages=tuple(
            PackageView(
                id=pin.id,
                version=pin.version,
                digest=pin.digest,
                dependencies=packages[(pin.id, pin.version)].dependencies,
                source_ids=tuple(s.id for s in packages[(pin.id, pin.version)].sources),
            )
            for pin in profile.rules.packages
        ),
        policy_id=profile.policy.id,
        policy_version=profile.policy.version,
        required_capabilities=tuple(sorted(profile.required_capabilities)),
        unverified_capabilities=profile.unverified_capabilities,
        optional_rules=profile.optional_rules,
    )


class ProfileRuntime:
    """Builds and caches one play service per supported registered profile."""

    def __init__(
        self,
        registry: ProfileRegistry,
        store: AsyncSQLiteStore | AsyncPostgresStore,
        build: EngineBuilder,
        default: RegisteredProfile,
        *,
        rng: RandomSource = secrets,
    ) -> None:
        self.registry, self.store, self.build, self.rng = registry, store, build, rng
        self._services: dict[tuple[str, int], PlayService] = {}
        self.default = registry.get(default.id, default.version)
        self.play = self.service(self.default)

    def service(self, profile: RegisteredProfile) -> PlayService:
        key = (profile.id, profile.version)
        existing = self._services.get(key)
        if existing is not None:
            return existing
        registered = self.registry.require_supported(profile.id, profile.version)
        engine = self.build(registered)
        if reference(engine.resources.rules) != registered.reference:
            raise ValidationError(f"Runtime rules do not match profile {identity(registered)}")
        service = PlayService(self.store, engine, rng=self.rng, profiles=self)
        self._services[key] = service
        return service

    def select(self, chosen: ProfileSelection | None) -> PlayService:
        if chosen is None:
            return self.play
        return self.service(self.registry.get(chosen.id, chosen.version))

    def profile_of(self, campaign: Campaign) -> RegisteredProfile | None:
        return self.registry.find(campaign.get("rules_ref"))

    def for_campaign(self, campaign: Campaign) -> PlayService:
        profile = self.registry.resolve(campaign.get("rules_ref"))
        return self.service(profile).bind(campaign)

    def views(self) -> tuple[ProfileView, ...]:
        return tuple(view(profile) for profile in self.registry.profiles)


class ProfileMigrations:
    """Preview incompatibilities, then apply an atomic, idempotent profile switch."""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime

    @staticmethod
    def _setup(campaign: Campaign) -> Setup:
        if "setup_json" not in campaign:
            raise ValidationError("Profile migration requires a hosted game setup")
        return Setup.model_validate_json(campaign["setup_json"])

    def _authorize(self, campaign: Campaign, principal_id: str) -> Setup:
        setup = self._setup(campaign)
        if not any(seat.principal_id == principal_id for seat in setup.seats):
            # Avoid disclosing whether an inaccessible campaign exists.
            raise NotFoundError("Setup not found")
        if setup.host_id != principal_id:
            raise AuthorizationError("Only the host can migrate campaign rules")
        return setup

    def _incompatibilities(
        self,
        campaign: Campaign,
        state: PlayState,
        current: PlayService,
        target: RegisteredProfile,
        principal_id: str,
    ) -> tuple[PlayService | None, tuple[BuildDiff, ...], tuple[Incompatibility, ...]]:
        found: list[Incompatibility] = []
        diffs: list[BuildDiff] = []
        service = self.runtime.service(target)
        gm = principal_id in service.engine.reviewer.gm_ids
        bound: PlayService | None
        try:
            bound = service.bind(campaign, migration_target=True)
        except ValidationError as exc:
            bound = None
            found.append(
                Incompatibility(
                    kind="scenario",
                    reference=campaign["id"],
                    message=f"Scenario rules do not bind to the target profile: {exc}",
                )
            )
        reviewer = (bound or service).engine.reviewer
        for actor in state.actors:
            before = current.engine.reviewer.review(actor.proposal).compilation.build
            review = reviewer.review(actor.proposal)
            after = review.compilation.build
            if before is None or after is None or review.status in ("illegal", "blocked"):
                reasons = [d.message for d in review.compilation.diagnostics] + [
                    f.message for f in review.findings
                ]
                found.append(
                    Incompatibility(
                        kind="character",
                        reference=actor.actor_id,
                        message="; ".join(reasons)
                        or "Character is illegal under the target profile",
                    )
                )
                continue
            if review.status != "automatic" and not gm:
                found.append(
                    Incompatibility(
                        kind="character",
                        reference=actor.actor_id,
                        message="Character needs GM power approval under the target profile",
                    )
                )
                continue
            diffs.append(_diff(actor.actor_id, before, after))
        specs = (bound or service).engine.resources.specs
        for item in state.resources.items:
            if item.definition_id not in specs:
                found.append(
                    Incompatibility(
                        kind="resource",
                        reference=item.id,
                        message=(
                            f"Equipment {item.definition_id} is not implemented "
                            "in the target profile"
                        ),
                    )
                )
        document = campaign.get("scenario_document_json")
        if document is not None:
            from wayfarer.orchestration.scenario_documents import parse_document

            pinned = parse_document(document)
            if pinned.compatibility.rules != target.rules:
                found.append(
                    Incompatibility(
                        kind="scenario",
                        reference=pinned.scenario_id,
                        message=(
                            "Pinned scenario document was published for other rules pins; "
                            "publish a revision for the target profile and create a new game"
                        ),
                    )
                )
        return bound, tuple(diffs), tuple(found)

    async def preview(
        self, cid: str, chosen: ProfileSelection, *, principal_id: str
    ) -> ProfileMigrationPreview:
        campaign = await self.runtime.store.read(cid)
        self._authorize(campaign, principal_id)
        return self._preview(campaign, chosen, principal_id)

    def _preview(
        self, campaign: Campaign, chosen: ProfileSelection, principal_id: str
    ) -> ProfileMigrationPreview:
        source = self.runtime.registry.resolve(campaign.get("rules_ref"))
        target = self.runtime.registry.require_supported(chosen.id, chosen.version)
        if (source.id, source.version) == (target.id, target.version):
            raise ValidationError("Campaign already uses the selected profile")
        setup = self._setup(campaign)
        if setup.phase not in ("paused", "completed"):
            raise ConflictError("Pause or complete the game before migrating its rules")
        current = self.runtime.for_campaign(campaign)
        state = current._load(campaign)
        bound, diffs, found = self._incompatibilities(
            campaign, state, current, target, principal_id
        )
        return ProfileMigrationPreview(
            campaign_id=campaign["id"],
            revision=state.revision,
            from_profile=selection(source),
            to_profile=selection(target),
            from_digest=current.engine.digest,
            to_digest=bound.engine.digest if bound is not None else None,
            actor_diffs=diffs,
            incompatibilities=found,
        )

    async def apply(self, cid: str, value: object, *, principal_id: str) -> MigrationEntry:
        try:
            command = MigrateProfile.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid profile migration") from exc
        approval = ApplyMigration(
            id=command.id,
            actor_id=principal_id,
            expected_revision=command.expected_revision,
            expected_from_digest=command.expected_from_digest,
            reason=command.reason,
        )
        payload = AdvancementService._payload(
            "rules-migration",
            {**approval.model_dump(mode="json"), "profile": command.profile.model_dump()},
        )
        campaign = await self.runtime.store.read(cid)
        self._authorize(campaign, principal_id)
        # A completed migration changed the campaign pins, so the receipt must be
        # consulted before the preview would reject "already uses this profile".
        duplicate = await self.runtime.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            state = PlayState.model_validate_json(duplicate["play_json"])
            entry = next((m for m in state.migrations if m.id == command.id), None)
            if entry is None:
                raise ConflictError("Command ID belongs to another operation")
            return entry
        preview = self._preview(campaign, command.profile, principal_id)
        if not preview.applicable:
            raise ValidationError(
                "Migration blocked: "
                + "; ".join(
                    f"{i.kind} {i.reference}: {i.message}" for i in preview.incompatibilities
                )
            )
        if command.expected_from_digest != preview.from_digest:
            raise ConflictError("Migration source configuration changed")
        source = self.runtime.registry.resolve(campaign.get("rules_ref"))
        target = self.runtime.registry.get(command.profile.id, command.profile.version)
        current = self.runtime.for_campaign(campaign)
        bound = self.runtime.service(target).bind(campaign, migration_target=True)
        migration = MigrationService(
            current,
            bound,
            authority=frozenset({principal_id}),
            from_profile=identity(source),
            to_profile=identity(target),
        )
        return await migration.apply(
            cid, approval, authenticated_gm_id=principal_id, payload=payload
        )
