"""Canonical wineskin, approved B253 learning and actual one-gallon casts."""

import secrets
from pathlib import Path

from test_actions import campaign
from test_gurps_melee import setup
from test_lock_spell_persistence import revision

from support.runtime import build_play, seed_play
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.water import package
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.battlefield import Battlefield
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.magic.spell_state import SpellResult
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_inventory import (
    DeclareInventoryWaterReceiver,
    InventoryWaterReceiver,
)
from wayfarer.engine.simulation.resources import Item
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.water import WaterService


async def fixture(path: Path, backend: str) -> tuple[str, PlayService, Campaign]:
    entries = tuple(
        e
        for e in BASIC_EQUIPMENT.entries
        if e.definition_id
        in {
            "equipment:wineskin",
            "equipment:blanket",
            "equipment:small-backpack",
            "equipment:ceramic-bottle",
        }
    )
    base_id, original = await setup(
        path / "base",
        "gurps-basic-set-4e-2004",
        allow_supernatural=True,
        start_encounter=False,
        scene_bound=True,
        battlefield=Battlefield(id="dock", location_id="dock", width=10, height=3),
        extra_definitions=package().definitions,
        extra_purchases=(
            Purchase(definition_id="trait:magery-0"),
            Purchase(definition_id="trait:magery", amount=2),
            *(
                Purchase(definition_id="spell:" + s, amount=4)
                for s in ("seek-water", "purify-water", "create-water")
            ),
        ),
        extra_equipment=entries,
        preserve_extra_equipment=True,
        extra_items=(
            Item(id="wine-a", definition_id="equipment:wineskin", owner_id="a"),
            Item(id="blanket-a", definition_id="equipment:blanket", owner_id="a", quantity=3),
            Item(id="bag-a", definition_id="equipment:small-backpack", owner_id="a"),
            Item(id="bottle-a", definition_id="equipment:ceramic-bottle", owner_id="a"),
        ),
    )
    source = original._load(await original.store.read(base_id))
    play = build_play(path / "campaign", original.engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(play.engine)
    await seed_play(
        play,
        initial,
        source.world,
        source.resources.model_copy(
            update={
                "revision": 0,
                "events": (),
                "items": tuple(
                    i.model_copy(update={"equipped": False, "ready": False})
                    for i in source.resources.items
                ),
            }
        ),
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=a.proposal, aware_of=a.aware_of)
            for a in source.actors
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
            CampaignMember(principal_id="watcher", role="spectator"),
        ),
    )
    return initial["id"], play, await play.store.read(initial["id"])


async def declare(
    play: PlayService,
    cid: str,
    *,
    receiver_id: str = "wineskin",
    item_id: str = "wine-a",
    actor_id: str = "a",
) -> DeclareInventoryWaterReceiver:
    command = DeclareInventoryWaterReceiver(
        id="receiver:" + receiver_id,
        actor_id="gm",
        expected_revision=await revision(play, cid),
        receiver=InventoryWaterReceiver(id=receiver_id, actor_id=actor_id, item_id=item_id),
    )
    await WaterService(play).execute(cid, command, principal_id="gm")
    return command


async def begin(
    play: PlayService,
    cid: str,
    *,
    identifier: str = "create",
    receiver_id: str = "wineskin",
    item_id: str = "wine-a",
    actor_id: str = "a",
) -> RuntimeSpellCommand:
    await WaterService(play).execute(
        cid,
        DeclareWaterChannel(
            id="channel:" + identifier,
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id="channel:" + identifier,
                actor_id=actor_id,
                location_id="dock",
                touching=True,
                plan=WaterPlan(
                    spell_id="create-water", target_id=item_id, inventory_receiver_id=receiver_id
                ),
            ),
        ),
        principal_id="gm",
    )
    command = RuntimeSpellCommand(
        id="start:" + identifier,
        kind="start",
        actor_id=actor_id,
        expected_revision=await revision(play, cid),
        cast_id=identifier,
        spell_id="create-water",
        channel_id="channel:" + identifier,
    )
    await WaterService(play).execute(
        cid, command, principal_id="alice" if actor_id == "a" else "bob"
    )
    return command


async def complete(
    play: PlayService,
    cid: str,
    start: RuntimeSpellCommand,
    *,
    success: bool = True,
    seeded: bool = False,
) -> tuple[RuntimeSpellCommand, SpellResult]:
    await play.execute(
        cid,
        Wait(
            id="wait:" + start.cast_id,
            actor_id=start.actor_id,
            expected_revision=await revision(play, cid),
            ticks=1,
        ),
        principal_id=start.actor_id,
    )
    if not seeded:
        play.rng = RecordedDice((3, 3, 3) if success else (6, 6, 4))
    command = start.model_copy(
        update={
            "id": "complete:" + start.cast_id,
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    result = await WaterService(play).execute(
        cid, command, principal_id="alice" if start.actor_id == "a" else "bob"
    )
    assert isinstance(result, SpellResult)
    if not seeded:
        play.rng = RecordedDice(())
    return command, result


def seeded(play: PlayService) -> None:
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
