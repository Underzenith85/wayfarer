"""B66/B410/B415: the latest accepted thrower is the fragment opponent."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play
from test_actions import world
from test_area_attacks import grenade, launch
from test_fragment_object_custody import opened
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene
from test_opponent_attack_routes import enroll, luck_source
from test_prepared_fragment_attack import responses

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.explosion import ExplosionSpec
from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.combat.commands import ChooseDefense, ResolveWeaponExplosion
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.world import Fact
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.opponent_fragment_delivery import latest_delivery
from wayfarer.orchestration.opponent_fragment_records import (
    ChooseOpponentFragment,
    OpponentFragmentPending,
    PrepareOpponentFragment,
)
from wayfarer.orchestration.opponent_fragment_sources import bind_fragment_source
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


async def delivered(path: Path, backend: str) -> tuple[str, PlayService, str, str]:
    definition, purchase = luck_source()
    visible = replace(
        world(),
        facts=world().facts + (Fact("visible-a", "a", "visible", "person"),),
        knowledge=(("b", "visible-a"), ("a", "promise")),
    )
    mode = grenade().model_copy(
        update={"firearm": FirearmSpec(technology_level=6, action="grenade", fuse_seconds=5)}
    )
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        free_defender_hand=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        ranged_mode=mode,
        ranged_scene=scene()
        + (scene()[0].model_copy(update={"attacker_id": "b", "defender_id": "a"}),),
        warhead=ExplosionSpec(dice=1, fragmentation_dice=1),
        runtime_world=visible,
        durability=ObjectProfile(construction="unliving", hp=100, dr=100, ht=10, size_modifier=0),
    )
    play = await enroll(path, backend, cid, original)
    await launch(
        cid, play, GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0), [3, 3, 3]
    )
    original_command = next(
        row.command_id
        for row in await play.store.history(cid)
        if row.command_input
        and json.loads(row.command_input).get("command", {}).get("maneuver") == "attack"
    )
    await turn(cid, play, "b", "change_posture", posture="kneeling")
    await turn(cid, play, "a", "do_nothing")
    await turn(
        cid, play, "b", "ready", item_id="sword-a", recover_thrown_item=True, ready_hand="left-hand"
    )
    await turn(cid, play, "a", "do_nothing")
    await turn(
        cid,
        play,
        "b",
        "attack",
        item_id="sword-a",
        target_id="a",
        mode_id="ranged",
        area_aim_point=GroundPosition(encounter_id="fight", geometry="grid", x=0, y=0),
    )
    latest_command = (await play.store.history(cid))[-1].command_id
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="rethrow",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="a",
    )
    for _ in range(2):
        await turn(cid, play, "a", "do_nothing")
        await turn(cid, play, "b", "do_nothing")
    return cid, play, original_command, latest_command


def preparation(
    play: PlayService, campaign: Campaign, source: str, owner: str
) -> PrepareOpponentFragment:
    state = play._load(campaign)
    blast = blasts(state.resources)[0]
    return PrepareOpponentFragment(
        id="prepare-delivery",
        actor_id=owner,
        expected_revision=state.revision,
        launch_command_id=source,
        resolution=ResolveWeaponExplosion(
            id="prepare-delivery",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            blast_id=blast.id,
            responses=responses(),
            object_cover={
                i.id: 100 for i in (*state.resources.items, *state.resources.expended_items)
            },
            object_sizes={
                i.id: 0 for i in (*state.resources.items, *state.resources.expended_items)
            },
            environment="air",
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_claimed_thrower_is_rejected_after_canonical_rethrow(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original, _ = await delivered(tmp_path, backend)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="latest|delivery"):
        await TaskService(play).execute(
            cid, preparation(play, before, original, "b"), principal_id="gm"
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_latest_thrower_fragment_luck_actual_state_restart_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, latest = await delivered(tmp_path, backend)
    before = await play.store.read(cid)
    play.rng = RecordedDice((1, 5, 5, 6))
    await TaskService(play).execute(cid, preparation(play, before, latest, "a"), principal_id="gm")
    prepared = await play.store.read(cid)
    state = play._load(prepared)
    pending = snapshot(state).pending
    assert isinstance(pending, OpponentFragmentPending)
    assert pending.actor_id == "a" and pending.attacker_id == "b"
    assert (
        pending.launch.origin_attack_id
        and pending.launch.origin_attack_id != pending.launch.attack_id
    )
    assert next(p for p in state.resources.pools if p.id == "hp:a").current == 9
    choose = ChooseOpponentFragment(
        id="latest-choice",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    restart = build_play(
        tmp_path, play.engine, store=play.store, rng=RecordedDice(()), instants=play.instants
    )
    with pytest.raises((AuthorizationError, ValidationError)):
        await TaskService(restart).execute(cid, choose, principal_id="b")
    with pytest.raises(ConflictError):
        await TaskService(restart).execute(
            cid,
            choose.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id="a",
        )
    faces = (2, 2, 2, 1, 1, 1) + (1, 5, 5, 6) * 4
    history, events = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice(faces), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await TaskService(failing).execute(cid, choose, principal_id="a")
    assert await play.store.read(cid) == prepared
    assert await play.store.history(cid) == history and await play.store.stream(cid) == events
    restart.rng = RecordedDice(faces)
    result = await TaskService(restart).execute(cid, choose, principal_id="a")
    final = await play.store.read(cid)
    assert next(p for p in play._load(final).resources.pools if p.id == "hp:a").current == 9
    retry = build_play(
        tmp_path, play.engine, store=play.store, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(retry).execute(cid, choose, principal_id="a") == result
    assert final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_latest_delivery_preparation_seed_reexecutes(tmp_path: Path, backend: str) -> None:
    cid, play, _, latest = await delivered(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    await TaskService(play).execute(cid, preparation(play, initial, latest, "a"), principal_id="gm")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert isinstance(pending, OpponentFragmentPending)
    await TaskService(play).execute(
        cid,
        ChooseOpponentFragment(
            id="seeded-latest-choice",
            actor_id="a",
            expected_revision=play._load(await play.store.read(cid)).revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="a",
    )
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    ids = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_delivery_requires_birth_and_exact_producer_inputs(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, latest = await delivered(tmp_path, backend)
    before = await play.store.read(cid)
    blast = blasts(play._load(before).resources)[0]
    history = await play.store.history(cid)
    delivery = latest_delivery(history, blast.id)
    assert delivery
    birth_index = next(
        i
        for i, row in enumerate(history)
        if blast.id in {b.id for b in blasts(play._load(row.state_after).resources)}
    )
    with pytest.raises(ValidationError, match="birth receipt"):
        latest_delivery(history[:birth_index] + history[birth_index + 1 :], blast.id)
    producer_index = next(
        i for i, row in enumerate(history) if row.command_id == delivery.producer_command_id
    )
    tampered = history.copy()
    tampered[producer_index] = replace(tampered[producer_index], payload_hash="0" * 64)
    with pytest.raises(ValidationError, match="digest"):
        latest_delivery(tampered, blast.id)
    command = preparation(play, before, latest, "a")
    bound = await bind_fragment_source(play, cid, command)
    assert bound.launch
    forged = bound.model_copy(
        update={"launch": bound.launch.model_copy(update={"origin_attack_id": "forged-origin"})}
    )
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="differs"):
        await TaskService(play).execute(cid, forged, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_historical_launch_origin_is_absent_and_roundtrip_stable(
    tmp_path: Path, backend: str
) -> None:
    _, _, _, pending, _ = await opened(tmp_path, backend, legacy=True, seeded_open=True)
    encoded = pending.launch.model_dump_json()
    assert pending.launch.origin_attack_id is None
    assert "origin_attack_id" not in json.loads(encoded)
    assert type(pending.launch).model_validate_json(encoded).model_dump_json() == encoded
