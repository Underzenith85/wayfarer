"""Current physical collection proof gates the atomic clock and material action."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, build_runtime
from support.water_collection import cast, declare, end_old_flow, fixture
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_lock_spell_persistence import revision

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.water_collection import (
    Collection,
    CollectionReceipt,
    CollectWater,
    DeclareWaterCollection,
)
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.water import WaterService


async def ready(tmp_path: Path, backend: str) -> tuple[str, PlayService, tuple[str, ...], Campaign]:
    cid, play, facts, genesis = await fixture(tmp_path, backend)
    await end_old_flow(play, cid, facts)
    await cast(
        play, cid, "producer", WaterPlan(spell_id="create-water", target_id="vessel", gallons=1)
    )
    await declare(play, cid)
    return cid, play, facts, genesis


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_collection_authority_retry_restart_privacy_and_rollback(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, _ = await ready(tmp_path, backend)
    command = CollectWater(
        id="collect",
        actor_id="a",
        collection_id="measured-collection",
        expected_revision=await revision(play, cid),
    )
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    for principal in ("watcher", "gm"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await WaterService(play).execute(cid, command, principal_id=principal)
    with pytest.raises(ConflictError):
        await WaterService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="alice"
        )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await WaterService(failing).execute(cid, command, principal_id="alice")
    assert (
        await play.store.read(cid) == before
        and await play.store.history(cid) == history
        and await play.store.stream(cid) == stream
    )
    receipt = await WaterService(play).execute(cid, command, principal_id="alice")
    assert isinstance(receipt, CollectionReceipt)
    saved = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await WaterService(restarted).execute(cid, command, principal_id="alice") == receipt
    assert await play.store.read(cid) == saved
    with pytest.raises(ConflictError):
        await WaterService(play).execute(
            cid, command.model_copy(update={"collection_id": "other"}), principal_id="alice"
        )
    with pytest.raises(ConflictError):
        await WaterService(play).execute(
            cid,
            command.model_copy(
                update={"id": "second", "expected_revision": await revision(play, cid)}
            ),
            principal_id="alice",
        )
    for principal in ("alice", "watcher"):
        view = json.dumps(await build_runtime(play).read(cid, principal_id=principal))
        assert "water-collection:" not in view and "collection_seconds" not in view
    assert receipt.source_remaining_gallons == 0 and receipt.assembly_gallons == 3
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "mutation", ["volume", "owner", "height", "width", "clean", "child-volume", "active"]
)
async def test_current_source_receiver_and_measurement_changes_refuse_before_clock(
    tmp_path: Path, backend: str, mutation: str
) -> None:
    from wayfarer.engine.simulation.magic.water_parcels import DeclareWaterParcels, ParcelFlow

    cid, play, facts, _ = await ready(tmp_path, backend)
    if mutation == "volume":
        await cast(
            play,
            cid,
            "more-source",
            WaterPlan(spell_id="create-water", target_id="vessel", gallons=1),
        )
    elif mutation == "owner":
        await change(
            play,
            cid,
            lambda state: state.model_copy(
                update={
                    "world": replace(
                        state.world,
                        entities=tuple(
                            replace(e, owner_id="b") if e.id == "vessel" else e
                            for e in state.world.entities
                        ),
                    )
                }
            ),
        )
    elif mutation == "active":
        await WaterService(play).execute(
            cid,
            DeclareWaterParcels(
                id="fresh-observation",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                flow=ParcelFlow(
                    id="fresh-flow",
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
    else:
        subject, predicate, value = {
            "height": ("vessel", "height_mm", "900"),
            "width": ("vessel", "stream_width_mm", "21"),
            "clean": ("parcel-0", "interior_cleanliness", "dirty"),
            "child-volume": ("parcel-0", "water_volume_gallons", "1"),
        }[mutation]
        await change(
            play,
            cid,
            lambda state: state.model_copy(
                update={
                    "world": replace(
                        state.world,
                        facts=tuple(
                            replace(f, value=value)
                            if (f.subject_id, f.predicate) == (subject, predicate)
                            else f
                            for f in state.world.facts
                        ),
                    )
                }
            ),
        )
    saved = await play.store.read(cid)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ConflictError, ValidationError)):
        await WaterService(play).execute(
            cid,
            CollectWater(
                id="collect",
                actor_id="a",
                collection_id="measured-collection",
                expected_revision=await revision(play, cid),
            ),
            principal_id="alice",
        )
    assert (
        await play.store.read(cid) == saved
        and await play.store.stream(cid) == stream
        and play.rng.exhausted()
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_active_observation_and_player_measurement_authorship_refuse(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.water_parcels import DeclareWaterParcels, ParcelFlow

    cid, play, facts, _ = await fixture(tmp_path, backend)
    await cast(
        play, cid, "producer", WaterPlan(spell_id="create-water", target_id="vessel", gallons=1)
    )
    await WaterService(play).execute(
        cid,
        DeclareWaterParcels(
            id="active-observation",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            flow=ParcelFlow(
                id="active-flow",
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
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="active"):
        await declare(play, cid)
    assert await play.store.read(cid) == before
    state = play._load(before)
    ids = tuple(
        f.id
        for f in state.world.facts
        if f.subject_id in {"vessel", "hidden", "parcel-0", "parcel-1", "parcel-2"}
        and f.id.startswith(("measure:", "collection:"))
    )
    command = DeclareWaterCollection(
        id="fake",
        actor_id="a",
        expected_revision=state.revision,
        collection=Collection(
            id="fake",
            actor_id="a",
            source_id="vessel",
            assembly_id="hidden",
            container_id="parcel-0",
            fact_ids=ids,
        ),
    )
    with pytest.raises((AuthorizationError, ValidationError)):
        await WaterService(play).execute(cid, command, principal_id="alice")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["pause_group", "split_party"])
async def test_actual_party_activity_blocks_collection_atomically(
    tmp_path: Path, backend: str, kind: Literal["pause_group", "split_party"]
) -> None:
    from wayfarer.orchestration.party import PartyCommand, PartyService

    cid, play, facts, _ = await fixture(tmp_path, backend, party=True)
    await end_old_flow(play, cid, facts)
    await cast(
        play, cid, "producer", WaterPlan(spell_id="create-water", target_id="vessel", gallons=1)
    )
    await declare(play, cid)
    await PartyService(play).execute(
        cid,
        PartyCommand(
            id="party-barrier",
            actor_id="a",
            kind=kind,
            target_id="separate" if kind == "split_party" else None,
            expected_revision=await revision(play, cid),
        ),
        principal_id="a",
    )
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    if kind == "split_party":
        assert len(play._load(before).party.groups) == 2
    else:
        assert play._load(before).party.groups[0].paused
    with pytest.raises(ConflictError):
        await WaterService(play).execute(
            cid,
            CollectWater(
                id="blocked-collection",
                actor_id="a",
                collection_id="measured-collection",
                expected_revision=await revision(play, cid),
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before and await play.store.stream(cid) == stream
    if kind == "pause_group":
        await PartyService(play).execute(
            cid,
            PartyCommand(
                id="resume",
                actor_id="a",
                kind="resume_group",
                expected_revision=await revision(play, cid),
            ),
            principal_id="a",
        )
        start = play._load(await play.store.read(cid))
        other_actors = tuple(a for a in start.actors if a.actor_id != "a")
        await WaterService(play).execute(
            cid,
            CollectWater(
                id="allowed-collection",
                actor_id="a",
                collection_id="measured-collection",
                expected_revision=await revision(play, cid),
            ),
            principal_id="alice",
        )
        after = play._load(await play.store.read(cid))
        assert after.resources.game_time == start.resources.game_time + 7
        assert after.party.groups[0].ready_through == after.resources.game_time
        assert tuple(a for a in after.actors if a.actor_id != "a") == other_actors
        assert (
            next(a for a in after.actors if a.actor_id == "a").available_at
            == after.resources.game_time
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_canonical_clock_then_stale_physical_fact_rolls_back(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.engine.rules.checks import RandomSource
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.resources import Advance

    cid, play, _, _ = await ready(tmp_path, backend)
    original = play.advance_clock
    calls: list[int] = []

    def clock(
        state: PlayState,
        command: Advance,
        rng: RandomSource | None = None,
        *,
        run_npcs: bool = True,
    ) -> PlayState:
        advanced = original(state, command, rng, run_npcs=run_npcs)
        calls.append(advanced.resources.game_time)
        return advanced.model_copy(
            update={
                "world": replace(
                    advanced.world,
                    facts=tuple(
                        replace(f, value="blocked") if f.id == "collection:source:path" else f
                        for f in advanced.world.facts
                    ),
                )
            }
        )

    monkeypatch.setattr(play, "advance_clock", clock)
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    with pytest.raises((ConflictError, ValidationError)):
        await WaterService(play).execute(
            cid,
            CollectWater(
                id="clock-stale",
                actor_id="a",
                collection_id="measured-collection",
                expected_revision=before["revision"],
            ),
            principal_id="alice",
        )
    assert calls == [play._load(before).resources.game_time + 7]
    assert await play.store.read(cid) == before and await play.store.stream(cid) == stream


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_collection_late_cas_conflict_preserves_clock_and_material(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Callable

    from wayfarer.contracts import CommandReceipt, TurnResult
    from wayfarer.persistence.events import CommandEntropy, CommandOrigin, CommandResolution

    cid, play, _, _ = await ready(tmp_path, backend)
    before = play._load(await play.store.read(cid))
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
        if request_id == "collect-race":
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
    with pytest.raises(ConflictError):
        await WaterService(play).execute(
            cid,
            CollectWater(
                id="collect-race",
                actor_id="a",
                collection_id="measured-collection",
                expected_revision=before.revision,
            ),
            principal_id="alice",
        )
    assert winning and await play.store.read(cid) == winning[0]
    after = play._load(winning[0])
    assert after.resources.game_time == before.resources.game_time and after.world == before.world
    assert after.resources.events == before.resources.events
    assert await play.store.duplicate(cid, "collect-race", "unused") is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_collection_genesis_receipt_cannot_be_authored(tmp_path: Path, backend: str) -> None:
    from test_actions import campaign

    from wayfarer.engine.simulation.actions import ActorSetup
    from wayfarer.engine.simulation.resources import ResourceEvent

    cid, play, _, _ = await ready(tmp_path, backend)
    saved = await play.store.read(cid)
    state = play._load(saved)
    resources = state.resources.model_copy(
        update={
            "revision": 0,
            "events": (
                ResourceEvent(id="water-collection:forged", at=0, target_id="a", kind="{}"),
            ),
        }
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            resources,
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "predicate,value",
    [
        ("collection_seconds", "0"),
        ("collection_seconds", "1.5"),
        ("capacity_gallons", "0"),
        ("opening_mm", "5"),
    ],
)
async def test_invalid_measured_collection_refuses_before_clock(
    tmp_path: Path, backend: str, predicate: str, value: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await end_old_flow(play, cid, facts)
    await cast(
        play, cid, "producer", WaterPlan(spell_id="create-water", target_id="vessel", gallons=1)
    )
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "world": replace(
                    state.world,
                    facts=tuple(
                        replace(f, value=value)
                        if f.predicate == predicate and f.subject_id in ("vessel", "parcel-0")
                        else f
                        for f in state.world.facts
                    ),
                )
            }
        ),
    )
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    with pytest.raises(ValidationError):
        await declare(play, cid)
    assert await play.store.read(cid) == before and await play.store.stream(cid) == stream


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_clock_new_busy_collector_is_not_overwritten(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.engine.rules.checks import RandomSource
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.resources import Advance

    cid, play, _, _ = await ready(tmp_path, backend)
    original = play.advance_clock
    calls: list[int] = []

    def clock(
        state: PlayState,
        command: Advance,
        rng: RandomSource | None = None,
        *,
        run_npcs: bool = True,
    ) -> PlayState:
        advanced = original(state, command, rng, run_npcs=run_npcs)
        calls.append(advanced.resources.game_time)
        return advanced.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"available_at": advanced.resources.game_time + 1})
                    if a.actor_id == "a"
                    else a
                    for a in advanced.actors
                )
            }
        )

    monkeypatch.setattr(play, "advance_clock", clock)
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    with pytest.raises(ConflictError, match="became unavailable"):
        await WaterService(play).execute(
            cid,
            CollectWater(
                id="clock-busy",
                actor_id="a",
                collection_id="measured-collection",
                expected_revision=before["revision"],
            ),
            principal_id="alice",
        )
    assert calls == [play._load(before).resources.game_time + 7]
    assert await play.store.read(cid) == before and await play.store.stream(cid) == stream
