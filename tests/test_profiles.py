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
    # The creation receipt for a profile-less command keeps its pre-profile payload bytes.
    stored = await profiles.store.read(str(default["id"]))
    assert "rules_profile" not in json.loads(SetupService.load(stored).creation_json)
    with pytest.raises(ValidationError, match="not supported"):
        await setup.create(
            CreateSetup(id="gurps", brief=graph.brief, graph=graph, rules_profile=GURPS_LITE),
            principal_id="alice",
        )
    with pytest.raises(ValidationError, match="Unknown rules profile"):
        await setup.create(
            CreateSetup(
                id="unknown",
                brief=graph.brief,
                graph=graph,
                rules_profile=ProfileSelection(id="profile:test-extended", version=2),
            ),
            principal_id="alice",
        )
    assert {c["id"] for c in await setup.play.store.listing()} == {default["id"], chosen["id"]}
    # A service composed without a registry cannot honour selections.
    bare = SetupService(CampaignAccess(PlayService(profiles.store, build(PROTOTYPE_PROFILE))))
    with pytest.raises(ValidationError, match="unavailable"):
        await bare.create(
            CreateSetup(id="bare", brief=graph.brief, graph=graph, rules_profile=EXTENDED),
            principal_id="alice",
        )


async def test_dispatch_binds_each_campaign_to_its_own_profile(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    extended = await activated(setup, extended_graph(item=True, trait=True), EXTENDED)
    default = await activated(setup, starting_scenario(), None)
    # The extended character is illegal under the prototype profile...
    from wayfarer.orchestration.studio import ScenarioStudio

    assert not ScenarioStudio(profiles.play).validate(extended_graph(trait=True)).valid
    assert (
        ScenarioStudio(profiles.service(EXTENDED_PROFILE))
        .validate(extended_graph(trait=True))
        .valid
    )
    # ...yet dispatch loads each campaign through the service pinned to its own profile.
    access = CampaignAccess(profiles.play)
    bound = await access.runtime(extended)
    assert bound.play.engine.reviewer.compiler.rules == EXTENDED_PROFILE.rules
    state = bound.play._load(await profiles.store.read(extended))
    assert state.resources.items[0].definition_id == "equipment:test-lantern"
    assert (await access.runtime(default)).play.engine.reviewer.compiler.rules == DEFAULT_RULES
    restarted = runtime(tmp_path)
    again = await CampaignAccess(restarted.play).runtime(extended)
    assert again.play.engine.digest == bound.play.engine.digest
    # Without a registry, a differently pinned campaign fails closed as before.
    bare = PlayService(profiles.store, build(PROTOTYPE_PROFILE))
    with pytest.raises(ValidationError, match="do not match the play engine"):
        bare.for_campaign(await profiles.store.read(extended))._load(
            await profiles.store.read(extended)
        )


async def test_migration_previews_incompatibilities_and_blocks_atomically(
    tmp_path: Path,
) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    migrations = ProfileMigrations(profiles)
    cid = await activated(setup, extended_graph(item=True, trait=True), EXTENDED)
    preview = await migrations.preview(cid, PROTOTYPE, principal_id="alice")
    assert preview.from_profile == EXTENDED and preview.to_profile == PROTOTYPE
    assert {(i.kind, i.reference) for i in preview.incompatibilities} == {
        ("character", "mira"),
        ("resource", "lantern-1"),
    }
    assert not preview.applicable and preview.to_digest is not None
    before = await profiles.store.read(cid)
    history = len(await profiles.store.history(cid))
    with pytest.raises(ValidationError, match="Migration blocked: character mira"):
        await migrations.apply(
            cid, migration(cid, preview.from_digest, chosen=PROTOTYPE), principal_id="alice"
        )
    assert await profiles.store.read(cid) == before
    assert len(await profiles.store.history(cid)) == history
    scenario = await activated(setup, extended_graph(check=True), EXTENDED)
    preview = await migrations.preview(scenario, PROTOTYPE, principal_id="alice")
    assert [i.kind for i in preview.incompatibilities] == ["scenario"]
    assert "implemented catalog skill" in preview.incompatibilities[0].message
    assert preview.to_digest is None
    with pytest.raises(ValidationError, match="Migration blocked: scenario"):
        await migrations.apply(
            scenario,
            migration(scenario, preview.from_digest, chosen=PROTOTYPE),
            principal_id="alice",
        )


async def test_migration_is_explicit_authorized_atomic_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    migrations = ProfileMigrations(profiles)
    cid = await activated(setup, starting_scenario(), None)
    preview = await migrations.preview(cid, EXTENDED, principal_id="alice")
    assert preview.applicable and preview.revision == 4
    assert [d.actor_id for d in preview.actor_diffs] == ["mira"]
    assert preview.actor_diffs[0].added == () and preview.actor_diffs[0].removed == ()
    assert preview.from_digest != preview.to_digest
    command = migration(cid, preview.from_digest)
    untouched = await profiles.store.read(cid)
    with pytest.raises(NotFoundError):
        await migrations.preview(cid, EXTENDED, principal_id="bob")
    with pytest.raises(NotFoundError):
        await migrations.apply(cid, command, principal_id="bob")
    with pytest.raises(ValidationError, match="not supported"):
        await migrations.preview(cid, GURPS_LITE, principal_id="alice")
    with pytest.raises(ValidationError, match="already uses"):
        await migrations.preview(cid, PROTOTYPE, principal_id="alice")
    with pytest.raises(ConflictError, match="source configuration changed"):
        await migrations.apply(
            cid, command.model_copy(update={"expected_from_digest": "stale"}), principal_id="alice"
        )
    with pytest.raises(ConflictError):
        await migrations.apply(
            cid, command.model_copy(update={"expected_revision": 3}), principal_id="alice"
        )
    with pytest.raises(ValidationError, match="Invalid profile migration"):
        await migrations.apply(cid, {"id": "m"}, principal_id="alice")
    await setup.execute(
        cid,
        SetupCommand(id="resume", expected_revision=4, operation="resume"),
        principal_id="alice",
    )
    with pytest.raises(ConflictError, match="Pause or complete"):
        await migrations.preview(cid, EXTENDED, principal_id="alice")
    await setup.execute(
        cid,
        SetupCommand(id="pause-again", expected_revision=5, operation="pause"),
        principal_id="alice",
    )
    assert await profiles.store.read(cid) != untouched
    untouched = await profiles.store.read(cid)
    command = command.model_copy(update={"expected_revision": 6})
    # A failure inside the write transaction leaves no partial state or receipt...
    target = profiles.service(EXTENDED_PROFILE)
    original = ActionEngine.validate

    def explode(self: ActionEngine, state: PlayState) -> None:
        if self.reviewer.compiler.rules == EXTENDED_PROFILE.rules and state.migrations:
            raise ValidationError("Injected migration failure")
        original(self, state)

    monkeypatch.setattr(ActionEngine, "validate", explode)
    with pytest.raises(ValidationError, match="Injected"):
        await migrations.apply(cid, command, principal_id="alice")
    monkeypatch.setattr(ActionEngine, "validate", original)
    assert await profiles.store.read(cid) == untouched
    assert target.engine.reviewer.compiler.rules == EXTENDED_PROFILE.rules
    # ...so the identical retry applies once and later retries replay the receipt.
    entry = await migrations.apply(cid, command, principal_id="alice")
    assert entry.from_profile == "profile:wayfarer-lite@1"
    assert entry.to_profile == "profile:test-extended@1"
    assert entry.from_digest == preview.from_digest and entry.to_digest == preview.to_digest
    assert await migrations.apply(cid, command, principal_id="alice") == entry
    with pytest.raises(ConflictError):
        await migrations.apply(
            cid, command.model_copy(update={"reason": "Different"}), principal_id="alice"
        )
    migrated = await profiles.store.read(cid)
    assert migrated["rules_ref"] == EXTENDED_PROFILE.reference
    assert migrated["revision"] == 7
    assert await profiles.store.read(cid) == await profiles.store.replay(cid)
    view = await setup.read(cid, principal_id="alice")
    assert view["rules_profile"] == {
        "id": EXTENDED_PROFILE.id,
        "version": 1,
        "title": EXTENDED_PROFILE.title,
        "supported": True,
    }
    access = await CampaignAccess(profiles.play).runtime(cid)
    state = access.play._load(migrated)
    assert [m.id for m in state.migrations] == ["m"]
    assert state.configuration_digest == access.play.engine.digest
    assert access.play.engine.reviewer.compiler.rules == EXTENDED_PROFILE.rules
    # Play continues under the new profile after a resume.
    await setup.execute(
        cid,
        SetupCommand(id="resume-2", expected_revision=7, operation="resume"),
        principal_id="alice",
    )
    await access.execute(
        cid,
        {"kind": "wait", "id": "wait", "actor_id": "mira", "expected_revision": 8, "ticks": 1},
        principal_id="alice",
    )
    with pytest.raises(ValidationError, match="already uses"):
        await migrations.preview(cid, EXTENDED, principal_id="alice")


async def test_only_the_host_migrates_a_shared_table(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    graph = starting_scenario(2)
    created = await setup.create(
        CreateSetup(id="shared", brief=graph.brief, graph=graph), principal_id="alice"
    )
    cid = str(created["id"])
    commands = (
        (
            "alice",
            SetupCommand(id="invite", expected_revision=0, operation="invite", principal_id="bob"),
        ),
        ("bob", SetupCommand(id="join", expected_revision=1, operation="join")),
        (
            "alice",
            SetupCommand(
                id="assign-a",
                expected_revision=2,
                operation="assign",
                principal_id="alice",
                actor_ids=("mira",),
            ),
        ),
        (
            "alice",
            SetupCommand(
                id="assign-b",
                expected_revision=3,
                operation="assign",
                principal_id="bob",
                actor_ids=("iven",),
            ),
        ),
        ("alice", SetupCommand(id="ready-a", expected_revision=4, operation="ready")),
        ("bob", SetupCommand(id="ready-b", expected_revision=5, operation="ready")),
        ("alice", SetupCommand(id="activate", expected_revision=6, operation="activate")),
        ("alice", SetupCommand(id="pause", expected_revision=7, operation="pause")),
    )
    for principal, command in commands:
        await setup.execute(cid, command, principal_id=principal)
    migrations = ProfileMigrations(profiles)
    preview = await migrations.preview(cid, EXTENDED, principal_id="alice")
    assert {d.actor_id for d in preview.actor_diffs} == {"mira", "iven"}
    with pytest.raises(AuthorizationError, match="Only the host"):
        await migrations.preview(cid, EXTENDED, principal_id="bob")
    approval = migration(cid, preview.from_digest).model_copy(update={"expected_revision": 8})
    with pytest.raises(AuthorizationError, match="Only the host"):
        await migrations.apply(cid, approval, principal_id="bob")
    entry = await migrations.apply(cid, approval, principal_id="alice")
    state = (await CampaignAccess(profiles.play).runtime(cid)).play._load(
        await profiles.store.read(cid)
    )
    assert [a.approval.decision for a in state.actors if a.approval] == ["automatic", "automatic"]
    assert entry.revision == 9 == state.revision


async def test_http_profile_listing_selection_and_migration(tmp_path: Path) -> None:
    profiles = runtime(tmp_path)
    app = create_campaign_app(
        CampaignAccess(profiles.play), TOKENS, scenario_templates=(starting_scenario(),)
    )
    graph = starting_scenario().model_dump(mode="json")
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/setups/profiles")).status == 401
        response = await client.get("/setups/profiles", headers=ALICE)
        listed = {(p["id"], p["version"]): p for p in await response.json()}
        assert listed[("profile:wayfarer-lite", 1)]["supported"] is True
        assert listed[("profile:test-extended", 1)]["packages"][1]["dependencies"] == [
            "package:wayfarer-lite"
        ]
        lite = listed[("profile:gurps-lite-4e-2004", 3)]
        assert lite["supported"] is False and lite["conformance_profile_id"] == "gurps-lite-4e-2004"
        assert lite["unverified_capabilities"] == list(GURPS_LITE_PROFILE.unverified_capabilities)
        assert lite["packages"][0]["source_ids"] == ["sjg:gurps-lite-4e-2004"]
        response = await client.post(
            "/setups",
            headers=ALICE,
            json={
                "id": str(uuid.uuid4()),
                "brief": graph["brief"],
                "graph": graph,
                "rules_profile": {"id": "profile:gurps-lite-4e-2004", "version": 3},
            },
        )
        assert response.status == 400 and "not supported" in (await response.json())["error"]
        response = await client.post(
            "/setups",
            headers=ALICE,
            json={"id": str(uuid.uuid4()), "brief": graph["brief"], "graph": graph},
        )
        assert response.status == 201, await response.text()
        lobby = await response.json()
        cid = lobby["id"]
        assert lobby["rules_profile"]["id"] == "profile:wayfarer-lite"
        steps: list[dict[str, object]] = [
            {"operation": "assign", "principal_id": "alice", "actor_ids": ["mira"]},
            {"operation": "ready"},
            {"operation": "activate"},
        ]
        for revision, fields in enumerate(steps):
            response = await client.post(
                f"/setups/{cid}",
                headers=ALICE,
                json={"id": str(uuid.uuid4()), "expected_revision": revision, **fields},
            )
            assert response.status == 200, await response.text()
        query = {"profile_id": "profile:test-extended", "version": "1"}
        response = await client.get(f"/setups/{cid}/migration", headers=ALICE, params=query)
        assert response.status == 409, await response.text()
        response = await client.post(
            f"/setups/{cid}",
            headers=ALICE,
            json={"id": str(uuid.uuid4()), "expected_revision": 3, "operation": "pause"},
        )
        assert response.status == 200
        response = await client.get(f"/setups/{cid}/migration", headers=ALICE, params=query)
        assert response.status == 200, await response.text()
        preview = await response.json()
        assert preview["applicable"] is True and preview["incompatibilities"] == []
        response = await client.get(
            f"/setups/{cid}/migration", headers=ALICE, params={"profile_id": "x"}
        )
        assert response.status == 400
        response = await client.get(
            f"/setups/{cid}/migration", headers={"Authorization": "Bearer bob-token"}, params=query
        )
        assert response.status == 404
        body = {
            "id": "http-migration",
            "expected_revision": preview["revision"],
            "profile": preview["to_profile"],
            "expected_from_digest": preview["from_digest"],
            "reason": "Switch to the extended profile",
        }
        response = await client.post(f"/setups/{cid}/migration", headers=ALICE, json=body)
        assert response.status == 200, await response.text()
        result = await response.json()
        assert result["entry"]["to_profile"] == "profile:test-extended@1"
        assert result["setup"]["rules_profile"]["id"] == "profile:test-extended"
        response = await client.post(f"/setups/{cid}/migration", headers=ALICE, json=body)
        assert response.status == 200 and (await response.json())["entry"] == result["entry"]
        response = await client.get(f"/setups/{cid}", headers=ALICE)
        assert (await response.json())["rules"] == EXTENDED_PROFILE.reference
        access = await client.app[SETUP_KEY].access.runtime(cid)
        assert access.play.engine.reviewer.compiler.rules == EXTENDED_PROFILE.rules


async def test_servers_without_a_registry_expose_no_profiles(tmp_path: Path) -> None:
    play = PlayService(AsyncSQLiteStore(tmp_path / "bare.sqlite", 10), build(PROTOTYPE_PROFILE))
    app = create_campaign_app(CampaignAccess(play), TOKENS)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/setups/profiles", headers=ALICE)
        assert await response.json() == []
        response = await client.get("/setups/none/migration", headers=ALICE)
        assert response.status == 400


def test_profile_contract_schema_drift() -> None:
    from wayfarer.simulation.advancement import MigrationEntry
    from wayfarer.simulation.profiles import ProfileMigrationPreview, ProfileView

    models = (
        ProfileSelection,
        ProfileView,
        ProfileMigrationPreview,
        MigrateProfile,
        MigrationEntry,
    )
    expected = {model.__name__: model.model_json_schema() for model in models}
    assert json.loads(Path("contracts/profiles/v1/schemas.json").read_text()) == expected
