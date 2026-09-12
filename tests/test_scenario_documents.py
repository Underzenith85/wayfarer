"""Portable content uses the real compiler, studio, storage and privacy boundaries."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError as SchemaError
from test_actions import campaign
from test_scenes import configured
from test_wave11 import graph_fixture

from wayfarer.engine.simulation.access import CampaignMember
from wayfarer.engine.simulation.scenario_document import (
    DraftRevision,
    PlayerScenarioExport,
    PublicBrief,
    PublishedRevision,
    ScenarioDocument,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenario_documents import (
    ScenarioDocuments,
    adapt_graph,
    bind_party,
    engine_digest,
    parse_document,
)
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def setup(tmp_path: Path) -> tuple[ScenarioDocuments, ScenarioDocument]:
    engine, _ = configured()
    studio = ScenarioStudio(PlayService(AsyncSQLiteStore(tmp_path / "documents.sqlite"), engine))
    graph = graph_fixture()
    document = adapt_graph(
        graph,
        studio=studio,
        revision_id="courier-r1",
        author="Test author",
        public=PublicBrief(
            title="A missing courier",
            summary="Find a missing traveler.",
            setup=graph.brief,
            opening_prompt="Explore the docks.",
        ),
    )
    assert isinstance(document, ScenarioDocument)
    return ScenarioDocuments(studio), document


def publish(service: ScenarioDocuments, document: ScenarioDocument) -> PublishedRevision:
    return service.publish(
        service.save_draft(document.canonical(), draft_id="draft", principal_id="gm"),
        principal_id="gm",
    )


def test_canonical_round_trip_and_shared_schemas(tmp_path: Path) -> None:
    service, document = setup(tmp_path)
    formatted = json.dumps(document.model_dump(mode="json"), indent=4, ensure_ascii=True)
    restored = parse_document(formatted)
    assert restored == document
    assert restored.digest == document.digest
    assert service.validate(formatted).content_digest == document.digest
    assert bind_party(document).world == graph_fixture().world
    for model in (ScenarioDocument, PlayerScenarioExport):
        Draft202012Validator.check_schema(model.model_json_schema())
    Draft202012Validator(ScenarioDocument.model_json_schema()).validate(json.loads(formatted))
    assert "actors" not in type(document.graph).model_fields


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-version",
        "boolean-version",
        "unknown-field",
        "credentials",
        "duplicate-actor",
        "duplicate-entity",
        "duplicate-approach",
        "dangling-fact",
        "bad-predicate",
        "bad-condition",
        "runtime-history",
        "runtime-time",
        "rules",
        "engine",
        "capability",
        "undeclared-capability",
        "dangling-slot",
        "unavailable-catalog",
        "illegal-pregen",
        "incompatible-party",
        "conflicting-mechanics",
        "duplicate-key",
        "nan",
    ],
)
def test_invalid_drafts_are_preserved_but_not_publishable(tmp_path: Path, mutation: str) -> None:
    service, document = setup(tmp_path)
    raw = document.model_dump(mode="json")
    # JSON fixtures deliberately exercise untyped imports and model-generated proposals.
    data = json.loads(json.dumps(raw))
    graph = data["graph"]
    if mutation == "unknown-version":
        data["schema_version"] = 99
    elif mutation == "boolean-version":
        data["schema_version"] = True
    elif mutation == "unknown-field":
        graph["world"]["entities"][0]["executable"] = "run this"
    elif mutation == "credentials":
        data["principal_id"] = "private-account"
    elif mutation == "duplicate-actor":
        data["party"]["slots"] *= 2
    elif mutation == "duplicate-entity":
        graph["world"]["entities"] += graph["world"]["entities"][:1]
    elif mutation == "duplicate-approach":
        graph["approaches"] = [
            dict(id="approach", scene_id="dock-scene", check_rule_id="missing", actor_id="a")
        ] * 2
    elif mutation == "dangling-fact":
        graph["objectives"]["objectives"][0]["predicates"][0]["value"] = "missing-fact"
        graph["actions"]["objectives"] = graph["objectives"]
    elif mutation == "bad-predicate":
        graph["objectives"]["failures"] = [dict(kind="python", subject_id="a", value="eval()")]
    elif mutation == "bad-condition":
        graph["objectives"]["failures"] = [
            dict(kind="condition", subject_id="a", value="invincible")
        ]
        graph["actions"]["objectives"] = graph["objectives"]
    elif mutation == "runtime-history":
        graph["resources"]["receipts"] = [dict(command_id="secret-command", digest="x")]
    elif mutation == "runtime-time":
        graph["resources"]["game_time"] = 5
    elif mutation == "rules":
        data["compatibility"]["rules"]["packages"][0]["digest"] = "0" * 64
    elif mutation == "engine":
        data["compatibility"]["engine_digest"] = "0" * 64
    elif mutation == "capability":
        data["compatibility"]["capabilities"].append("execute-code")
    elif mutation == "undeclared-capability":
        data["compatibility"]["capabilities"] = ["actions-v1", "objectives-v1", "combat-v1"]
    elif mutation == "dangling-slot":
        data["pregenerated"][0]["slot_id"] = "missing"
    elif mutation == "unavailable-catalog":
        data["party"]["slots"][0]["required_definitions"] = ["magic:invented"]
    elif mutation == "illegal-pregen":
        data["pregenerated"][0]["proposal"]["draft"]["purchases"] = []
    elif mutation == "incompatible-party":
        data["party"]["maximum_points"] = 0
    elif mutation == "conflicting-mechanics":
        graph["actions"]["objectives"] = {**graph["objectives"], "deadline": 999}
    source = json.dumps(data)
    if mutation == "duplicate-key":
        source = source.replace('"schema_version": 1', '"schema_version": 2, "schema_version": 1')
    if mutation == "nan":
        source = source.replace('"revision": 1', '"revision": NaN')
    draft = service.save_draft(source, draft_id="draft", principal_id="gm")
    assert draft.report.status == "invalid", (mutation, draft.report)
    assert draft.report.findings
    saved = DraftRevision.model_validate_json(draft.model_dump_json())
    assert saved.content_json == source
    with pytest.raises(ValidationError):
        service.publish(saved, principal_id="gm")


def test_party_is_required_and_never_changes_references(tmp_path: Path) -> None:
    service, document = setup(tmp_path)
    unbound = document.model_copy(update={"pregenerated": ()})
    assert service.validate(unbound.canonical()).status == "needs-party"
    assert service.validate(unbound.canonical(), party=()).status == "invalid"
    party = document.pregenerated
    assert service.validate(unbound.canonical(), party=party).status == "playable"
    assert service.validate(unbound.canonical(), party=party + party).status == "invalid"
    bound = bind_party(unbound, party)
    assert bound.world == document.graph.world
    assert bound.objectives == document.graph.objectives
    assert bound.actors[0].actor_id == document.party.slots[0].actor_id
    draft = service.save_draft(
        unbound.canonical(), draft_id="draft", principal_id="gm", party=party
    )
    with pytest.raises(ValidationError):
        service.publish(draft, principal_id="gm")
    assert service.publish(draft, principal_id="gm", party=party).report.party_digest


def test_private_exports_and_authorization(tmp_path: Path) -> None:
    service, document = setup(tmp_path)
    graph = document.graph.model_copy(
        update={"title": "SECRET GRAPH TITLE", "failure_consequence": "SECRET ENDING"}
    )
    provenance = document.provenance.model_copy(update={"author": "SECRET AUTHOR"})
    document = document.model_copy(
        update={"graph": graph, "provenance": provenance, "gm_notes": "SECRET NOTES"}
    )
    revision = publish(service, document)
    public = service.player_export(revision).model_dump_json()
    assert "SECRET" not in public
    assert "graph" not in json.loads(public)
    assert "pregenerated" not in json.loads(public)
    assert "clue" not in public
    Draft202012Validator(PlayerScenarioExport.model_json_schema()).validate(json.loads(public))
    private = service.author_export(revision, principal_id="gm")
    assert "SECRET NOTES" in private and "SECRET ENDING" in private
    with pytest.raises(AuthorizationError):
        service.author_export(revision, principal_id="alice")
    with pytest.raises(AuthorizationError):
        service.save_draft(private, draft_id="x", principal_id="alice")


async def test_published_snapshot_survives_edits_and_restart(tmp_path: Path) -> None:
    service, document = setup(tmp_path)
    revision = publish(service, document)
    initial = campaign(service.studio.engine(bind_party(document)))
    members = (CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),)
    activated = await service.activate(revision, initial, members, principal_id="gm")
    await service.activate(revision, initial, members, principal_id="gm")
    restarted, _ = setup(tmp_path)
    stored = await restarted.studio.play.store.read(initial["id"])
    assert stored["scenario_document_json"] == document.canonical()
    assert "scenario_document_json" not in await CampaignAccess(activated).read(
        initial["id"], principal_id="alice"
    )
    later = document.model_copy(
        update={"revision_id": "courier-r2", "revision": 2, "gm_notes": "New secret"}
    )
    next_revision = publish(service, later)
    with pytest.raises(ConflictError):
        await service.activate(next_revision, initial, members, principal_id="gm")
    assert (await service.studio.play.store.read(initial["id"]))[
        "scenario_document_json"
    ] == revision.content_json
    assert PublishedRevision.model_validate_json(revision.model_dump_json()) == revision
    with pytest.raises(SchemaError):
        PublishedRevision.model_validate_json(
            revision.model_copy(update={"content_json": later.canonical()}).model_dump_json()
        )
    with pytest.raises(AuthorizationError):
        await service.activate(revision, initial, members, principal_id="alice")


def test_draft_compare_and_swap_and_stale_report(tmp_path: Path) -> None:
    service, document = setup(tmp_path)
    draft = service.save_draft(document.canonical(), draft_id="draft", principal_id="gm")
    edited = service.save_draft(
        "{}", draft_id="draft", principal_id="gm", previous=draft, expected_edit=1
    )
    assert edited.edit == 2 and draft.report.status == "playable"
    with pytest.raises(ConflictError):
        service.save_draft(
            "{}", draft_id="draft", principal_id="gm", previous=edited, expected_edit=1
        )
    with pytest.raises(ValidationError):
        service.publish(edited.model_copy(update={"report": draft.report}), principal_id="gm")
    policy = service.studio.play.engine.reviewer.compiler.policy
    service.studio.play.engine.reviewer.compiler.policy = replace(
        policy, point_budget=policy.point_budget + 1
    )
    assert engine_digest(service.studio) != draft.report.engine_digest
    with pytest.raises(ValidationError):
        service.publish(draft, principal_id="gm")


def test_published_examples_and_contract_drift(tmp_path: Path) -> None:
    from scripts.scenario_contracts import ROOT, artifacts
    from wayfarer.adventures.lantern import engine

    configured = engine()
    service = ScenarioDocuments(
        ScenarioStudio(
            PlayService(AsyncSQLiteStore(tmp_path / "examples.sqlite"), configured),
            npc_reviewer=configured.reviewer,
        )
    )
    for name, expected in artifacts().items():
        assert (ROOT / name).read_text() == expected, name
    schema = json.loads((ROOT / "document.schema.json").read_text())
    for name in ("authored", "generated"):
        source = (ROOT / f"{name}.example.json").read_text()
        Draft202012Validator(schema).validate(json.loads(source))
        assert service.validate(source).status == "playable"
        document = parse_document(source)
        assert parse_document(document.canonical()) == document
        assert bind_party(document).recovery == document.graph.recovery
        assert document.graph.recovery and document.graph.npcs
        broken = json.loads(source)
        broken["graph"]["recovery"]["options"][0]["scene_id"] = "missing-scene"
        assert service.validate(json.dumps(broken)).status == "invalid"


async def test_activation_revalidates_actual_party_and_engine(tmp_path: Path) -> None:
    service, document = setup(tmp_path)
    revision = publish(service, document)
    initial = campaign(service.studio.engine(bind_party(document)))
    members = (CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),)
    with pytest.raises(ValidationError):
        await service.activate(revision, initial, members, principal_id="gm", party=())
    pregen = document.pregenerated[0]
    changed = pregen.model_copy(
        update={
            "proposal": pregen.proposal.model_copy(
                update={
                    "draft": pregen.proposal.draft.model_copy(
                        update={"backstory": "A different legal character"}
                    )
                }
            )
        }
    )
    report = service.validate(document.canonical(), party=(changed,))
    assert report.status == "playable" and report.party_digest != revision.report.party_digest
    activated = await service.activate(
        revision, initial, members, principal_id="gm", party=(changed,)
    )
    assert (
        activated._load(await activated.store.read(initial["id"])).actors[0].proposal
        == changed.proposal
    )
    policy = service.studio.play.engine.reviewer.policy
    service.studio.play.engine.reviewer.policy = policy.model_copy(
        update={"version": policy.version + 1}
    )
    with pytest.raises(ValidationError):
        await service.activate(revision, initial, members, principal_id="gm", party=(changed,))


async def test_existing_graph_activation_ignores_json_key_order(tmp_path: Path) -> None:
    service, document = setup(tmp_path)
    graph = bind_party(document)
    engine = service.studio.engine(graph)
    initial = campaign(engine)
    # Existing graph serialization placed actors before NPC fields; factoring the
    # shared content base must not turn a semantically identical retry into a conflict.
    initial["scenario_graph_json"] = json.dumps(graph.model_dump(mode="json"), sort_keys=True)
    members = (CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),)
    await PlayService(service.studio.play.store, engine).create(
        initial, graph.world, graph.resources, graph.actors, members
    )
    await service.studio.activate(graph, initial, members, principal_id="gm")


@pytest.mark.parametrize(
    ("mutation", "code", "reference", "expected"),
    [
        (
            "undeclared-capability",
            "document.capabilities",
            "compatibility",
            "engine capabilities noncombat-v1 that compatibility.capabilities does not declare",
        ),
        (
            "rules",
            "document.rules",
            "compatibility",
            "engine digest",
        ),
        (
            "unavailable-catalog",
            "party.definition",
            "a",
            "Slot a requires magic:invented, which is not in the pinned catalog",
        ),
        (
            "incompatible-party",
            "party.pregen",
            "a",
            "points, outside the 0-0 range this scenario allows",
        ),
        (
            "duplicate-entity",
            "document.duplicate",
            "document.graph.world.entities",
            "declares dock more than once",
        ),
    ],
)
def test_document_findings_name_the_node_and_the_remedy(
    tmp_path: Path, mutation: str, code: str, reference: str, expected: str
) -> None:
    """A document finding an author cannot act on is a defect of the validator (#365)."""
    service, document = setup(tmp_path)
    data = json.loads(document.canonical())
    if mutation == "undeclared-capability":
        data["graph"]["noncombat"] = {
            "id": "case",
            "version": 1,
            "encounters": [
                {
                    "id": "search",
                    "scene_id": "dock-scene",
                    "category": "investigation",
                    "stakes": "Find evidence",
                    "required_progress": 2,
                    "approaches": [{"id": "look", "check_rule_id": "inspect"}],
                }
            ],
        }
    elif mutation == "rules":
        data["compatibility"]["engine_digest"] = "0" * 64
    elif mutation == "unavailable-catalog":
        data["party"]["slots"][0]["required_definitions"] = ["magic:invented"]
    elif mutation == "incompatible-party":
        data["party"]["maximum_points"] = 0
    else:
        data["graph"]["world"]["entities"] += data["graph"]["world"]["entities"][:1]
    report = service.validate(json.dumps(data))
    finding = next(f for f in report.findings if f.code == code)
    assert finding.reference == reference, report
    assert expected in finding.message, finding.message
