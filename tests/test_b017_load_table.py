"""Selected Characters B17 table: literal cells and actual approved/inventory consumers."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import fixed_instants, open_store, seed_campaign
from test_resources import campaign
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer import validation
from wayfarer.certification.executable_evidence import (
    FIXTURE,
    provenance,
)
from wayfarer.engine.character import statistics
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.character.statistics import CharacterStatistics, StatisticsError
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.simulation.equipment.catalog import (
    EquipmentCatalog,
    EquipmentProfile,
    Provenance,
    inventory_load,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import (
    COMMAND_ADAPTER,
    Item,
    Owner,
    ResourceState,
    Transfer,
)
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.resources import ResourceService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.command_inputs import replay_payload

BASIC: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
ROOT = Path(__file__).resolve().parents[1]
SECTION = "section:characters:b017:basic-lift-and-encumbrance-table"
WEIGHT = "equipment:b017-calibration-weight"


def cases(operation: str) -> list[dict[str, object]]:
    data = json.loads((ROOT / FIXTURE).read_text())
    return [c for c in data["cases"] if c.get("operation") == operation]


def inputs(case: dict[str, object]) -> dict[str, object]:
    return validation.mapping(case["input"])


def expected(case: dict[str, object]) -> dict[str, object]:
    return validation.mapping(case["expected"])


@dataclass(frozen=True)
class Context:
    compiler: CharacterCompiler
    reviewer: PowerReviewer
    equipment: EquipmentCatalog
    resources: ResourceEngine


def context() -> Context:
    # Authored calibration mass, not a claim that B17 adds a catalog equipment row.
    base = profile_package(BASIC)
    package = replace(
        base,
        version="0.6.0",
        definitions=statistics.definitions(BASIC, revision=2)
        + (
            RuleDefinition(
                WEIGHT,
                DefinitionKind.EQUIPMENT,
                "Authored calibration mass",
                statistics.source(BASIC).id,
                0,
                ImplementationStatus.IMPLEMENTED,
            ),
        ),
    )
    original = profile_compiler(BASIC, package=package)
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        original.rules,
        replace(original.policy, allowed_equipment=frozenset({WEIGHT})),
        statistics_profile=BASIC,
    )
    equipment = EquipmentCatalog(
        profile_id=BASIC,
        entries=(
            EquipmentProfile(
                definition_id=WEIGHT,
                provenance=Provenance(
                    source_id=statistics.source(BASIC).id,
                    edition="Fourth Edition, third printing (February 2008)",
                    pages=(17,),
                    errata="none",
                ),
                weight_millipounds=1,
                price=0,
                technology_level=0,
            ),
        ),
    )
    world = World(entities=(Entity("a", EntityKind.ACTOR, "A"), Entity("b", EntityKind.ACTOR, "B")))
    resource = ResourceEngine(
        world, RulesCatalog((package,)), compiler.rules, compiler.policy, equipment.bind((package,))
    )
    return Context(
        compiler,
        PowerReviewer(compiler, PowerPolicy(id="b017", version=1), frozenset({"gm"})),
        equipment,
        resource,
    )


def approved(
    ctx: Context,
    strength: int = 10,
    *,
    move: int | None = None,
    speed: int | None = None,
    actor_id: str = "a",
    campaign_id: str = "b017",
) -> CharacterStatistics:
    purchases = tuple(
        Purchase(definition_id=id, amount=amount)
        for id, amount in (
            ("secondary:basic-move", move),
            ("secondary:basic-speed", speed),
        )
        if amount is not None
    )
    proposal = CharacterProposal(draft=gurps_draft(*purchases, st_level=strength))
    approval = ctx.reviewer.approve(
        proposal,
        campaign_id=campaign_id,
        actor_id=actor_id,
        revision=0,
        approver_id="gm",
        reason="Approve source-defined purchased statistics including explicit guideline exceptions",
    )
    build, runtime = ctx.reviewer.activate(
        proposal, approval, campaign_id=campaign_id, actor_id=actor_id
    )
    assert build.statistics is not None and build.statistics.profile_id == BASIC
    assert ctx.compiler.statistics is not None and ctx.compiler.statistics.revision == 2
    assert build.revision == approval.build_revision
    assert build.statistics.st == strength and runtime.hp == strength
    assert {p.definition_id: p.cost for p in build.purchases}["attribute:st"] == (
        strength - 10
    ) * 10
    return build.statistics


def load_state(pounds: Decimal) -> ResourceState:
    units = pounds * 1000
    assert units == units.to_integral_value() and units >= 0
    return ResourceState(
        owners=(Owner(actor_id="a", capacity=1000000), Owner(actor_id="b", capacity=1000000)),
        items=(Item(id="mass", definition_id=WEIGHT, owner_id="a", quantity=int(units)),)
        if units
        else (),
    )


def record(case: dict[str, object], record_property: Callable[[str, object], None]) -> None:
    assert case["profile"] == BASIC and case["statistics_revision"] == 2
    assert case["capability_id"] == "gurps.character.secondary_characteristics"
    for name, value in provenance(ROOT, case).items():
        record_property(name, value)


@pytest.mark.parametrize("case", cases("b017_table"), ids=lambda c: str(c["id"]))
def test_literal_printed_row_and_all_native_load_boundaries(
    case: dict[str, object], record_property: Callable[[str, object], None]
) -> None:
    ctx = context()
    stats = approved(ctx, int(str(inputs(case)["ST"])))
    wanted = expected(case)
    raw = wanted["thresholds"]
    assert isinstance(raw, list)
    thresholds = tuple(Decimal(str(value)) for value in raw)
    assert stats.basic_lift == Decimal(str(wanted["basic_lift"]))
    assert statistics.encumbrance_thresholds(BASIC, stats.basic_lift) == thresholds
    empty = inventory_load(ctx.equipment, ctx.resources, load_state(Decimal(0)), "a", stats)
    assert empty.weight_pounds == Decimal(0)
    assert empty.level == 0 and empty.move == 5 and empty.dodge == 8
    for index, limit in enumerate(thresholds):
        for weight, level in (
            (limit - Decimal(".001"), index),
            (limit, index),
            (limit + Decimal(".001"), index + 1 if index < 4 else None),
        ):
            result = inventory_load(ctx.equipment, ctx.resources, load_state(weight), "a", stats)
            assert result.weight_pounds == weight
            assert result.level == level
            assert (result.move, result.dodge) == (
                (None, None) if level is None else ([5, 4, 3, 2, 1][level], [8, 7, 6, 5, 4][level])
            )
    with pytest.raises(StatisticsError, match="negative"):
        statistics.encumbrance(BASIC, stats.basic_lift, Decimal("-.001"))
    record(case, record_property)


@pytest.mark.parametrize("case", cases("b017_load"), ids=lambda c: str(c["id"]))
def test_actual_inventory_load_consumes_printed_level(
    case: dict[str, object], record_property: Callable[[str, object], None]
) -> None:
    ctx = context()
    given = inputs(case)
    stats = approved(ctx, move=int(str(given["basic_move"])))
    load = inventory_load(
        ctx.equipment, ctx.resources, load_state(Decimal(str(given["weight"]))), "a", stats
    )
    wanted = expected(case)
    assert (load.level, load.move, load.dodge) == (wanted["level"], wanted["move"], wanted["dodge"])
    record(case, record_property)


@pytest.mark.parametrize("case", cases("b017_movement"), ids=lambda c: str(c["id"]))
def test_actual_loaded_movement_fraction_minimum_and_zero(
    case: dict[str, object], record_property: Callable[[str, object], None]
) -> None:
    ctx = context()
    given = inputs(case)
    move = int(str(given["basic_move"]))
    speed = int(str(given["basic_speed_quarters"])) if "basic_speed_quarters" in given else None
    stats = approved(ctx, move=None if move == 0 else move, speed=speed)
    assert stats.basic_move == move
    raw = given["weights"]
    assert isinstance(raw, list)
    loads = [
        inventory_load(ctx.equipment, ctx.resources, load_state(Decimal(str(value))), "a", stats)
        for value in raw
    ]
    assert [load.move for load in loads] == expected(case)["move"]
    assert [load.dodge for load in loads] == expected(case)["dodge"]
    record(case, record_property)


@pytest.mark.parametrize("case", cases("b017_ownership"), ids=lambda c: str(c["id"]))
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_native_resource_ownership_retry_and_recorded_reexecution(
    tmp_path: Path,
    case: dict[str, object],
    backend: str,
    record_property: Callable[[str, object], None],
) -> None:
    ctx = context()
    initial = campaign(ctx.resources)
    stats = approved(ctx, campaign_id=initial["id"])
    recipient_stats = approved(ctx, actor_id="b", campaign_id=initial["id"])
    given = inputs(case)
    wanted = expected(case)
    store = open_store(tmp_path, backend=backend)
    service = ResourceService(
        store, ctx.resources, instants=fixed_instants(), seeds=lambda: "1" * 64
    )
    await service.create(initial, load_state(Decimal(str(given["millipounds"])) / 1000))
    genesis = await store.read(initial["id"])
    before = ResourceState.model_validate_json(genesis["resources_json"])
    load = inventory_load(ctx.equipment, ctx.resources, before, "a", stats)
    assert [load.level, load.move, load.dodge] == wanted["before"]
    transfer = Transfer(
        id="give",
        actor_id="a",
        expected_revision=0,
        item_id="mass",
        quantity=int(str(given["transfer_millipounds"])),
        owner_id="b",
        new_item_id="recipient-mass",
    )
    updated = await service.execute(initial["id"], transfer, principal_id="a")
    assert await service.execute(initial["id"], transfer, principal_id="a") == updated
    with pytest.raises(ConflictError):
        await service.execute(
            initial["id"], transfer.model_copy(update={"id": "stale"}), principal_id="a"
        )
    a = inventory_load(ctx.equipment, ctx.resources, updated, "a", stats)
    b = inventory_load(ctx.equipment, ctx.resources, updated, "b", recipient_stats)
    assert [a.level, a.move, a.dodge] == wanted["after_actor"]
    assert [b.level, b.move, b.dodge] == wanted["after_recipient"]
    assert a.weight_pounds == Decimal(str(wanted["actor_weight"])) and b.weight_pounds == Decimal(
        str(wanted["recipient_weight"])
    )
    assert a.weight_pounds + b.weight_pounds == load.weight_pounds
    committed = await store.read(initial["id"])
    assert await store.replay(initial["id"]) == committed
    retained = [r for r in await store.history(initial["id"]) if r.command_id == "give"]
    assert len(retained) == 1 and retained[0].reexecutable
    replay_store = AsyncSQLiteStore(tmp_path / "original-genesis.sqlite")
    await seed_campaign(replay_store, genesis)

    def unavailable() -> str:
        raise AssertionError("Recorded reexecution must not capture a new seed")

    replay_service = ResourceService(replay_store, ctx.resources, seeds=unavailable)
    saved = retained[0]
    assert saved.command_input is not None
    native = COMMAND_ADAPTER.validate_json(json.dumps(replay_payload(saved.command_input)))
    with replay_inputs(saved):
        reexecuted = await replay_service.execute(
            initial["id"], native, principal_id=saved.actor_id
        )
    assert reexecuted == updated
    assert await replay_store.read(initial["id"]) == committed
    original_events = [e.event for e in await store.stream(initial["id"], after=0)]
    replay_events = [e.event for e in await replay_store.stream(initial["id"], after=0)]
    assert replay_events == original_events
    record(case, record_property)


@pytest.mark.parametrize(
    "mutation",
    [None, "missing", "skipped", "failure", "error", "duplicate", "stale", "status-only"],
)
def test_exact_section_evidence_requires_every_case_and_both_ownership_stores(
    tmp_path: Path, mutation: str | None
) -> None:
    import xml.etree.ElementTree as ET

    from wayfarer.certification.executable_evidence import MANIFEST, evaluate_execution

    manifest = json.loads((ROOT / MANIFEST).read_text())
    obligation = next(b for b in manifest["obligations"] if b["identifier"] == SECTION)
    fixture = json.loads((ROOT / FIXTURE).read_text())
    selected = [c for c in fixture["cases"] if c["id"].startswith("basic-b017-")]
    assert len(selected) == 29
    assert set(obligation["case_ids"]) == {c["id"] for c in selected}
    assert {f"basic-b017-load-table-st-{i:02}" for i in range(1, 21)} <= set(obligation["case_ids"])
    manifest["cases"] = [b for b in manifest["cases"] if b["case_id"] in obligation["case_ids"]]
    manifest["obligations"] = [obligation]
    fixture["cases"] = selected
    for path, data in ((MANIFEST, manifest), (FIXTURE, fixture)):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(json.dumps(data))
    by_id = {c["id"]: c for c in selected}
    suite = ET.Element("testsuite")
    for binding in manifest["cases"]:
        for node in binding["tests"]:
            last = "postgres-basic-b017-native-ownership" in node
            if last and mutation == "missing":
                continue
            filename, name = node.split("::", 1)
            test = ET.SubElement(
                suite,
                "testcase",
                classname=filename.removesuffix(".py").replace("/", "."),
                name=name,
            )
            if not (last and mutation == "status-only"):
                properties = ET.SubElement(test, "properties")
                for key, value in provenance(tmp_path, by_id[binding["case_id"]]).items():
                    ET.SubElement(
                        properties,
                        "property",
                        name=key,
                        value="stale"
                        if last and mutation == "stale" and key == "checkout_digest"
                        else value,
                    )
            if last and mutation in ("skipped", "failure", "error"):
                ET.SubElement(test, mutation)
            if last and mutation == "duplicate":
                suite.append(test)
    report = tmp_path / "executed.xml"
    ET.ElementTree(suite).write(report)
    result = evaluate_execution(tmp_path, report)
    assert (SECTION in result.verified) == (mutation is None)
    assert (result.problem(SECTION) is None) == (mutation is None)
    for cap in ("gurps.character.primary_attributes", "gurps.character.secondary_characteristics"):
        assert f"capability:{cap}" not in result.verified
