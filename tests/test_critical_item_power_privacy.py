"""Actual critical enchanting protects unknown B481 rating in both read projections."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_campaign
from test_actions import campaign
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_haste_manufacture import begin_project, paid_cast
from test_haste_manufacture_power_composition import _canonical_campaign, power_project
from test_haste_manufacture_power_composition import paid_cast as named_paid_cast
from test_haste_manufacture_power_composition import prepare as blueprint

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand
from wayfarer.engine.simulation.magic.haste_state import HasteMana, ObserveHasteMana
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.resources import ResourceEvent, Transfer
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.party import PartyCommand
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.projections import ViewRequest, project
from wayfarer.orchestration.size_forms import SizeFormService
from wayfarer.persistence.replay import verify_commands


async def prepare(path: Path, backend: str, reduction: int) -> tuple[str, PlayService, Campaign]:
    _, original, initial = await blueprint(path / "blueprint", backend, reduction)
    foundation = original._load(initial)
    engine = ActionEngine(
        original.engine.reviewer,
        original.engine.resources,
        original.engine.rules.model_copy(
            update={
                "scenes": SceneRules(
                    id="workshop-scene",
                    version=1,
                    scenes=(Scene(id="dock", version=1, location_id="dock", title="Workshop"),),
                )
            }
        ),
    )
    play = build_play(path / "host", engine, backend=backend, rng=secrets)
    play.seeds = lambda: f"{1:064x}"
    genesis = campaign(engine)
    state = play.initial_state(
        genesis,
        foundation.world,
        foundation.resources,
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=a.proposal, body=a.body)
            for a in foundation.actors
        ),
        members=foundation.members,
    )
    genesis["play_json"] = state.model_dump_json()
    genesis = await seed_campaign(play.store, genesis)
    await HasteService(play).execute(
        genesis["id"],
        ObserveHasteMana(
            id="mana",
            actor_id="gm",
            expected_revision=0,
            environment=HasteMana(location_id="dock", mana="normal"),
        ),
        principal_id="gm",
    )
    return genesis["id"], play, genesis


def assert_private(
    play: PlayService, state: PlayState, rating: int, *, hidden: bool, owner: str = "a"
) -> None:
    binding = next(i for i in state.resources.items if i.id == "cloak").enchantments[0]
    assert binding.power == rating and binding.power_reduction == 0
    for member in state.members:
        for name in ("campaign", "stream"):
            view = project(name, ViewRequest(build_runtime(play), state, member))
            inventory = view.get("inventory", ())
            assert isinstance(inventory, (tuple, list))
            item = next(
                (
                    validation.mapping(i)
                    for i in inventory
                    if validation.mapping(i)["id"] == "cloak"
                ),
                None,
            )
            if owner in member.actor_ids:
                assert item is not None
                bindings = item["enchantments"]
                assert isinstance(bindings, (tuple, list))
                visible = validation.mapping(bindings[0])
                assert visible["id"] == binding.id and visible["power_reduction"] == 0
                expected = binding.model_dump(mode="json")
                if hidden:
                    expected.pop("power")
                    assert "power" not in visible
                else:
                    assert visible["power"] == rating
                assert visible == expected
            else:
                assert item is None
            assert "enchantment-power:" not in json.dumps(view)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("critical_spell", ["haste", "power"])
async def test_critical_actual_settlement_use_transfer_restart_and_whole_seed(
    tmp_path: Path, backend: str, critical_spell: str
) -> None:
    cid, play, initial = await prepare(tmp_path, backend, 1)
    settle = await begin_project(play, cid, 1)
    # Actual source RNG: attack-equivalent enchant check 1,1,1, then bonus 6,5.
    haste_hidden = critical_spell == "haste"
    rating = 28 if haste_hidden else 17
    play.rng, play.seeds = secrets, lambda: f"{270 if haste_hidden else 1:064x}"
    result = await EnchantmentService(play).execute(cid, settle, principal_id="gm")
    assert result.status == "completed" and result.check is not None
    if haste_hidden:
        assert result.check.dice == (1, 1, 1)
    state = play._load(await play.store.read(cid))
    assert_private(play, state, rating, hidden=haste_hidden)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await EnchantmentService(restarted).execute(cid, settle, principal_id="gm") == result
    with pytest.raises(ConflictError):
        await EnchantmentService(restarted).execute(
            cid, settle.model_copy(update={"work_id": "changed"}), principal_id="gm"
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.seeds = lambda: f"{1:064x}"
    await paid_cast(play, cid, 1)
    state = play._load(await play.store.read(cid))
    assert_private(play, state, rating, hidden=haste_hidden)
    power_settle = await power_project(play, cid, 1)
    play.seeds = lambda: f"{1 if haste_hidden else 270:064x}"
    await EnchantmentService(play).execute(cid, power_settle, principal_id="gm")
    play.seeds = lambda: f"{1:064x}"
    state = play._load(await play.store.read(cid))
    for name in ("campaign", "stream"):
        view = await build_runtime(play).project(name, cid, principal_id="alice")
        visible = validation.mapping(
            validation.sequence(validation.decode(json.dumps(view["inventory"])))[0]
        )
        bindings = validation.sequence(visible["enchantments"])
        if haste_hidden:
            assert "power" not in validation.mapping(bindings[0])
            assert validation.mapping(bindings[1])["power"] == 17
        else:
            assert validation.mapping(bindings[0])["power"] == 17
            assert "power" not in validation.mapping(bindings[1])
        assert validation.mapping(bindings[1])["power_reduction"] == 1
    await named_paid_cast(play, cid, "after-power")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["after-power"]
    assert effect.skill == rating and effect.cost == 1 and effect.maintenance == 0
    for operation in ("drop", "retrieve"):
        current = play._load(await play.store.read(cid))
        await SizeFormService(play).retrieve(
            cid,
            WorldGroundCommand(
                id="cloak-" + operation,
                actor_id="a",
                expected_revision=current.resources.revision,
                kind=operation,
                item_id="cloak",
            ),
            principal_id="alice",
        )
    state = play._load(await play.store.read(cid))
    # Ownership transfer is an actual canonical command, not discovery.
    transfer = PartyCommand(
        id="give-enchanted-cloak",
        actor_id="a",
        expected_revision=state.revision,
        kind="transfer_item",
        activity_json=Transfer(
            id="inner-give",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="cloak",
            quantity=1,
            owner_id="b",
        ).model_dump_json(),
    )
    await build_runtime(play).submit_json(
        cid, transfer.model_dump(mode="json"), principal_id="alice"
    )
    final = await play.store.read(cid)
    state = play._load(final)
    assert_private(play, state, rating, hidden=haste_hidden, owner="b")
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
    assert final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dice,rating,hidden", [((1, 1, 1, 2, 3), 22, True), ((3, 3, 3), 17, False)]
)
async def test_actual_rating_visibility_and_atomic_authority(
    tmp_path: Path, backend: str, dice: tuple[int, ...], rating: int, hidden: bool
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, 1)
    settle = await begin_project(play, cid, 1)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    rng = RecordedDice(())
    play.rng = rng
    for principal in ("alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await EnchantmentService(play).execute(cid, settle, principal_id=principal)
    with pytest.raises(ConflictError):
        await EnchantmentService(play).execute(
            cid, settle.model_copy(update={"expected_revision": 0}), principal_id="gm"
        )
    assert rng.exhausted() and await play.store.read(cid) == before
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(dice))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await EnchantmentService(failing).execute(cid, settle, principal_id="gm")
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = RecordedDice(dice)
    await EnchantmentService(play).execute(cid, settle, principal_id="gm")
    assert play.rng.exhausted()
    assert_private(play, play._load(await play.store.read(cid)), rating, hidden=hidden)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_private_critical_event_cannot_be_seeded(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await prepare(tmp_path, backend, 1)
    state = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            state.resources.model_copy(
                update={
                    "revision": 0,
                    "events": (
                        ResourceEvent(
                            id="enchantment-power:forged", at=0, target_id="made", kind="2,3"
                        ),
                    ),
                }
            ),
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_historical_critical_record_views_do_not_change_checkpoint_or_commands(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, 1)
    settle = await begin_project(play, cid, 1)
    play.rng = RecordedDice((1, 1, 1, 2, 3))
    before = await play.store.read(cid)
    service = EnchantmentService(play)
    # Actual older captured settlement, using the canonical transaction. No new
    # producer/command flag is needed to protect an already-persisted binding.
    await submit(
        play,
        cid,
        service.plan(
            play,
            play._load(before),
            settle,
            principal_id="gm",
            correct_settlement=False,
            correct_energy=False,
        ),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    records, events = await play.store.history(cid), await play.store.stream(cid)
    captured = await play.store.command_input(cid, settle.id)
    assert captured is not None and captured.text is not None
    assert "enchantment_settlement_generation" not in captured.text
    assert "enchantment_energy_generation" not in captured.text
    assert "privacy_generation" not in captured.text
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    state = restarted._load(saved)
    assert_private(restarted, state, 22, hidden=True)
    for principal in ("alice", "bob", "watcher", "gm"):
        for name in ("campaign", "stream"):
            await build_runtime(restarted).project(name, cid, principal_id=principal)
    outcome = await EnchantmentService(restarted).execute(cid, settle, principal_id="gm")
    assert outcome.status == "completed"
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
    assert await play.store.history(cid) == records and await play.store.stream(cid) == events
    assert await play.store.command_input(cid, settle.id) == captured
