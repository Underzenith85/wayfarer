"""Real approved B253 mist extinguishes a real approved B246 fire."""

import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, build_runtime, played, seed_play
from test_actions import campaign
from test_lock_spell_persistence import prepare as lock_prepare
from test_lock_spell_persistence import revision

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.fire import package as fire_package
from wayfarer.engine.rules.magic.protocols import AreaSelection
from wayfarer.engine.rules.magic.water import package
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.basic.rows import source
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog, EquipmentProfile
from wayfarer.engine.simulation.equipment.objects import BurnObject, apply_object
from wayfarer.engine.simulation.health.hazards import HazardCommand
from wayfarer.engine.simulation.magic.bindings import SpellChannel, SpellRules
from wayfarer.engine.simulation.magic.spell_state import SpellResult
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, active_spells
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_mist import (
    DeclareWaterScene,
    MistItemFootprint,
    MistPosition,
    MistScene,
    MistSource,
)
from wayfarer.engine.simulation.magic.water_mist_state import PREFIX, MistMaterial
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest
from wayfarer.engine.simulation.resources import EquipmentSpec, Item
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    CombatService,
    EndEncounter,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.water import WaterService
from wayfarer.persistence.replay import verify_commands


async def setup(
    tmp_path: Path,
    backend: str,
    *,
    seeded: bool = False,
    admit: bool = True,
    unsupported: Literal["none", "spent", "sprayer"] = "none",
    ordinary: bool = False,
    fire_radius: int = 1,
) -> tuple[str, PlayService, Campaign]:
    water, fire = package(), fire_package()
    gear = RuleDefinition(
        "equipment:mist-cloth",
        DefinitionKind.EQUIPMENT,
        "Authored cloth",
        "sjg:basic-set-characters-4e-2004",
        0,
        ImplementationStatus.IMPLEMENTED,
    )
    original_id, play = await lock_prepare(
        tmp_path,
        backend,
        combat=True,
        extra_definitions=tuple(
            d for p in (water, fire) for d in p.definitions if d.id.startswith("spell:")
        )
        + (gear,),
        extra_sources=water.sources + fire.sources,
        extra_purchases=tuple(
            Purchase(definition_id="spell:" + key, amount=4)
            for key in (
                "seek-water",
                "purify-water",
                "create-water",
                "destroy-water",
                "ignite-fire",
                "create-fire",
            )
        ),
    )
    original = play._load(await play.store.read(original_id))
    play.engine.rules = play.engine.rules.model_copy(
        update={
            "spells": SpellRules(
                id="mist-fire",
                version=1,
                execution_version=2,
                channels=(
                    SpellChannel(
                        id="fire",
                        actor_id="a",
                        target_id="b",
                        location_id="dock",
                        spell_id="create-fire",
                        area=AreaSelection(center=(2, 1)),
                    ),
                ),
            )
        }
    )
    compiler = play.engine.reviewer.compiler
    profile = ObjectProfile(construction="homogenous", hp=10, dr=0, ht=10)
    spec = EquipmentSpec(definition_id=gear.id, unit_weight=1, stackable=False, durability=profile)
    assert compiler.definitions[gear.id].kind is DefinitionKind.EQUIPMENT
    play.engine.resources.specs[gear.id] = spec
    entry = EquipmentProfile(
        definition_id=gear.id,
        provenance=source(288),
        weight_millipounds=1,
        price=0,
        technology_level=0,
        durability=profile,
    )
    assert play.engine.rules.combat is not None
    play.engine.rules = play.engine.rules.model_copy(
        update={
            "combat": play.engine.rules.combat.model_copy(
                update={
                    "gurps_equipment": EquipmentCatalog(
                        profile_id="gurps-basic-set-4e-2004", entries=(entry,)
                    )
                }
            )
        }
    )
    initial = campaign(play.engine)
    await seed_play(
        play,
        initial,
        original.world,
        original.resources.model_copy(
            update={
                "items": tuple(
                    Item(
                        id=name,
                        definition_id=gear.id,
                        owner_id=owner,
                        condition=ObjectCondition(hp=10, burning=True),
                    )
                    for name, owner in (("inside-cloth", "b"), ("outside-cloth", "a"))
                ),
                "expended_items": (
                    Item(
                        id="spent-fire",
                        definition_id=gear.id,
                        owner_id="b",
                        condition=ObjectCondition(hp=10, burning=True),
                    ),
                )
                if unsupported == "spent"
                else (),
                "hazards": (
                    HazardSchedule(
                        id="sprayer",
                        actor_id="b",
                        spec=HazardSpec(
                            id="sprayer-fire:unsupported",
                            scene_id="dock",
                            kind="fire",
                            delay=100,
                            interval=1,
                            cycles=100,
                            damage_dice=1,
                            damage_add=-1,
                            resistible=False,
                            reference="B433",
                        ),
                        started=0,
                        due=100,
                        remaining=100,
                        ht=10,
                        will=10,
                        swimming=10,
                    ),
                )
                if unsupported == "sprayer"
                else (),
            }
        ),
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=a.proposal, aware_of=("chest", "hidden", "b"))
            for a in original.actors
        ),
        members=(
            CampaignMember(principal_id="a", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="b", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    cid = initial["id"]
    initial = await play.store.read(cid)
    if seeded:
        play.rng = secrets
        play.seeds = lambda: f"{1:064x}"
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fire-fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=2, y=1)),
            ),
        ),
        principal_id="gm",
    )
    start = RuntimeSpellCommand(
        id="fire-start",
        actor_id="a",
        expected_revision=1,
        kind="start",
        spell_id="create-fire",
        cast_id="real-fire",
        channel_id="fire",
        radius=fire_radius,
    )
    if not seeded:
        play.rng = RecordedDice([3] * 100)
    await SpellService(play).execute(cid, start.model_dump(), principal_id="a")
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="b-turn",
            actor_id="b",
            expected_revision=2,
            encounter_id="fire-fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    if not seeded:
        play.rng = RecordedDice([3] * 100)
    await combat.execute(
        cid,
        EndEncounter(
            id="end-fire-fight",
            actor_id="gm",
            expected_revision=3,
            encounter_id="fire-fight",
            reason="Both sides withdraw",
        ),
        principal_id="gm",
    )
    if ordinary:
        fire_spec = ordinary_spec()
        await HazardService(play, lambda *_: HazardContext(fire_spec)).execute(
            cid,
            HazardCommand(
                id="ordinary-enter",
                actor_id="b",
                expected_revision=await revision(play, cid),
                kind="enter",
                hazard_id=fire_spec.id,
            ),
            principal_id="b",
        )
    service = WaterService(play)
    await service.execute(
        cid,
        DeclareWater(
            id="droplet-carrier",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            body=WaterBody(
                object_id="chest",
                location_id="dock",
                position=(2, 1),
                gallons=0,
                pure_gallons=0,
                nature="Authored mist locus",
            ),
        ),
        principal_id="gm",
    )
    resources = play._load(await play.store.read(cid)).resources
    if not admit:
        return cid, play, initial
    sources = tuple(
        MistSource(hazard_id=h.spec.id, cells=((2, 1),))
        for h in resources.hazards
        if h.active and h.spec.kind == "fire"
    )
    await service.execute(
        cid,
        DeclareWaterScene(
            id="scene",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            scene=MistScene(
                id="closed-dock",
                location_id="dock",
                positions=tuple(
                    MistPosition(entity_id=name, point=point)
                    for name, point in (
                        ("a", (1, 1)),
                        ("b", (2, 1)),
                        ("chest", (2, 1)),
                        ("hidden", (4, 4)),
                    )
                ),
                sources=sources,
                item_footprints=(
                    MistItemFootprint(
                        item_id="inside-cloth", definition_id=gear.id, owner_id="b", cells=((2, 1),)
                    ),
                    MistItemFootprint(
                        item_id="outside-cloth",
                        definition_id=gear.id,
                        owner_id="a",
                        cells=((1, 1),),
                    ),
                ),
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        DeclareWaterChannel(
            id="mist-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id="mist",
                actor_id="a",
                location_id="dock",
                plan=WaterPlan(
                    spell_id="create-water", target_id="chest", mist_scene_id="closed-dock"
                ),
            ),
        ),
        principal_id="gm",
    )
    return cid, play, initial


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("success", [True, False])
async def test_real_fire_cast_then_real_mist(tmp_path: Path, backend: str, success: bool) -> None:
    cid, play, _ = await setup(tmp_path, backend)
    service = WaterService(play)
    before = play._load(await play.store.read(cid))
    assert any(e.cast_id == "real-fire" for e in active_spells(before.resources))
    assert any(h.active for h in before.resources.hazards if h.spec.kind == "fire")
    fp = next(p.current for p in before.resources.pools if p.id == "fp:a")
    start = RuntimeSpellCommand(
        id="mist-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="create-water",
        cast_id="mist-cast",
        channel_id="mist",
    )
    await service.execute(cid, start, principal_id="a")
    await play.execute(
        cid,
        Wait(id="wait-mist", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    play.rng = RecordedDice([3, 3, 3] if success else [6, 5, 5])
    complete = start.model_copy(
        update={
            "id": "mist-complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    result = await service.execute(cid, complete, principal_id="a")
    assert isinstance(result, SpellResult)
    assert result.energy_spent == (2 if success else 1)
    state = play._load(await play.store.read(cid))
    assert any(e.cast_id == "real-fire" for e in active_spells(state.resources)) is (not success)
    assert any(h.active for h in state.resources.hazards if h.spec.kind == "fire") is (not success)
    mist = [
        MistMaterial.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith(PREFIX)
    ]
    assert len(mist) == int(success)
    if success:
        assert mist[0].position == (2, 1) and mist[0].gallons == 1
    assert (
        next(p.current for p in state.resources.pools if p.id == "fp:a") == fp - result.energy_spent
    )
    assert latest(state.resources)["chest"].gallons == 0
    assert next(
        i.condition.burning for i in state.resources.items if i.id == "inside-cloth" and i.condition
    ) is (not success)
    assert next(
        i.condition.burning
        for i in state.resources.items
        if i.id == "outside-cloth" and i.condition
    )

    saved = await play.store.read(cid)
    restart = build_play(
        tmp_path, play.engine, backend=backend, store=play.store, rng=RecordedDice(())
    )
    assert await WaterService(restart).execute(cid, complete, principal_id="a") == result
    assert await play.store.read(cid) == saved
    if success:
        with pytest.raises(ValidationError, match="not burning"):
            apply_object(
                restart.engine.resources,
                state.resources,
                BurnObject(
                    id="burn-extinguished",
                    actor_id="b",
                    expected_revision=state.revision,
                    item_id="inside-cloth",
                ),
                system=True,
                rng=RecordedDice(()),
            )
        assert await play.store.read(cid) == saved
        await restart.execute(
            cid,
            Wait(id="after-mist", actor_id="a", expected_revision=state.revision, ticks=2),
            principal_id="a",
        )
        after = restart._load(await play.store.read(cid))
        assert not any(h.active for h in after.resources.hazards if h.spec.kind == "fire")
        assert not any(e.cast_id == "real-fire" for e in active_spells(after.resources))
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_multi_cell_create_fire_cannot_be_wholly_extinguished(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await setup(tmp_path, backend, admit=False, fire_radius=2)
    before = await play.store.read(cid)
    command = scene_command(play, before)
    unique_sources = {s.hazard_id: s for s in command.scene.sources}
    command = command.model_copy(
        update={
            "scene": command.scene.model_copy(update={"sources": tuple(unique_sources.values())})
        }
    )
    with pytest.raises(ValidationError, match="single-cell square fire footprint"):
        await WaterService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_fire_exposure_cannot_be_declared_at_another_source_position(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await setup(tmp_path, backend, admit=False)
    before = await play.store.read(cid)
    command = scene_command(play, before)
    command = command.model_copy(
        update={
            "scene": command.scene.model_copy(
                update={
                    "sources": tuple(
                        s.model_copy(update={"cells": ((1, 1),)}) for s in command.scene.sources
                    )
                }
            )
        }
    )
    with pytest.raises(ValidationError, match="actual source spell"):
        await WaterService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("prefix", ["water-scene:", "water-mist:"])
async def test_genesis_cannot_forge_private_mist_material_or_scene(
    tmp_path: Path, backend: str, prefix: str
) -> None:
    from wayfarer.engine.simulation.resources import ResourceEvent

    cid, play, _ = await setup(tmp_path, backend)
    saved = await play.store.read(cid)
    state = play._load(saved)
    forged = state.resources.model_copy(
        update={
            "revision": 0,
            "events": (ResourceEvent(id=prefix + "forged", at=0, target_id="a", kind="{}"),),
        }
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            forged,
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
    assert await play.store.read(cid) == saved


def scene_command(
    play: PlayService, saved: Campaign, *, cells: tuple[tuple[int, int], ...] = ((2, 1),)
) -> DeclareWaterScene:
    resources = play._load(saved).resources
    return DeclareWaterScene(
        id="replacement-scene",
        actor_id="gm",
        expected_revision=saved["revision"],
        scene=MistScene(
            id="replacement",
            location_id="dock",
            positions=tuple(
                MistPosition(entity_id=name, point=point)
                for name, point in (
                    ("a", (1, 1)),
                    ("b", (2, 1)),
                    ("chest", (2, 1)),
                    ("hidden", (4, 4)),
                )
            ),
            sources=tuple(
                MistSource(hazard_id=h.spec.id, cells=((2, 1),))
                for h in resources.hazards
                if h.active and h.spec.kind == "fire"
            ),
            item_footprints=(
                MistItemFootprint(
                    item_id="inside-cloth",
                    definition_id="equipment:mist-cloth",
                    owner_id="b",
                    cells=cells,
                ),
                MistItemFootprint(
                    item_id="outside-cloth",
                    definition_id="equipment:mist-cloth",
                    owner_id="a",
                    cells=((1, 1),),
                ),
            ),
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("carrier", ["spent", "sprayer"])
async def test_unmodeled_spent_and_moving_fire_reject_real_scene(
    tmp_path: Path, backend: str, carrier: Literal["spent", "sprayer"]
) -> None:
    cid, play, _ = await setup(tmp_path, backend, admit=False, unsupported=carrier)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="expended-item|sprayer clothing"):
        await WaterService(play).execute(cid, scene_command(play, before), principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("missing", [True, False])
async def test_missing_or_multi_cell_burning_object_footprint_rejects_real_scene(
    tmp_path: Path, backend: str, missing: bool
) -> None:
    cid, play, _ = await setup(tmp_path, backend, admit=False)
    before = await play.store.read(cid)
    command = scene_command(play, before, cells=((2, 1), (3, 1)))
    if missing:
        command = command.model_copy(
            update={"scene": command.scene.model_copy(update={"item_footprints": ()})}
        )
    with pytest.raises(ValidationError, match="complete current single-cell footprint"):
        await WaterService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_scene_supersession_rejects_pending_cast_before_dice_and_preserves_campaign(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await setup(tmp_path, backend)
    service = WaterService(play)
    start = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="create-water",
        cast_id="mist-cast",
        channel_id="mist",
    )
    await service.execute(cid, start, principal_id="a")
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    await service.execute(cid, scene_command(play, await play.store.read(cid)), principal_id="gm")
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="no longer current"):
        await service.execute(
            cid,
            start.model_copy(
                update={
                    "id": "complete",
                    "kind": "complete",
                    "expected_revision": before["revision"],
                }
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_scene_authority_and_player_projection(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await setup(tmp_path, backend)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director"):
        await WaterService(play).execute(cid, scene_command(play, before), principal_id="a")
    assert await play.store.read(cid) == before
    view = json.dumps(await build_runtime(play).read(cid, principal_id="a"))
    assert (
        "water-scene:" not in view and "item_footprints" not in view and "water-mist:" not in view
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_full_seeded_history_reexecutes_actual_fire_and_mist(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await setup(tmp_path, backend, seeded=True)
    service = WaterService(play)
    start = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="create-water",
        cast_id="mist-cast",
        channel_id="mist",
    )
    await service.execute(cid, start, principal_id="a")
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    result = await service.execute(
        cid,
        start.model_copy(
            update={
                "id": "complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="a",
    )
    assert result.outcome == "active"
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    final = await play.store.read(cid)
    assert {k: v for k, v in replayed.items() if k != "play_json"} == {
        k: v for k, v in final.items() if k != "play_json"
    }
    assert json.loads(replayed["play_json"]) == json.loads(final["play_json"])


def ordinary_spec() -> HazardSpec:
    return HazardSpec(
        id="ordinary-source",
        scene_id="dock",
        kind="fire",
        delay=1,
        interval=1,
        cycles=100,
        damage_dice=1,
        damage_add=-1,
        resistible=False,
        reference="B433",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ordinary_exposure_deadline_settles_before_atomic_extinction(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await setup(tmp_path, backend, ordinary=True)
    service = WaterService(play)
    start = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="create-water",
        cast_id="mist-cast",
        channel_id="mist",
    )
    await service.execute(cid, start, principal_id="a")
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="Resolve due fire exposure"):
        await service.execute(
            cid,
            start.model_copy(
                update={
                    "id": "premature",
                    "kind": "complete",
                    "expected_revision": before["revision"],
                }
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    play.rng = RecordedDice([1])
    await HazardService(play, lambda *_: HazardContext(ordinary_spec())).execute(
        cid,
        HazardCommand(
            id="ordinary-due",
            actor_id="b",
            expected_revision=await revision(play, cid),
            kind="resolve",
            hazard_id="ordinary-source",
        ),
        principal_id="b",
    )
    play.rng = RecordedDice([3, 3, 3])
    result = await service.execute(
        cid,
        start.model_copy(
            update={
                "id": "complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="a",
    )
    assert result.outcome == "active"
    state = play._load(await play.store.read(cid))
    assert not any(h.active for h in state.resources.hazards if h.spec.kind == "fire")
    hp = next(p.current for p in state.resources.pools if p.id == "hp:b")
    play.rng = RecordedDice(())
    await play.execute(
        cid,
        Wait(id="after-all-fires", actor_id="a", expected_revision=state.revision, ticks=10),
        principal_id="a",
    )
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == hp
    assert not any(h.active for h in after.resources.hazards if h.spec.kind == "fire")
    assert await play.store.read(cid) == await play.store.replay(cid)
