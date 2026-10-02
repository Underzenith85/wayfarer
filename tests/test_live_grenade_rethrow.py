"""B410: witnessed enemy pickup and rethrow keep one armed grenade fuse."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play
from test_actions import world
from test_area_attacks import grenade, launch
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene
from test_opponent_attack_routes import enroll
from test_prepared_fragment_attack import responses

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.explosion import ExplosionSpec
from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.combat.battlefield import Battlefield
from wayfarer.engine.simulation.combat.commands import ChooseDefense, ResolveWeaponExplosion
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts
from wayfarer.engine.world import Fact
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, generations
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def rethrow_pending(
    path: Path, backend: str, aim_x: int = 0
) -> tuple[str, PlayService, BlastRecord, ChooseDefense]:
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
        ranged_mode=mode,
        ranged_scene=scene()
        + (scene()[0].model_copy(update={"attacker_id": "b", "defender_id": "a"}),),
        warhead=ExplosionSpec(dice=1),
        durability=ObjectProfile(construction="unliving", hp=10, dr=0, ht=10),
        object_hp=7,
        runtime_world=visible,
        battlefield=Battlefield(id="dock", location_id="dock", width=20, height=4),
    )
    play = await enroll(path, backend, cid, original)
    await launch(
        cid, play, GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0), [3, 3, 3]
    )
    initial_blast = blasts(play._load(await play.store.read(cid)).resources)[0]
    await turn(cid, play, "b", "change_posture", posture="kneeling")
    await turn(cid, play, "a", "do_nothing")
    await turn(
        cid, play, "b", "ready", item_id="sword-a", recover_thrown_item=True, ready_hand="left-hand"
    )
    await turn(cid, play, "a", "do_nothing")
    state = play._load(await play.store.read(cid))
    current = next(i for i in state.resources.items if i.id == "sword-a")
    assert current.owner_id == "b" and current.ready and current.ground is None
    assert current.condition and current.condition.hp == 7
    await turn(
        cid,
        play,
        "b",
        "attack",
        item_id="sword-a",
        target_id="a",
        mode_id="ranged",
        area_aim_point=GroundPosition(encounter_id="fight", geometry="grid", x=aim_x, y=0),
    )
    state = play._load(await play.store.read(cid))
    return (
        cid,
        play,
        initial_blast,
        ChooseDefense(
            id="rethrow-resolution",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("faces,aim_x,expected_x", [((3, 3, 3), 0, 0), ((5, 5, 6, 3), 10, 13)])
async def test_rethrow_preserves_fuse_custody_retry_and_authority(
    tmp_path: Path,
    backend: str,
    faces: tuple[int, ...],
    aim_x: int,
    expected_x: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play, original, command = await rethrow_pending(tmp_path, backend, aim_x)
    before = await play.store.read(cid)
    restarted = build_play(
        tmp_path, play.engine, store=play.store, rng=RecordedDice(()), instants=play.instants
    )
    with pytest.raises((AuthorizationError, ValidationError)):
        await CombatService(restarted).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(restarted).execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    history, events = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice(faces), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await CombatService(failing).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == events
    restarted.rng = RecordedDice(faces)
    result = await CombatService(restarted).execute(cid, command, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    values = blasts(state.resources)
    assert len(values) == 1
    retained = values[0]
    assert (retained.id, retained.due, retained.payload, retained.fuse_dice) == (
        original.id,
        original.due,
        original.payload,
        original.fuse_dice,
    )
    assert retained.center and retained.center.x == expected_x
    item = next(i for i in state.resources.expended_items if i.id == "sword-a")
    assert item.owner_id == "b" and item.ground == retained.center
    assert item.condition and item.condition.hp == 7
    retry = build_play(
        tmp_path, play.engine, store=play.store, rng=RecordedDice(()), instants=play.instants
    )
    recorded = (await play.store.history(cid))[-1]
    assert recorded.command_input
    payload = json.loads(recorded.command_input)
    assert "grenade-fuse" in payload[generations.KEY]
    assert "preserve_grenade_fuse" not in payload["command"]
    with monkeypatch.context() as changed_default:
        changed_default.setattr(generations, "ACTIVE", frozenset())
        assert await CombatService(retry).execute(cid, command, principal_id="a") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_rethrow_seed_reexecutes(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, original, command = await rethrow_pending(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    if legacy:
        with monkeypatch.context() as old:
            old.setattr(generations, "ACTIVE", frozenset())
            await CombatService(play).execute(cid, command, principal_id="a")
    else:
        await CombatService(play).execute(cid, command, principal_id="a")
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    assert records[0].command_input
    payload = json.loads(records[0].command_input)
    assert (generations.KEY in payload) is not legacy
    assert "preserve_grenade_fuse" not in payload["command"]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks) and len(checks) == 1
    assert replayed == final
    values = blasts(play._load(final).resources)
    assert len(values) == (2 if legacy else 1)
    assert values[0].due == original.due
    if legacy:
        assert values[1].due == original.due + 2


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_rethrown_grenade_explodes_once_at_original_deadline(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original, command = await rethrow_pending(tmp_path, backend)
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(cid, command, principal_id="a")
    for _ in range(2):
        await turn(cid, play, "a", "do_nothing")
        await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == original.due
    play.rng = RecordedDice((3, 3, 3, 3, 3))
    resolution = ResolveWeaponExplosion(
        id="original-deadline",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=original.id,
        responses=responses(),
        object_cover={i.id: 0 for i in (*state.resources.items, *state.resources.expended_items)},
        environment="air",
    )
    await CombatService(play).execute(cid, resolution, principal_id="gm")
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    final = await play.store.read(cid)
    state = play._load(final)
    assert len(blasts(state.resources)) == 1 and blasts(state.resources)[0].resolved
    assert next(p for p in state.resources.pools if p.id == "hp:a").current == 7
    assert next(p for p in state.resources.pools if p.id == "hp:b").current == 9
    assert not any(i.id == "sword-a" for i in state.resources.items)
    spent = [i for i in state.resources.expended_items if i.id == "sword-a"]
    assert len(spent) == 1 and spent[0].owner_id == "b"
    assert spent[0].firearm_failure and spent[0].firearm_failure.kind == "destroyed"
    assert final == await play.store.replay(cid)
