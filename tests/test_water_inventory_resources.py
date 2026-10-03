"""Actual created water reaches canonical mass, capacity and item lifecycle consumers."""

from pathlib import Path

import pytest
from support.water_inventory import begin, complete, declare, fixture

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.water_inventory import contents_mass, materials
from wayfarer.engine.simulation.resources import Consume, Item, Transfer
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService


async def filled(tmp_path: Path, backend: str) -> tuple[str, PlayService, PlayState]:
    cid, play, _ = await fixture(tmp_path, backend)
    await declare(play, cid)
    start = await begin(play, cid)
    _, outcome = await complete(play, cid, start)
    assert outcome.outcome == "active"
    return cid, play, play._load(await play.store.read(cid))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_created_water_transfers_mass_once_and_cannot_be_consumed(
    tmp_path: Path, backend: str
) -> None:
    cid, play, state = await filled(tmp_path, backend)
    resources, engine = state.resources, play.engine.resources
    assert (
        engine.specs["equipment:wineskin"].unit_weight + contents_mass(resources, "wine-a") == 8250
    )
    before_a, before_b = (
        engine.carried_weight(resources, "a"),
        engine.carried_weight(resources, "b"),
    )
    command = Transfer(
        id="resource-give-water",
        actor_id="a",
        expected_revision=resources.revision,
        item_id="wine-a",
        quantity=1,
        owner_id="b",
    )
    transferred = engine.apply(resources, command)
    assert engine.carried_weight(transferred, "a") == before_a - 8250
    assert engine.carried_weight(transferred, "b") == before_b + 8250
    assert materials(transferred) == materials(resources)
    assert next(i for i in transferred.items if i.id == "wine-a").owner_id == "b"
    assert engine.apply(transferred, command) == transferred
    restored = type(transferred).model_validate_json(transferred.model_dump_json())
    assert engine.apply(restored, command) == transferred
    with pytest.raises(ValidationError, match="material adapter"):
        engine.apply(
            transferred,
            Consume(
                id="resource-consume-water",
                actor_id="b",
                expected_revision=transferred.revision,
                item_id="wine-a",
                quantity=1,
            ),
        )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_full_wineskin_counts_own_contents_and_every_ancestor_capacity(
    tmp_path: Path, backend: str
) -> None:
    _, play, state = await filled(tmp_path, backend)
    resources, engine = state.resources, play.engine.resources
    with pytest.raises(ValidationError, match="Container capacity exceeded"):
        engine.apply(
            resources,
            Transfer(
                id="stuff-full-wineskin",
                actor_id="a",
                expected_revision=resources.revision,
                item_id="bottle-a",
                quantity=1,
                owner_id="a",
                container_id="wine-a",
            ),
        )
    # Canonical backpack capacity40lb: 9 blankets36lb + inner backpack3lb +
    # empty wineskin0.25lb fits. Its actual gallon raises outer contents to47.25lb.
    assert engine.specs["equipment:small-backpack"].unit_weight == 3000
    assert engine.specs["equipment:small-backpack"].container_capacity == 40000
    assert engine.specs["equipment:blanket"].unit_weight == 4000
    nested = resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"quantity": 9, "container_id": "bag-a"})
                if i.id == "blanket-a"
                else i.model_copy(update={"container_id": "inner-bag"})
                if i.id == "wine-a"
                else i
                for i in resources.items
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
    with pytest.raises(ValidationError, match="Container capacity exceeded"):
        engine.validate(nested)
    assert contents_mass(resources, "wine-a") == 8000


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("mutation", ["missing", "expended", "definition"])
async def test_actual_material_requires_its_live_original_item_carrier(
    tmp_path: Path, backend: str, mutation: str
) -> None:
    cid, play, state = await filled(tmp_path, backend)
    item = next(i for i in state.resources.items if i.id == "wine-a")
    items = tuple(i for i in state.resources.items if i.id != item.id)
    update = {"items": items}
    if mutation == "expended":
        update["expended_items"] = state.resources.expended_items + (item,)
    elif mutation == "definition":
        update["items"] = items + (
            item.model_copy(update={"definition_id": "equipment:ceramic-bottle"}),
        )
    with pytest.raises(ValidationError, match="material adapter"):
        play.engine.resources.validate(state.resources.model_copy(update=update))
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_party_transfer_capacity_cas_restart_retry_conserves_water(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import build_play, build_runtime
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.orchestration.party import PartyCommand

    cid, play, state = await filled(tmp_path, backend)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    command = PartyCommand(
        id="give-real-filled-wineskin",
        actor_id="a",
        expected_revision=state.revision,
        kind="transfer_item",
        activity_json=Transfer(
            id="inner-transfer",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="wine-a",
            quantity=1,
            owner_id="b",
        ).model_dump_json(),
    )
    overflow = command.model_copy(
        update={
            "id": "overflow-real-wineskin",
            "activity_json": Transfer(
                id="inner-overflow",
                actor_id="a",
                expected_revision=state.resources.revision,
                item_id="wine-a",
                quantity=1,
                owner_id="a",
                container_id="bottle-a",
            ).model_dump_json(),
        }
    )
    with pytest.raises(ValidationError, match="Container capacity exceeded"):
        await build_runtime(play).submit_json(
            cid, overflow.model_dump(mode="json"), principal_id="alice"
        )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await build_runtime(failing).submit_json(
            cid, command.model_dump(mode="json"), principal_id="alice"
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    receipt = await build_runtime(play).submit_json(
        cid, command.model_dump(mode="json"), principal_id="alice"
    )
    given = play._load(await play.store.read(cid))
    engine = play.engine.resources
    assert (
        engine.carried_weight(given.resources, "a")
        == engine.carried_weight(state.resources, "a") - 8250
    )
    assert (
        engine.carried_weight(given.resources, "b")
        == engine.carried_weight(state.resources, "b") + 8250
    )
    assert materials(given.resources) == materials(state.resources)
    saved = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await build_runtime(restarted).submit_json(
            cid, command.model_dump(mode="json"), principal_id="alice"
        )
        == receipt
    )
    assert await play.store.read(cid) == saved == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_create_and_registered_water_transfer_full_seeded_reexecution(
    tmp_path: Path, backend: str
) -> None:
    import secrets

    from support.runtime import build_runtime, played

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.engine.simulation.events import document
    from wayfarer.orchestration.party import PartyCommand
    from wayfarer.persistence.replay import verify_commands

    cid, play, genesis = await fixture(tmp_path, backend)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await declare(play, cid)
    start = await begin(play, cid)
    _, outcome = await complete(play, cid, start, seeded=True)
    assert outcome.outcome == "active"
    state = play._load(await play.store.read(cid))
    command = PartyCommand(
        id="seeded-transfer-filled-wineskin",
        actor_id="a",
        expected_revision=state.revision,
        kind="transfer_item",
        activity_json=Transfer(
            id="inner-seeded-transfer",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="wine-a",
            quantity=1,
            owner_id="b",
        ).model_dump_json(),
    )
    await build_runtime(play).submit_json(
        cid, command.model_dump(mode="json"), principal_id="alice"
    )
    committed = await play.store.read(cid)
    final = play._load(committed)
    assert next(i for i in final.resources.items if i.id == "wine-a").owner_id == "b"
    assert contents_mass(final.resources, "wine-a") == 8000
    assert play.engine.resources.carried_weight(final.resources, "a") == 19000
    assert play.engine.resources.carried_weight(final.resources, "b") == 13250
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(genesis).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "full-reexecution"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert document(replayed) == document(committed)
