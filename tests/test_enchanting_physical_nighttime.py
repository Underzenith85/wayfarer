"""B353's four-second Lift fits within B481's actual nightly rest interval."""

import asyncio
import json
from dataclasses import asdict, replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_enchanting_nighttime import advance_work, light
from test_enchanting_noncombat_nighttime import configured as blueprint
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_statistics import profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.skills.mundane.physical_outdoors import (
    definitions as outdoor_definitions,
)
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.equipment.catalog import (
    EquipmentCatalog,
    EquipmentProfile,
    Provenance,
)
from wayfarer.engine.simulation.magic.enchanting_calendar import enchanting_work_active
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    BeginEnchanting,
    CreateEnchantment,
)
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.physical import PhysicalCommand, PhysicalRoute, PhysicalService
from wayfarer.orchestration.physical_generations import decode, encode
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.replay import verify_commands


async def configured(
    path: Path,
    backend: str = "sqlite",
    *,
    enroll: bool = True,
    society_knows_swimming: bool = False,
) -> tuple[Campaign, PlayService, PlayState]:
    # Source spell admission and the Staff project use the reviewed blueprint.
    # Swimming's separate explicit opt-in pins the real Characters B224 skill.
    _, original, foundation = await blueprint(path / "blueprint", enroll=enroll)
    reviewer = original.engine.reviewer
    if society_knows_swimming:
        old = reviewer.compiler
        swimming = next(d for d in outdoor_definitions() if d.id == "skill:swimming")
        base = profile_package("gurps-basic-set-4e-2004")
        package = replace(
            base,
            sources=base.sources
            + tuple(
                s for s in enchantment_package().sources if s.id not in {p.id for p in base.sources}
            ),
            definitions=(*old.definitions.values(), swimming),
        )
        catalog = RulesCatalog((package,))
        compiler = CharacterCompiler(
            catalog,
            replace(old.rules, packages=(PackagePin(package.id, package.version, package.digest),)),
            old.policy,
            statistics_profile="gurps-basic-set-4e-2004",
        )
        reviewer = PowerReviewer(compiler, reviewer.policy, reviewer.gm_ids)
    equipment = EquipmentCatalog(
        profile_id="gurps-basic-set-4e-2004",
        entries=tuple(
            EquipmentProfile(
                definition_id=spec.definition_id,
                provenance=Provenance(
                    source_id="sjg:basic-set-characters-4e-2004",
                    edition="Fourth Edition, third printing (February 2008)",
                    pages=(288,),
                    errata="none",
                ),
                weight_millipounds=spec.unit_weight,
                price=0,
                technology_level=0,
            )
            for spec in original.engine.resources.specs.values()
        ),
    )
    resources = (
        ResourceEngine(
            foundation.world,
            catalog,
            compiler.rules,
            compiler.policy,
            tuple(original.engine.resources.specs.values()),
        )
        if society_knows_swimming
        else original.engine.resources.for_world(foundation.world)
    )
    resources.specs = {entry.definition_id: entry.inventory_spec() for entry in equipment.entries}
    engine = ActionEngine(
        reviewer,
        resources,
        original.engine.rules.model_copy(
            update={"combat": CombatRules(id="physical", version=1, gurps_equipment=equipment)}
        ),
    )
    play = build_play(path, engine, backend=backend, seeds=lambda: f"{1:064x}")
    initial = campaign(engine)
    actors = foundation.actors
    genesis = play.initial_state(
        initial,
        foundation.world,
        foundation.resources,
        tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal, body=a.body) for a in actors),
    )
    initial["play_json"] = genesis.model_dump_json()
    await seed_campaign(play.store, initial)
    if society_knows_swimming:
        await DefaultKnowledgeService(play).execute(
            initial["id"],
            AdoptDefaultKnowledge(
                id="swimming-society-knowledge",
                actor_id="gm",
                expected_revision=genesis.revision,
                performer_id="a",
                known_skill_ids=("skill:swimming",),
                reason="The GM establishes Swimming familiarity before this scenario's enchanting work",
            ),
            principal_id="gm",
        )
    if enroll:
        service = EnchantmentService(play)
        await service.declare_staff(
            initial["id"],
            DeclareStaffConstruction(
                id="staff-fact",
                actor_id="a",
                expected_revision=play._load(await play.store.read(initial["id"])).revision,
                construction=StaffConstruction(
                    item_id="blade",
                    definition_id="equipment:sword",
                    form="wand",
                    length_yards=Fraction(1),
                    material="reed",
                    once_living=True,
                ),
            ),
            principal_id="gm",
        )
        await service.execute(
            initial["id"],
            CreateEnchantment(
                id="create-staff",
                actor_id="a",
                expected_revision=play._load(await play.store.read(initial["id"])).revision,
                project_id="staff-project",
                recipe_id="source-staff",
                target_item_id="blade",
                enchanter_ids=("a", "b"),
            ),
            principal_id="gm",
        )
        await service.execute(
            initial["id"],
            BeginEnchanting(
                id="work-staff",
                actor_id="a",
                expected_revision=play._load(await play.store.read(initial["id"])).revision,
                project_id="staff-project",
            ),
            principal_id="gm",
        )
    return initial, play, genesis


def route(pounds: int = 160) -> PhysicalRoute:
    return PhysicalRoute(
        id="lift-stone", scene_id="forge", kind="lift", lift_kind="two-hand", pounds=Decimal(pounds)
    )


async def command(play: PlayService, cid: str) -> PhysicalCommand:
    return PhysicalCommand(
        id="lift",
        actor_id="a",
        expected_revision=play._load(await play.store.read(cid)).revision,
        kind="lift",
        route_id="lift-stone",
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("at", [28800, 86396])
async def test_real_four_second_lift_preserves_work_and_reexecutes(
    tmp_path: Path, backend: str, at: int
) -> None:
    initial, play, genesis = await configured(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, at)
    before = play._load(await play.store.read(cid))
    project = before.resources.enchantment_projects[0]
    assert project.active_work and project.active_work.due == 1238400
    assert not enchanting_work_active(before.resources, project)
    lift = await command(play, cid)
    service = PhysicalService(play, lambda *_: route())
    first, duplicate = await asyncio.gather(
        *(service.execute(cid, lift, principal_id="a") for _ in range(2))
    )
    assert first == duplicate
    assert first.succeeded and first.completed and first.capacity == "160"
    assert first.seconds == 4 and first.progress == "1" and first.elapsed == 4
    assert first.fp_lost == 0 and first.injury == 0 and not first.checks
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == at + 4
    assert state.resources.enchantment_projects == before.resources.enchantment_projects
    assert [(p.id, p.current, p.maximum) for p in state.resources.pools] == [
        (p.id, p.current, p.maximum) for p in before.resources.pools
    ]
    stored = await play.store.command_input(cid, lift.id)
    assert stored and stored.text
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict) and payload["enchanting_rest_generation"] == 1
    assert decode(str(payload["physical_route"])) == route()

    def unavailable(*_: object) -> PhysicalRoute:
        raise AssertionError("retry must reuse captured route")

    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await PhysicalService(restarted, unavailable).execute(cid, lift, principal_id="a") == first
    )
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=genesis.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("at", [0, 86397, 86400])
async def test_lift_does_not_enter_active_shift_before_dice_or_time(
    tmp_path: Path, at: int
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    if at:
        await advance_work(play, cid, at)
    before = await play.store.read(cid)
    play.rng = dice = RecordedDice(())
    with pytest.raises(ConflictError, match="enchanting work|enchanting work shift"):
        await PhysicalService(play, lambda *_: route()).execute(
            cid, await command(play, cid), principal_id="a"
        )
    assert dice.exhausted() and before == await play.store.read(cid)


async def test_lift_refuses_authority_stale_pending_cast_and_old_unmarked_rest(
    tmp_path: Path,
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    lift = await command(play, cid)
    service = PhysicalService(play, lambda *_: route())
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await service.execute(cid, lift, principal_id="b")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, lift.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    # Historical plans do not silently gain the new admission.
    with pytest.raises(ConflictError, match="enchanting work"):
        await submit(play, cid, service.plan(play, lift, principal_id="a"), principal_id="a")
    assert before == await play.store.read(cid)
    await SpellService(play).execute(
        cid, light("pending", lift.expected_revision), principal_id="a"
    )
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="concentrat|cast"):
        await service.execute(cid, await command(play, cid), principal_id="a")
    assert before == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_lift_and_checkpoint_rollback_preserve_capacity_and_cost(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await configured(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    lift = await command(play, cid)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await submit(
            failing,
            cid,
            PhysicalService(failing, lambda *_: route()).plan(
                failing, lift, principal_id="a", route=route()
            ),
            principal_id="a",
        )
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and stream == await play.store.stream(cid)
    failed = await PhysicalService(play, lambda *_: route(161)).execute(cid, lift, principal_id="a")
    assert not failed.succeeded and not failed.completed and failed.progress == "0"
    assert failed.capacity == "160" and failed.seconds == 4 and failed.fp_lost == 0
    assert play._load(await play.store.read(cid)).resources.game_time == 28804


def test_private_capture_requires_exact_typed_route() -> None:
    assert decode(encode(route())) == route()
    for value in (
        asdict(route()) | {"unknown": True},
        asdict(route()) | {"seconds": True},
        asdict(route()) | {"kind": "invented"},
    ):
        with pytest.raises(ValidationError, match="captured physical route"):
            decode(json.dumps(value, default=str))


async def test_historical_unmarked_non_enchanter_retry_retains_original_payload(
    tmp_path: Path,
) -> None:
    initial, play, _ = await configured(tmp_path, enroll=False)
    cid = initial["id"]
    lift = await command(play, cid)
    service = PhysicalService(play, lambda *_: route())
    result = await submit(play, cid, service.plan(play, lift, principal_id="a"), principal_id="a")
    stored = await play.store.command_input(cid, lift.id)
    assert stored and stored.text
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict) and "enchanting_rest_generation" not in payload

    def unavailable(*_: object) -> PhysicalRoute:
        raise AssertionError("historical retry must not resolve geometry")

    assert await PhysicalService(play, unavailable).execute(cid, lift, principal_id="a") == result
    assert await play.store.command_input(cid, lift.id) == stored


@pytest.mark.parametrize("corruption", ["future", "missing-route", "unversioned-route"])
async def test_captured_lift_admission_refuses_invalid_recorded_metadata(
    tmp_path: Path, corruption: str
) -> None:
    initial, play, _ = await configured(tmp_path, enroll=False)
    cid = initial["id"]
    lift = await command(play, cid)
    service = PhysicalService(play, lambda *_: route())
    await service.execute(cid, lift, principal_id="a")
    record = next(r for r in await play.store.history(cid) if r.command_id == lift.id)
    assert record.command_input is not None
    payload = replay_payload(record.command_input)
    assert isinstance(payload, dict)
    if corruption == "future":
        payload["enchanting_rest_generation"] = 2
    elif corruption == "missing-route":
        payload.pop("physical_route")
    else:
        payload.pop("enchanting_rest_generation")
        payload.pop("physical_skill_minimum_generation", None)
    before = await play.store.read(cid)
    encoded = json.dumps(payload)
    with replay_inputs(
        replace(record, command_input=encoded, payload_hash=payload_digest({"input": encoded}))
    ):
        with pytest.raises(ValidationError, match="generation|captured route"):
            await service.execute(cid, lift, principal_id="a")
    assert before == await play.store.read(cid)


def jump_route(*, prepared: bool = True) -> PhysicalRoute:
    return PhysicalRoute(
        id="jump-gap",
        scene_id="forge",
        kind="jump",
        jump_kind="broad",
        distance=Decimal(2),
        prepared=prepared,
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("prepared", [True, False])
async def test_night_jump_uses_actual_capacity_preparation_time_and_seeded_replay(
    tmp_path: Path, backend: str, prepared: bool
) -> None:
    initial, play, genesis = await configured(tmp_path, backend)
    cid = initial["id"]
    seconds = 3 if prepared else 1
    # Both preparation variants finish exactly at the next eight-hour shift.
    await advance_work(play, cid, 86400 - seconds)
    before = play._load(await play.store.read(cid))
    jump = PhysicalCommand(
        id="jump",
        actor_id="a",
        expected_revision=before.revision,
        kind="jump",
        route_id="jump-gap",
    )
    service = PhysicalService(play, lambda *_: jump_route(prepared=prepared))
    first, duplicate = await asyncio.gather(
        *(service.execute(cid, jump, principal_id="a") for _ in range(2))
    )
    assert first == duplicate
    # B352 Basic Move5: standing broad jump is7ft, halved without preparation.
    # Native Decimal division preserves its historical sequential rounding;
    # compare the independent rational source result to nine decimal places.
    precision = Decimal("0.000000001")
    assert Decimal(first.capacity).quantize(precision) == (
        Decimal(7) / (3 if prepared else 6)
    ).quantize(precision)
    assert first.succeeded is prepared and first.completed is prepared
    assert first.seconds == seconds and first.elapsed == seconds
    assert first.progress == ("2" if prepared else "0")
    assert first.fp_lost == 0 and first.injury == 0 and not first.checks
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 86400
    assert state.resources.enchantment_projects == before.resources.enchantment_projects
    assert [(p.id, p.current) for p in state.resources.pools] == [
        (p.id, p.current) for p in before.resources.pools
    ]

    def unavailable(*_: object) -> PhysicalRoute:
        raise AssertionError("jump retry must reuse the original trusted route")

    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await PhysicalService(restarted, unavailable).execute(cid, jump, principal_id="a") == first
    )
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=genesis.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("at,prepared", [(0, True), (86400, False), (86398, True)])
async def test_jump_preparation_must_fit_before_any_dice_or_clock(
    tmp_path: Path, at: int, prepared: bool
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    if at:
        await advance_work(play, cid, at)
    before = await play.store.read(cid)
    jump = PhysicalCommand(
        id="jump",
        actor_id="a",
        expected_revision=play._load(before).revision,
        kind="jump",
        route_id="jump-gap",
    )
    play.rng = dice = RecordedDice(())
    with pytest.raises(ConflictError, match="enchanting work|enchanting work shift"):
        await PhysicalService(play, lambda *_: jump_route(prepared=prepared)).execute(
            cid, jump, principal_id="a"
        )
    assert dice.exhausted() and before == await play.store.read(cid)
