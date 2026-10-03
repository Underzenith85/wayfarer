"""Authenticated current inventory receiver gates all material and casting changes."""

from pathlib import Path

import pytest
from support.runtime import build_play
from support.water_inventory import begin, complete, declare, fixture
from test_actions import campaign
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_lock_spell_persistence import revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_inventory import (
    DeclareInventoryWaterReceiver,
    InventoryWaterReceiver,
)
from wayfarer.engine.simulation.resources import Item, ResourceEvent
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.water import WaterService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_receiver_authority_genesis_and_actual_complete_rollback_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    command = DeclareInventoryWaterReceiver(
        id="physical-receiver",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        receiver=InventoryWaterReceiver(id="wineskin", actor_id="a", item_id="wine-a"),
    )
    before = await play.store.read(cid)
    for principal in ("alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await WaterService(play).execute(cid, command, principal_id=principal)
    assert await play.store.read(cid) == before
    state = play._load(before)
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            state.resources.model_copy(
                update={
                    "events": (
                        ResourceEvent(
                            id="water-inventory:forged", at=0, target_id="wine-a", kind="{}"
                        ),
                    )
                }
            ),
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
    await WaterService(play).execute(cid, command, principal_id="gm")
    start = await begin(play, cid)
    await play.execute(
        cid,
        Wait(id="due-create", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    completion = start.model_copy(
        update={
            "id": "complete-create",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    saved = await play.store.read(cid)
    for principal in ("bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await WaterService(play).execute(cid, completion, principal_id=principal)
    with pytest.raises(ConflictError):
        await WaterService(play).execute(
            cid, completion.model_copy(update={"expected_revision": 0}), principal_id="alice"
        )
    assert await play.store.read(cid) == saved
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await WaterService(failing).execute(cid, completion, principal_id="alice")
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = RecordedDice((3, 3, 3))
    receipt = await WaterService(play).execute(cid, completion, principal_id="alice")
    committed = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await WaterService(restarted).execute(cid, completion, principal_id="alice") == receipt
    assert await play.store.read(cid) == committed == await play.store.replay(cid)
    with pytest.raises(ConflictError):
        await WaterService(restarted).execute(
            cid, completion.model_copy(update={"channel_id": "other"}), principal_id="alice"
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("changed", ["owner", "parent", "ground"])
async def test_changed_receiver_refuses_before_start_rng(
    tmp_path: Path, backend: str, changed: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await declare(play, cid)

    def mutate_item(state: PlayState) -> PlayState:
        item_update = (
            {"owner_id": "b"}
            if changed == "owner"
            else {"container_id": "bag-a"}
            if changed == "parent"
            else {"world_ground_location_id": "dock"}
        )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            item.model_copy(update=item_update) if item.id == "wine-a" else item
                            for item in state.resources.items
                        )
                    }
                )
            }
        )

    await change(play, cid, mutate_item)
    before = await play.store.read(cid)
    with pytest.raises((ValidationError, ConflictError)):
        await begin(play, cid)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_stale_receiver_after_accepted_start_cannot_fill_or_spend(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await declare(play, cid)
    start = await begin(play, cid)
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "resources": s.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"container_id": "bag-a"})
                            if i.id == "wine-a"
                            else i
                            for i in s.resources.items
                        )
                    }
                )
            }
        ),
    )
    await play.execute(
        cid,
        Wait(id="due-stale", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    before = await play.store.read(cid)
    completion = start.model_copy(
        update={
            "id": "stale-complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    play.rng = RecordedDice(())
    with pytest.raises((ValidationError, ConflictError)):
        await WaterService(play).execute(cid, completion, principal_id="alice")
    assert await play.store.read(cid) == before


def test_inventory_plan_is_private_and_rejects_world_variants() -> None:
    assert (
        "inventory_receiver_id"
        not in WaterPlan(spell_id="create-water", target_id="vessel").model_dump()
    )
    for update in (
        {"gallons": 2},
        {"source_id": "source"},
        {"flowing_through_ring": True},
        {"mist_scene_id": "scene"},
        {"excluded_source_ids": ("source",)},
        {"allow_receiver_mixing": True},
    ):
        with pytest.raises(ValueError):
            WaterPlan.model_validate(
                {
                    "spell_id": "create-water",
                    "target_id": "wine-a",
                    "inventory_receiver_id": "wineskin",
                    **update,
                }
            )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("limit", ["owner", "ancestor"])
async def test_full_gallon_capacity_preview_refuses_before_rng_or_fatigue(
    tmp_path: Path, backend: str, limit: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)

    def constrained(state: PlayState) -> PlayState:
        if limit == "owner":
            resources = state.resources.model_copy(
                update={
                    "owners": tuple(
                        owner.model_copy(update={"capacity": 20000})
                        if owner.actor_id == "a"
                        else owner
                        for owner in state.resources.owners
                    )
                }
            )
        else:
            resources = state.resources.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(update={"quantity": 9, "container_id": "bag-a"})
                        if item.id == "blanket-a"
                        else item.model_copy(update={"container_id": "inner-bag"})
                        if item.id == "wine-a"
                        else item
                        for item in state.resources.items
                    )
                    + (
                        Item(
                            id="inner-bag",
                            definition_id="equipment:small-backpack",
                            owner_id="a",
                            container_id="bag-a",
                        ),
                    )
                }
            )
        return state.model_copy(update={"resources": resources})

    await change(play, cid, constrained)
    await declare(play, cid)
    # Channel admission records the intent; actual approved casting previews mass.
    from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
    from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
    from wayfarer.engine.simulation.magic.water_host import DeclareWaterChannel

    await WaterService(play).execute(
        cid,
        DeclareWaterChannel(
            id="capacity-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id="capacity",
                actor_id="a",
                location_id="dock",
                touching=True,
                plan=WaterPlan(
                    spell_id="create-water", target_id="wine-a", inventory_receiver_id="wineskin"
                ),
            ),
        ),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="capacity"):
        await WaterService(play).execute(
            cid,
            RuntimeSpellCommand(
                id="capacity-start",
                kind="start",
                actor_id="a",
                expected_revision=await revision(play, cid),
                spell_id="create-water",
                cast_id="capacity",
                channel_id="capacity",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_filled_receiver_cannot_reuse_empty_proof_or_clone_gallon(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await declare(play, cid)
    start = await begin(play, cid)
    await complete(play, cid, start)
    saved = await play.store.read(cid)
    with pytest.raises(ConflictError, match="empty"):
        await begin(play, cid, identifier="second-fill")
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_completion_late_cas_cannot_spend_or_create_water(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Callable

    from wayfarer.contracts import Campaign, CommandReceipt, TurnResult
    from wayfarer.persistence.events import CommandEntropy, CommandOrigin, CommandResolution

    cid, play, _ = await fixture(tmp_path, backend)
    await declare(play, cid)
    start = await begin(play, cid)
    await play.execute(
        cid,
        Wait(id="cas-due", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    before = play._load(await play.store.read(cid))
    completion = start.model_copy(
        update={"id": "complete-race", "kind": "complete", "expected_revision": before.revision}
    )
    original = play.store.commit_turn
    winning: list[Campaign] = []

    async def race(
        campaign_id: str,
        request_id: str,
        revision_number: int,
        text: str,
        resolve: Callable[[Campaign], CommandReceipt | CommandResolution],
        *,
        actor_id: str = "system",
        entropy: CommandEntropy | None = None,
        recorded_at_us: int | None = None,
        origin: CommandOrigin | None = None,
    ) -> TurnResult:
        if request_id == completion.id:
            await change(play, cid, lambda state: state)
            winning.append(await play.store.read(cid))
        return await original(
            campaign_id,
            request_id,
            revision_number,
            text,
            resolve,
            actor_id=actor_id,
            entropy=entropy,
            recorded_at_us=recorded_at_us,
            origin=origin,
        )

    monkeypatch.setattr(play.store, "commit_turn", race)
    play.rng = RecordedDice((3, 3, 3))
    with pytest.raises(ConflictError):
        await WaterService(play).execute(cid, completion, principal_id="alice")
    assert winning and await play.store.read(cid) == winning[0]
    after = play._load(winning[0])
    assert after.resources == before.resources.model_copy(update={"revision": after.revision})
    assert after.actors == before.actors
    assert await play.store.duplicate(cid, completion.id, "unused") is None
