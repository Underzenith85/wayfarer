"""Actual permanent enchantment and temporary physical Magelock producers."""

import secrets
from dataclasses import replace
from pathlib import Path

from test_actions import campaign
from test_lock_spell_persistence import cast, declare, prepare

from support.analyze_magic import fixture as permanent_fixture
from support.analyze_magic import revision
from support.runtime import build_play, build_runtime, seed_campaign
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.magic.knowledge import package
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.magic.detect_magic_state import (
    CompleteDetectMagic,
    DetectResult,
    DetectSubject,
    ObserveDetectMagicSubject,
    StartDetectMagic,
    WorkDetectMagic,
)
from wayfarer.engine.simulation.magic.haste_state import HasteMana, ObserveHasteMana
from wayfarer.engine.simulation.resources import Transfer
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.orchestration.detect_magic import DetectMagicService
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.party import PartyCommand
from wayfarer.orchestration.play import PlayService


async def fixture(
    path: Path, backend: str, carrier: str = "permanent"
) -> tuple[str, PlayService, Campaign]:
    if carrier in ("permanent", "mundane"):
        cid, play, original = await permanent_fixture(path, backend)
        if carrier == "mundane":
            state = play._load(await play.store.read(cid))
            await build_runtime(play).submit_json(
                cid,
                PartyCommand(
                    id="give-mundane",
                    actor_id="a",
                    expected_revision=state.revision,
                    kind="transfer_item",
                    activity_json=Transfer(
                        id="give-mundane-inner",
                        actor_id="a",
                        expected_revision=state.resources.revision,
                        item_id="workshop",
                        quantity=1,
                        owner_id="c",
                    ).model_dump_json(),
                ).model_dump(mode="json"),
                principal_id="alice",
            )
        return cid, play, original
    knowledge = package()
    cid, foundation_play = await prepare(
        path / "blueprint",
        backend,
        extra_definitions=tuple(d for d in knowledge.definitions if d.id == "spell:detect-magic"),
        extra_sources=knowledge.sources,
        extra_purchases=(Purchase(definition_id="spell:detect-magic", amount=1),),
    )
    foundation = foundation_play._load(await foundation_play.store.read(cid))
    world = replace(
        foundation.world,
        entities=foundation.world.entities
        + (Entity("c", EntityKind.ACTOR, "Cora", location_id="dock"),),
    )
    base = foundation_play.engine.resources
    engine = ActionEngine(
        foundation_play.engine.reviewer,
        base.for_world(world),
        foundation_play.engine.rules,
    )
    play = build_play(path / "host", engine, backend=backend, rng=secrets)
    initial = campaign(engine)
    source = foundation.resources.model_copy(
        update={
            "owners": foundation.resources.owners
            + (foundation.resources.owners[0].model_copy(update={"actor_id": "c"}),),
            "pools": foundation.resources.pools
            + tuple(
                p.model_copy(update={"id": p.id.split(":")[0] + ":c"})
                for p in foundation.resources.pools
                if p.id in ("hp:a", "fp:a")
            ),
        }
    )
    state = play.initial_state(
        initial,
        world,
        source,
        (
            ActorSetup(actor_id="a", proposal=foundation.actors[0].proposal, aware_of=("chest",)),
            ActorSetup(actor_id="c", proposal=foundation.actors[0].proposal, aware_of=("chest",)),
        ),
        members=foundation.members
        + (
            CampaignMember(principal_id="cora", role="player", actor_ids=("c",)),
            CampaignMember(principal_id="watcher", role="spectator"),
        ),
    )
    initial["play_json"] = state.model_dump_json()
    original = await seed_campaign(play.store, initial)
    cid = original["id"]
    play.seeds = lambda: f"{1:064x}"
    await declare(play, cid)
    await HasteService(play).execute(
        cid,
        ObserveHasteMana(
            id="mana",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            environment=HasteMana(location_id="dock", mana="normal"),
        ),
        principal_id="gm",
    )
    await cast(play, cid, "magelock", "actual-temporary", dice=None)
    return cid, play, original


async def start(
    play: PlayService, cid: str, *, name: str = "detection", carrier: str = "permanent"
) -> None:
    actor, target, kind = (
        ("c", "chest", "lock")
        if carrier == "temporary"
        else ("c", "cloak" if carrier == "permanent" else "workshop", "inventory")
    )
    service = DetectMagicService(play)
    await service.execute(
        cid,
        ObserveDetectMagicSubject(
            id=name + "-subject",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=DetectSubject.model_validate(
                dict(
                    id=name + "-physical",
                    caster_id=actor,
                    target_id=target,
                    carrier=kind,
                    backfire="injury-one",
                )
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        StartDetectMagic(
            id=name + "-start",
            actor_id=actor,
            expected_revision=await revision(play, cid),
            cast_id=name,
            subject_id=name + "-physical",
        ),
        principal_id="alice" if actor == "a" else "cora",
    )


async def work(play: PlayService, cid: str, *, name: str = "detection", actor: str = "c") -> None:
    await DetectMagicService(play).execute(
        cid,
        WorkDetectMagic(
            id=name + "-work",
            actor_id=actor,
            expected_revision=await revision(play, cid),
            cast_id=name,
            seconds=5,
        ),
        principal_id="alice" if actor == "a" else "cora",
    )


async def complete(
    play: PlayService, cid: str, seed: int, *, name: str = "detection", actor: str = "c"
) -> DetectResult:
    play.seeds = lambda: f"{seed:064x}"
    return await DetectMagicService(play).execute(
        cid,
        CompleteDetectMagic(
            id=name + "-complete",
            actor_id=actor,
            expected_revision=await revision(play, cid),
            cast_id=name,
        ),
        principal_id="alice" if actor == "a" else "cora",
    )
