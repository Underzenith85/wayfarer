"""B66/B136/B414: a pending fragment blast damages the current spent instance."""

import json
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play
from test_area_attacks import grenade
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import scene
from test_opponent_attack_routes import enroll, luck_source
from test_prepared_fragment_attack import responses

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.explosion import ExplosionSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ResolveWeaponExplosion, TakeCombatTurn
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.resources import Equip, Item
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.opponent_fragment_records import (
    ChooseOpponentFragment,
    OpponentFragmentPending,
    PrepareOpponentFragment,
)
from wayfarer.orchestration.opponent_fragment_sources import bind_fragment_source
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.replay import verify_commands


def source_item(state: PlayState) -> Item:
    return next(item for item in state.resources.expended_items if item.id == "sword-a")


async def opened(
    path: Path, backend: str, *, legacy: bool = False, seeded_open: bool = False
) -> tuple[str, PlayService, PrepareOpponentFragment, OpponentFragmentPending, Campaign]:
    definition, purchase = luck_source()
    cid, initial = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1, fragmentation_dice=1),
        durability=ObjectProfile(
            construction="unliving",
            hp=10,
            dr=0,
            ht=10,
            size_modifier=0,
            fragility=("combustible",),
        ),
    )
    play = await enroll(path, backend, cid, initial)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="launch",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
            area_aim_point=GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0),
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play, "b")
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    resolution = ResolveWeaponExplosion(
        id="fragment",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blasts(state.resources)[0].id,
        responses=responses(),
        object_cover=dict(sorted({"sword-a": 0, "sword-b": 100, "shield-b": 100}.items()))
        if legacy
        else {"sword-a": 0, "sword-b": 100, "shield-b": 100},
        object_sizes=dict(sorted({"sword-a": 0, "sword-b": 0, "shield-b": 0}.items()))
        if legacy
        else {"sword-a": 0, "sword-b": 0, "shield-b": 0},
        environment="air",
    )
    command = PrepareOpponentFragment(
        id=resolution.id,
        actor_id="b",
        expected_revision=state.revision,
        resolution=resolution,
        launch_command_id="launch",
    )
    initial_snapshot = await play.store.read(cid)
    play.rng = secrets if seeded_open else RecordedDice((1, 5, 5, 6, 1, 5, 5, 6))
    play.seeds = lambda: "ab" * 32
    if legacy:
        # Reproduce an admitted historical host input with the new private field absent.
        bound = await bind_fragment_source(play, cid, command)
        bound = bound.model_copy(update={"incendiary_objects": False})
        encoded = json.dumps({"operation": "task-host", "command": bound.model_dump(mode="json")})
        prior = replace(
            (await play.store.history(cid))[-1],
            command_input=encoded,
            payload_hash=payload_digest({"input": encoded}),
        )

        async def historical_binding(
            host: PlayService, campaign_id: str, request: PrepareOpponentFragment
        ) -> PrepareOpponentFragment:
            with replay_inputs(prior):
                return await bind_fragment_source(host, campaign_id, request)

        # Admit the old recorded input before the transaction. Only this fixture's
        # first binding uses a historical envelope; later replay uses the real log.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("wayfarer.orchestration.tasks.bind_fragment_source", historical_binding)
            await TaskService(play).execute(cid, command, principal_id="gm")
    else:
        await TaskService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, OpponentFragmentPending) and pending.original
    if not seeded_open:
        assert isinstance(play.rng, RecordedDice)
        assert pending.original.total == 16 and play.rng.exhausted()
    assert pending.preparation.progress.incendiary_objects is not legacy
    item = source_item(state)
    assert item.condition and item.condition.hp == 10
    return cid, play, command, pending, initial_snapshot


async def current_custody(play: PlayService, cid: str) -> None:
    def correct(state: PlayState) -> PlayState:
        current = source_item(state)
        assert current.condition
        updated = current.model_copy(
            update={
                "owner_id": "b",
                "ground": GroundPosition(encounter_id="fight", geometry="grid", x=2, y=0),
                "condition": current.condition.model_copy(update={"hp": 9}),
            }
        )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(update={"expended_items": (updated,)}),
                "actors": tuple(
                    actor.model_copy(update={"approval": None}) if actor.actor_id == "a" else actor
                    for actor in state.actors
                ),
            }
        )

    await change(play, cid, correct)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("packet", ["blast", "fragment"])
async def test_current_spent_item_ignition_custody_restart_and_exact_retry(
    tmp_path: Path, backend: str, legacy: bool, packet: Literal["blast", "fragment"]
) -> None:
    cid, play, _, pending, _ = await opened(tmp_path, backend, legacy=legacy)
    await current_custody(play, cid)
    before = await play.store.read(cid)
    state = play._load(before)
    command = ChooseOpponentFragment(
        id="choose",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    restarted = build_play(
        tmp_path, play.engine, store=play.store, rng=RecordedDice(()), instants=play.instants
    )
    with pytest.raises((AuthorizationError, ValidationError)):
        await TaskService(restarted).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await TaskService(restarted).execute(
            cid,
            command.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id="b",
        )
    assert isinstance(restarted.rng, RecordedDice)
    assert await play.store.read(cid) == before and restarted.rng.exhausted()
    following = (
        (6, 5, 5, 6) * 3 + (() if legacy else (5, 5, 6))
        if packet == "blast"
        else (1, 5, 5, 6) * 2 + (1, 5, 5, 5, 6)
    )
    faces = (2, 2, 2, 1, 1, 1) + following
    history, events = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice(faces), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await TaskService(failing).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == events
    assert not real_play_clock(play._load(before)).cooldowns
    restarted.rng = RecordedDice(faces)
    result = await TaskService(restarted).execute(cid, command, principal_id="b")
    final = restarted._load(await restarted.store.read(cid))
    item = source_item(final)
    ignited = packet == "blast" and not legacy
    assert item.condition and item.condition.hp == (3 if packet == "blast" else -1)
    assert item.condition.burning is ignited
    assert item.owner_id == "b" and item.ground == pending.preparation.progress.center
    assert item.firearm_failure and item.firearm_failure.kind == "destroyed"
    assert (
        not item.ready
        and not item.equipped
        and not any(i.id == item.id for i in final.resources.items)
    )
    assert (
        sum(i.id == item.id for i in (*final.resources.items, *final.resources.expended_items)) == 1
    )
    assert pending.launch.command.actor_id == "a"
    assert next(actor.approval for actor in final.actors if actor.actor_id == "a") is None
    assert result.check == pending.original and result.luck and result.luck.chosen_index == 0
    assert {entry.actor_id for entry in real_play_clock(final).cooldowns} == {"b"}
    assert next(pool.current for pool in final.resources.pools if pool.id == "hp:b") == 9
    assert restarted.rng.exhausted()
    blast_result = next(
        row
        for row in final.resources.object_results
        if row.command_id == "fragment:object:sword-a:blast"
    )
    assert blast_result.injury == (6 if packet == "blast" else 1)
    assert blast_result.ignited is ignited
    assert blast_result.checks == (((5, 5, 6),) if ignited else ())
    if packet == "fragment":
        fragment_result = next(
            row
            for row in final.resources.object_results
            if row.command_id == "fragment:object:sword-a:fragment:0"
        )
        assert fragment_result.injury == 9 and not fragment_result.ignited
        assert not fragment_result.checks
    saved = await restarted.store.read(cid)
    retry = build_play(
        tmp_path, play.engine, store=play.store, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(retry).execute(cid, command, principal_id="b") == result
    assert saved == await retry.store.read(cid) == await retry.store.replay(cid)
    with pytest.raises(ValidationError, match="Item is not owned"):
        retry.engine.resources.apply(
            final.resources,
            Equip(
                id="re-equip-spent",
                actor_id="b",
                expected_revision=final.resources.revision,
                item_id=item.id,
            ),
        )
    assert saved == await retry.store.read(cid)
    with pytest.raises(ConflictError):
        await TaskService(retry).execute(
            cid,
            command.model_copy(update={"id": "late", "expected_revision": final.revision}),
            principal_id="b",
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_new_and_historical_current_item_continuations_seed_reexecute(
    tmp_path: Path, backend: str, legacy: bool
) -> None:
    cid, play, _, pending, _ = await opened(tmp_path, backend, legacy=legacy)
    await current_custody(play, cid)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    state = play._load(initial)
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    command = ChooseOpponentFragment(
        id="seeded-choice",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    await TaskService(play).execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 1 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final == await play.store.replay(cid)
    current = source_item(play._load(final))
    assert current.owner_id == "b" and current.condition and current.condition.hp < 9


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_prepare_generation_is_recorded_and_seed_reexecutes(
    tmp_path: Path, backend: str, legacy: bool
) -> None:
    cid, play, _, pending, initial = await opened(
        tmp_path, backend, legacy=legacy, seeded_open=True
    )
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[-1:]
    recorded = records[0]
    assert recorded.command_input
    payload = json.loads(recorded.command_input)
    assert payload["command"].get("incendiary_objects", False) is not legacy
    assert ("incendiary_objects" in payload["command"]) is not legacy
    assert ("incendiary_objects" in pending.preparation.progress.model_dump()) is not legacy
    replayed, checks = await verify_commands(
        initial,
        records,
        [
            event
            for event in await play.store.stream(cid)
            if event.command_id == recorded.command_id
        ],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "prepare-reexecuted"),
    )
    assert len(checks) == 1 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final == await play.store.replay(cid)
