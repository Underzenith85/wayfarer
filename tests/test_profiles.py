"""Exact rules-profile selection, dispatch and explicit, atomic profile migration."""

import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from wayfarer.character.compiler import CharacterCompiler, Purchase
from wayfarer.character.power import PowerPolicy, PowerReviewer
from wayfarer.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.profiles import ProfileMigrations, ProfileRuntime
from wayfarer.orchestration.setup import SetupService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules import conformance, gurps_characters, gurps_skills
from wayfarer.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    PROTOTYPE_PACKAGE,
    PROTOTYPE_SOURCE,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesPackage,
    SourceReference,
    reference,
)
from wayfarer.rules.profiles import (
    DEFAULT_REGISTRY,
    GURPS_BASIC_PROFILE,
    GURPS_LITE_PROFILE,
    PROTOTYPE_PROFILE,
    ProfileRegistry,
    RegisteredProfile,
)
from wayfarer.runtime import starting_scenario
from wayfarer.simulation.actions import ActionEngine, ActionRules, CheckRule, PlayState
from wayfarer.simulation.profiles import MigrateProfile, ProfileSelection
from wayfarer.simulation.resources import EquipmentSpec, Item, ResourceEngine
from wayfarer.simulation.setup import CreateSetup, SetupCommand
from wayfarer.simulation.studio import ScenarioGraph
from wayfarer.transport.campaign_api import create_campaign_app
from wayfarer.transport.setup_api import SETUP_KEY

FIXTURE = Path("tests/fixtures/gurps/conformance.json")

EXTRA_SOURCE = SourceReference("source:test-extra", "Test extension", "original")
EXTRA_PACKAGE = RulesPackage(
    id="package:test-extra",
    version="1.0.0",
    edition=PROTOTYPE_PACKAGE.edition,
    sources=(EXTRA_SOURCE,),
    definitions=(
        RuleDefinition(
            "trait:lantern-bearer",
            DefinitionKind.TRAIT,
            "Lantern bearer",
            EXTRA_SOURCE.id,
            5,
            ImplementationStatus.IMPLEMENTED,
        ),
        RuleDefinition(
            "equipment:test-lantern",
            DefinitionKind.EQUIPMENT,
            "Test lantern",
            EXTRA_SOURCE.id,
            0,
            ImplementationStatus.IMPLEMENTED,
        ),
        RuleDefinition(
            "skill:test-stealth",
            DefinitionKind.SKILL,
            "Stealth",
            EXTRA_SOURCE.id,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.skill", "check.target"),
        ),
    ),
    dependencies=(PROTOTYPE_PACKAGE.id,),
)
EXTENDED_POLICY = replace(
    DEFAULT_POLICY,
    id="policy:test-extended",
    permitted_sources=frozenset({PROTOTYPE_SOURCE.id, EXTRA_SOURCE.id}),
    allowed_equipment=frozenset({"equipment:test-lantern"}),
)


def pin(package: RulesPackage) -> PackagePin:
    return PackagePin(package.id, package.version, package.digest)


EXTENDED_PROFILE = RegisteredProfile(
    id="profile:test-extended",
    version=1,
    title="Prototype plus test extension",
    rules=CampaignRules(
        edition=PROTOTYPE_PACKAGE.edition,
        packages=(pin(PROTOTYPE_PACKAGE), pin(EXTRA_PACKAGE)),
        policy_id=EXTENDED_POLICY.id,
        policy_version=EXTENDED_POLICY.version,
    ),
    policy=EXTENDED_POLICY,
    packages=(PROTOTYPE_PACKAGE, EXTRA_PACKAGE),
)
REGISTRY = ProfileRegistry((PROTOTYPE_PROFILE, EXTENDED_PROFILE, GURPS_LITE_PROFILE))
EXTENDED = ProfileSelection(id=EXTENDED_PROFILE.id, version=1)
PROTOTYPE = ProfileSelection(id=PROTOTYPE_PROFILE.id, version=1)
GURPS_LITE = ProfileSelection(id=GURPS_LITE_PROFILE.id, version=GURPS_LITE_PROFILE.version)
TOKENS = {"alice-token": "alice", "bob-token": "bob"}
ALICE = {"Authorization": "Bearer alice-token"}


def build(profile: RegisteredProfile) -> ActionEngine:
    catalog = profile.catalog
    specs = (
        (EquipmentSpec(definition_id="equipment:test-lantern", unit_weight=1),)
        if "equipment:test-lantern" in profile.policy.allowed_equipment
        else ()
    )
    return ActionEngine(
        PowerReviewer(
            CharacterCompiler(catalog, profile.rules, profile.policy),
            PowerPolicy(id="test-power", version=1),
            frozenset(),
        ),
        ResourceEngine(starting_scenario().world, catalog, profile.rules, profile.policy, specs),
        ActionRules(id="test-actions", version=1),
    )


def runtime(tmp_path: Path) -> ProfileRuntime:
    store = AsyncSQLiteStore(tmp_path / "profiles.sqlite", 10)
    return ProfileRuntime(REGISTRY, store, build, PROTOTYPE_PROFILE)


def extended_graph(
    *, item: bool = False, trait: bool = False, check: bool = False
) -> ScenarioGraph:
    graph = starting_scenario()
    mira = graph.actors[0]
    if trait:
        mira = mira.model_copy(
            update={
                "proposal": mira.proposal.model_copy(
                    update={
                        "draft": mira.proposal.draft.model_copy(
                            update={
                                "purchases": mira.proposal.draft.purchases
                                + (Purchase(definition_id="trait:lantern-bearer", amount=1),)
                            }
                        )
                    }
                )
            }
        )
    resources = graph.resources
    if item:
        resources = resources.model_copy(
            update={
                "items": (
                    Item(id="lantern-1", definition_id="equipment:test-lantern", owner_id="mira"),
                )
            }
        )
    actions = graph.actions
    if check:
        actions = actions.model_copy(
            update={
                "checks": (
                    CheckRule(
                        id="sneak-past",
                        action="inspect",
                        target_id="beacon",
                        definition_id="skill:test-stealth",
                        package_id=EXTRA_PACKAGE.id,
                        package_version=EXTRA_PACKAGE.version,
                    ),
                )
            }
        )
    return graph.model_copy(
        update={
            "id": "beacon-extended",
            "actors": (mira,),
            "resources": resources,
            "actions": actions,
        }
    )


async def activated(
    setup: SetupService, graph: ScenarioGraph, chosen: ProfileSelection | None
) -> str:
    created = await setup.create(
        CreateSetup(id=str(uuid.uuid4()), brief=graph.brief, graph=graph, rules_profile=chosen),
        principal_id="alice",
    )
    cid = str(created["id"])
    steps = (
        SetupCommand(
            id="assign",
            expected_revision=0,
            operation="assign",
            principal_id="alice",
            actor_ids=("mira",),
        ),
        SetupCommand(id="ready", expected_revision=1, operation="ready"),
        SetupCommand(id="activate", expected_revision=2, operation="activate"),
        SetupCommand(id="pause", expected_revision=3, operation="pause"),
    )
    for command in steps:
        await setup.execute(cid, command, principal_id="alice")
    return cid


def migration(
    cid: str, digest: str, *, chosen: ProfileSelection = EXTENDED, command_id: str = "m"
) -> MigrateProfile:
    return MigrateProfile(
        id=command_id,
        expected_revision=4,
        profile=chosen,
        expected_from_digest=digest,
        reason="Approved profile change",
    )


def test_default_registry_preserves_prototype_pins_and_rejects_gurps_until_verified() -> None:
    assert [p.id for p in DEFAULT_REGISTRY.profiles] == [
        "profile:wayfarer-lite",
        "profile:gurps-lite-4e-2004",
        "profile:gurps-basic-set-4e-2004",
        "profile:gurps-lite-4e-2004",
        "profile:gurps-basic-set-4e-2004",
        "profile:gurps-basic-set-4e-2004",
    ]
    assert PROTOTYPE_PROFILE.rules == DEFAULT_RULES
    assert PROTOTYPE_PROFILE.reference == reference(DEFAULT_RULES)
    assert PROTOTYPE_PROFILE.rules.packages[0].id == "package:wayfarer-lite"
    assert PROTOTYPE_PROFILE.supported and not PROTOTYPE_PROFILE.conformance_profile_id
    assert DEFAULT_REGISTRY.resolve(reference(DEFAULT_RULES)) is PROTOTYPE_PROFILE
    for profile in (GURPS_LITE_PROFILE, GURPS_BASIC_PROFILE):
        assert profile.conformance_profile_id is not None
        target = conformance.profile(profile.conformance_profile_id)
        assert profile.required_capabilities == target.required_capabilities
        assert set(profile.unverified_capabilities) == {
            identifier
            for identifier in target.required_capabilities
            if conformance.CAPABILITIES[identifier].status
            is not conformance.CoverageStatus.VERIFIED
        }
        assert not profile.supported
        assert profile.rules.edition == "gurps-4e-2004"
        # Version 3 adds pinned #98 skill metadata; version 2 remains registered.
        carried = {d.id for p in profile.packages for d in p.definitions}
        assert carried == {
            d.id
            for d in (
                gurps_characters.definitions(profile.conformance_profile_id)
                + gurps_skills.definitions(profile.conformance_profile_id)
            )
        }
        assert profile.version == 3
        with pytest.raises(ValidationError, match="not supported"):
            DEFAULT_REGISTRY.require_supported(profile.id, profile.version)
    assert GURPS_BASIC_PROFILE.packages[1].dependencies == (GURPS_BASIC_PROFILE.packages[0].id,)
    assert "gurps.tactical.hex_movement" not in GURPS_LITE_PROFILE.required_capabilities
    assert "gurps.tactical.hex_movement" in GURPS_BASIC_PROFILE.required_capabilities


@pytest.mark.parametrize(
    ("profile_id", "version"),
    [
        ("profile:wayfarer-lite", 2),
        ("profile:gurps-lite-4e-2004", 0),
        ("gurps-lite-4e-2004", 1),
        ("PROFILE:WAYFARER-LITE", 1),
        ("package:wayfarer-lite", 1),
    ],
)
def test_exact_profile_selection_only(profile_id: str, version: int) -> None:
    with pytest.raises(ValidationError, match="Unknown rules profile"):
        DEFAULT_REGISTRY.get(profile_id, version)


def test_resolution_requires_exact_pins() -> None:
    tampered = reference(DEFAULT_RULES)
    tampered["packages"][0]["digest"] = "0" * 64
    assert DEFAULT_REGISTRY.find(tampered) is None
    assert DEFAULT_REGISTRY.find(None) is None
    with pytest.raises(ValidationError, match="registered profile"):
        DEFAULT_REGISTRY.resolve(tampered)
    other = reference(DEFAULT_RULES)
    other["policy_version"] = 2
    with pytest.raises(ValidationError, match="registered profile"):
        DEFAULT_REGISTRY.resolve(other)


def test_registration_validates_dependencies_capabilities_and_options() -> None:
    lite = GURPS_LITE_PROFILE
    with pytest.raises(ValidationError, match="missing dependency"):
        ProfileRegistry(
            (
                replace(
                    GURPS_BASIC_PROFILE,
                    rules=replace(
                        GURPS_BASIC_PROFILE.rules, packages=GURPS_BASIC_PROFILE.rules.packages[1:]
                    ),
                    packages=GURPS_BASIC_PROFILE.packages[1:],
                ),
            )
        )
    with pytest.raises(ValidationError, match="edition mismatch"):
        ProfileRegistry((replace(lite, rules=replace(lite.rules, edition="wayfarer-lite")),))
    with pytest.raises(ValidationError, match="policy pin"):
        ProfileRegistry((replace(lite, rules=replace(lite.rules, policy_version=2)),))
    with pytest.raises(ValidationError, match="Optional rules"):
        ProfileRegistry((replace(lite, optional_rules=("bleeding",)),))
    with pytest.raises(ValidationError, match="Unknown rules profile"):
        ProfileRegistry((replace(lite, conformance_profile_id="gurps-5e"),))
    with pytest.raises(ValidationError, match="outside conformance profile"):
        ProfileRegistry(
            (
                replace(
                    lite,
                    required_capabilities=lite.required_capabilities
                    | {"gurps.tactical.hex_movement"},
                ),
            )
        )
    with pytest.raises(ValidationError, match="Unknown rules capability"):
        ProfileRegistry(
            (replace(lite, required_capabilities=lite.required_capabilities | {"gurps.invented"}),)
        )
    with pytest.raises(ValidationError, match="omits required"):
        ProfileRegistry((replace(lite, required_capabilities=frozenset()),))
    with pytest.raises(ValidationError, match="require a conformance profile"):
        ProfileRegistry(
            (replace(PROTOTYPE_PROFILE, required_capabilities=frozenset({"gurps.check.success"})),)
        )
    with pytest.raises(ValidationError, match="does not permit source"):
        ProfileRegistry(
            (replace(lite, policy=replace(lite.policy, permitted_sources=frozenset())),)
        )
    with pytest.raises(ValidationError, match="Duplicate profile registration"):
        ProfileRegistry((PROTOTYPE_PROFILE, replace(PROTOTYPE_PROFILE, title="Again")))
    with pytest.raises(ValidationError, match="already registered"):
        ProfileRegistry((PROTOTYPE_PROFILE, replace(PROTOTYPE_PROFILE, version=2)))
    tampered = replace(PROTOTYPE_PROFILE, packages=(replace(PROTOTYPE_PACKAGE, version="9"),))
    with pytest.raises(ValidationError, match="does not resolve"):
        ProfileRegistry((tampered,))


def test_gurps_registration_cites_the_frozen_sources() -> None:
    """Independent expectation: the fixture's frozen source metadata, not engine output."""
    sources = {s["id"]: s for s in json.loads(FIXTURE.read_text())["sources"]}
    registered = {
        s.id: s
        for profile in (GURPS_LITE_PROFILE, GURPS_BASIC_PROFILE)
        for p in profile.packages
        for s in p.sources
    }
    assert set(registered) == set(sources)
    for identifier, source in sources.items():
        cited = registered[identifier]
        assert cited.title == source["title"]
        assert cited.rights == "user-supplied-reference"
        assert cited.citation is not None
        if source["printing"] is not None:
            assert "first printing" in cited.citation
            assert source["errata"][0]["revision"] in cited.citation
        else:
            assert source["revision"] in cited.citation
    assert GURPS_LITE_PROFILE.packages[0].sources[0].id == "sjg:gurps-lite-4e-2004"
    assert {s.id for p in GURPS_BASIC_PROFILE.packages for s in p.sources} == {
        "sjg:basic-set-characters-4e-2004",
        "sjg:basic-set-campaigns-4e-2004",
    }


async def test_new_campaigns_select_exact_profiles_and_old_ones_stay_unchanged(
    tmp_path: Path,
) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    graph = starting_scenario()
    default = await setup.create(
        CreateSetup(id="default", brief=graph.brief, graph=graph), principal_id="alice"
    )
    assert default["rules"] == reference(DEFAULT_RULES)
    assert default["rules_profile"] == {
        "id": "profile:wayfarer-lite",
        "version": 1,
        "title": "Wayfarer prototype rules",
        "supported": True,
    }
    chosen = await setup.create(
        CreateSetup(id="chosen", brief=graph.brief, graph=graph, rules_profile=EXTENDED),
        principal_id="alice",
    )
    assert chosen["rules"] == EXTENDED_PROFILE.reference
    assert chosen["rules_profile"] == {
        "id": EXTENDED_PROFILE.id,
        "version": 1,
        "title": EXTENDED_PROFILE.title,
        "supported": True,
    }
    old_graph = graph.model_copy(update={"rules": None})
    old = await setup.create(
        CreateSetup(id="old", brief=old_graph.brief, graph=old_graph), principal_id="alice"
    )
    assert old["rules"] == reference(DEFAULT_RULES)


async def test_unverified_gurps_selection_is_rejected_before_state_creation(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    graph = starting_scenario()
    with pytest.raises(ValidationError, match="not supported"):
        await setup.create(
            CreateSetup(id="gurps", brief=graph.brief, graph=graph, rules_profile=GURPS_LITE),
            principal_id="alice",
        )
    assert await profiles.store.load("gurps") is None


async def test_failed_migration_rolls_back_and_cannot_be_replayed(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    graph = starting_scenario()
    cid = await activated(setup, graph, None)
    before = await profiles.load(cid)
    assert before is not None
    digest = profiles.play.digest(before)
    command = migration(cid, digest, chosen=GURPS_LITE)
    with pytest.raises(ValidationError, match="not supported"):
        await profiles.migrations.execute(cid, command, principal_id="alice")
    after = await profiles.load(cid)
    assert after == before
    with pytest.raises(ValidationError, match="not supported"):
        await profiles.migrations.execute(cid, command, principal_id="alice")


async def test_migration_is_atomic_replayable_and_recompiles_state(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    graph = extended_graph(item=True, trait=True, check=True)
    cid = await activated(setup, graph, None)
    before = await profiles.load(cid)
    assert before is not None
    digest = profiles.play.digest(before)
    command = migration(cid, digest)
    result = await profiles.migrations.execute(cid, command, principal_id="alice")
    assert result.rules == EXTENDED_PROFILE.reference
    assert result.revision == 5
    assert result.character_builds["mira"].total_points == 5
    assert result.resources.items[0].definition_id == "equipment:test-lantern"
    replay = await profiles.migrations.execute(cid, command, principal_id="alice")
    assert replay == result


async def test_migration_requires_gm_pause_and_exact_current_digest(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    cid = await activated(setup, starting_scenario(), None)
    state = await profiles.load(cid)
    assert state is not None
    digest = profiles.play.digest(state)
    command = migration(cid, digest)
    with pytest.raises(AuthorizationError):
        await profiles.migrations.execute(cid, command, principal_id="bob")
    bad = command.model_copy(update={"expected_from_digest": "0" * 64})
    with pytest.raises(ConflictError, match="digest"):
        await profiles.migrations.execute(cid, bad, principal_id="alice")


async def test_migration_rejects_stale_revision_and_reused_id(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    cid = await activated(setup, starting_scenario(), None)
    state = await profiles.load(cid)
    assert state is not None
    digest = profiles.play.digest(state)
    with pytest.raises(ConflictError, match="revision"):
        await profiles.migrations.execute(
            cid,
            migration(cid, digest).model_copy(update={"expected_revision": 3}),
            principal_id="alice",
        )
    migrated = await profiles.migrations.execute(cid, migration(cid, digest), principal_id="alice")
    with pytest.raises(ConflictError, match="already used"):
        await profiles.migrations.execute(
            cid,
            migration(cid, profiles.play.digest(migrated), command_id="m").model_copy(
                update={"expected_revision": 5}
            ),
            principal_id="alice",
        )


async def test_unknown_profile_migration_is_rejected(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    cid = await activated(setup, starting_scenario(), None)
    state = await profiles.load(cid)
    assert state is not None
    unknown = ProfileSelection(id="profile:missing", version=1)
    with pytest.raises(ValidationError, match="Unknown rules profile"):
        await profiles.migrations.execute(
            cid,
            migration(cid, profiles.play.digest(state), chosen=unknown),
            principal_id="alice",
        )


async def test_http_profile_projection_and_migration(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    cid = await activated(setup, starting_scenario(), None)
    state = await profiles.load(cid)
    assert state is not None
    app = create_campaign_app(profiles.play, profiles.migrations, principal_for_token=TOKENS.get)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.get(f"/campaigns/{cid}", headers=ALICE)
        assert response.status == 200
        body = await response.json()
        assert body["rules_profile"] == {
            "id": PROTOTYPE_PROFILE.id,
            "version": PROTOTYPE_PROFILE.version,
            "title": PROTOTYPE_PROFILE.title,
            "supported": True,
        }
        response = await client.post(
            f"/campaigns/{cid}/rules-profile",
            headers=ALICE,
            json={
                "id": "m",
                "expected_revision": 4,
                "profile": {"id": EXTENDED_PROFILE.id, "version": EXTENDED_PROFILE.version},
                "expected_from_digest": profiles.play.digest(state),
                "reason": "Approved profile change",
            },
        )
        assert response.status == 200
        migrated = await response.json()
        assert migrated["rules_profile"]["id"] == EXTENDED_PROFILE.id
        assert migrated["rules_profile"]["supported"] is True
    finally:
        await client.close()


async def test_http_unknown_campaign_and_invalid_profile_errors(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    app = create_campaign_app(profiles.play, profiles.migrations, principal_for_token=TOKENS.get)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.get("/campaigns/missing", headers=ALICE)
        assert response.status == 404
        response = await client.post(
            "/campaigns/missing/rules-profile",
            headers=ALICE,
            json={
                "id": "m",
                "expected_revision": 0,
                "profile": {"id": "profile:missing", "version": 1},
                "expected_from_digest": "0" * 64,
                "reason": "Approved profile change",
            },
        )
        assert response.status == 404
    finally:
        await client.close()
