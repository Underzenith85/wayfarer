"""Actual B253 producers plus measured complete-vessel collection fixtures."""

import secrets
from dataclasses import replace
from pathlib import Path

from test_actions import campaign
from test_lock_spell_persistence import revision
from test_water_parcel_flow import fixture as parcel_fixture

from support.runtime import build_play, seed_play
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.magic.spell_state import SpellResult
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_collection import Collection, DeclareWaterCollection
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_parcels import DeclareWaterParcels, ParcelFlow
from wayfarer.engine.simulation.magic.water_state import WaterBody
from wayfarer.engine.world import Entity, EntityKind, Fact
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.water import WaterService


async def fixture(
    path: Path, backend: str, *, party: bool = False, impure_source: bool = False
) -> tuple[str, PlayService, tuple[str, ...], Campaign]:
    cid, original, parcel_facts, _ = await parcel_fixture(path / "base", backend)
    state = original._load(await original.store.read(cid))
    added: tuple[Fact, ...] = (
        Fact("collection:source:height", "vessel", "height_mm", "2000"),
        Fact("collection:source:x", "vessel", "position_x_yards", "0"),
        Fact("collection:source:y", "vessel", "position_y_yards", "0"),
        Fact("collection:source:width", "vessel", "stream_width_mm", "10"),
        Fact("collection:source:time", "vessel", "collection_seconds", "7"),
        Fact("collection:source:path", "vessel", "collection_path_clear", "true"),
        Fact("collection:target:opening", "parcel-0", "opening_mm", "20"),
        Fact("collection:target:capacity", "parcel-0", "capacity_gallons", "3"),
        Fact("collection:target:condition", "parcel-0", "condition", "intact"),
        Fact("collection:target:clean", "parcel-0", "interior_cleanliness", "clean"),
    )
    if impure_source:
        added += (
            Fact("collection:source:volume", "vessel", "water_volume_gallons", "1"),
            Fact("collection:source:purity", "vessel", "water_purity", "impure"),
        )
    world = replace(
        state.world,
        entities=state.world.entities
        + (Entity("vessel", EntityKind.OBJECT, "Measured collection source vessel", "dock", "a"),),
        facts=state.world.facts + added,
        knowledge=state.world.knowledge + (("a", "collection:source:height"),),
    )
    engine = original.engine
    if party:
        engine = ActionEngine(
            engine.reviewer,
            engine.resources,
            engine.rules.model_copy(
                update={
                    "scenes": SceneRules(
                        id="collection-scenes",
                        version=1,
                        scenes=(
                            Scene(id="dock-scene", version=1, location_id="dock", title="Dock"),
                        ),
                    ),
                    "party": PartyRules(id="collection-party", version=1),
                }
            ),
        )
    if party:
        world = replace(
            world,
            entities=world.entities + (Entity("companion", EntityKind.ACTOR, "Companion", "dock"),),
        )
    engine = ActionEngine(engine.reviewer, engine.resources.for_world(world), engine.rules)
    play = build_play(path / "collection", engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(play.engine)
    await seed_play(
        play,
        initial,
        world,
        state.resources.model_copy(
            update={
                "revision": 0,
                "events": (),
                "owners": state.resources.owners
                + (
                    (state.resources.owners[0].model_copy(update={"actor_id": "companion"}),)
                    if party
                    else ()
                ),
            }
        ),
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=a.proposal, aware_of=a.aware_of + ("vessel",))
            for a in state.actors
        )
        + ((ActorSetup(actor_id="companion", proposal=state.actors[0].proposal),) if party else ()),
        members=state.members + (CampaignMember(principal_id="watcher", role="spectator"),),
    )
    cid = initial["id"]
    genesis = await play.store.read(cid)
    seed(play)
    for name, gallons, pure, nature in (
        ("hidden", 3, 2, "assembly of separate containers"),
        ("chest", 0, 0, "receiver"),
        ("vessel", 1 if impure_source else 0, 0, "whole measured pouring vessel"),
    ):
        await WaterService(play).execute(
            cid,
            DeclareWater(
                id="body:" + name,
                actor_id="gm",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id=name,
                    location_id="dock",
                    gallons=gallons,
                    pure_gallons=pure,
                    nature=nature,
                    capacity_gallons=5 if name != "vessel" else 3,
                ),
            ),
            principal_id="gm",
        )
    return cid, play, parcel_facts, genesis


async def cast(
    play: PlayService,
    cid: str,
    identifier: str,
    plan: WaterPlan,
    *,
    success: bool = True,
    seeded: bool = False,
) -> SpellResult:
    await WaterService(play).execute(
        cid,
        DeclareWaterChannel(
            id="channel:" + identifier,
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id="flow:" + identifier, actor_id="a", location_id="dock", touching=True, plan=plan
            ),
        ),
        principal_id="gm",
    )
    start = RuntimeSpellCommand(
        id="start:" + identifier,
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id=plan.spell_id,
        cast_id=identifier,
        channel_id="flow:" + identifier,
    )
    await WaterService(play).execute(cid, start, principal_id="alice")
    seconds = plan.gallons * plan.seconds_per_gallon if plan.spell_id == "purify-water" else 1
    for n in range(seconds):
        await play.execute(
            cid,
            Wait(
                id=f"wait:{identifier}:{n}",
                actor_id="a",
                expected_revision=await revision(play, cid),
                ticks=1,
            ),
            principal_id="a",
        )
        if n + 1 < seconds:
            await WaterService(play).execute(
                cid,
                start.model_copy(
                    update={
                        "id": f"concentrate:{identifier}:{n}",
                        "kind": "concentrate",
                        "expected_revision": await revision(play, cid),
                    }
                ),
                principal_id="alice",
            )
    if not seeded:
        play.rng = RecordedDice((3, 3, 3) if success else (6, 6, 4))
    result = await WaterService(play).execute(
        cid,
        start.model_copy(
            update={
                "id": "complete:" + identifier,
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    assert isinstance(result, SpellResult)
    if not seeded:
        play.rng = RecordedDice(())
    return result


async def end_old_flow(
    play: PlayService, cid: str, facts: tuple[str, ...], *, seeded: bool = False
) -> None:
    await WaterService(play).execute(
        cid,
        DeclareWaterParcels(
            id="old-observation",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            flow=ParcelFlow(
                id="old-flow",
                caster_id="a",
                source_id="hidden",
                target_id="chest",
                ring_id="ring",
                container_ids=("parcel-0", "parcel-1", "parcel-2"),
                fact_ids=facts,
            ),
        ),
        principal_id="gm",
    )
    result = await cast(
        play,
        cid,
        "old-purify",
        WaterPlan(
            spell_id="purify-water",
            source_id="hidden",
            target_id="chest",
            gallons=1,
            flowing_through_ring=True,
            parcel_flow_id="old-flow",
        ),
        seeded=seeded,
    )
    assert result.outcome == "active"


async def declare(play: PlayService, cid: str) -> None:
    state = play._load(await play.store.read(cid))
    facts = tuple(
        f.id
        for f in state.world.facts
        if f.subject_id in {"vessel", "hidden", "parcel-0", "parcel-1", "parcel-2"}
        and f.id.startswith(("measure:", "collection:"))
    )
    await WaterService(play).execute(
        cid,
        DeclareWaterCollection(
            id="collection-proof",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            collection=Collection(
                id="measured-collection",
                actor_id="a",
                source_id="vessel",
                assembly_id="hidden",
                container_id="parcel-0",
                fact_ids=facts,
            ),
        ),
        principal_id="gm",
    )


def seed(play: PlayService) -> None:
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
