"""B66/B410 task-host rethrow uses one armed cause and the actual last opponent."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play
from test_actions import world
from test_area_attacks import grenade, launch
from test_fragment_rethrow_delivery import preparation
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene
from test_opponent_attack_routes import enroll, luck_source

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.explosion import ExplosionSpec
from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts
from wayfarer.engine.world import Fact
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration import task_combat_generations
from wayfarer.orchestration.opponent_attack_records import (
    BeginOpponentAttack,
    ChooseOpponentAttack,
    OpponentAttackPending,
)
from wayfarer.orchestration.opponent_fragment_delivery import latest_delivery
from wayfarer.orchestration.opponent_fragment_records import (
    ChooseOpponentFragment,
    OpponentFragmentPending,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_combat_generations import KEY, features
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import canonical, combat_intent, intent_input, same_input
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.replay import verify_commands


async def pending_delivery(
    path: Path, backend: str
) -> tuple[str, PlayService, BlastRecord, str, ChooseOpponentAttack]:
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
    initial_blast = blasts(play._load(await play.store.read(cid)).resources)[0]
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
    launch_id = (await play.store.history(cid))[-1].command_id
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming
    play.rng = RecordedDice((3, 3, 3))
    await TaskService(play).execute(
        cid,
        BeginOpponentAttack(
            id="task-rethrow-open",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=incoming.id,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, OpponentAttackPending)
    command = ChooseOpponentAttack(
        id="task-rethrow-choice",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="accept",
        response=ChooseDefense(
            id="task-rethrow-choice",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
    )
    return cid, play, initial_blast, launch_id, command


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_task_rethrow_original_cause_current_custody_authority_rollback_retry(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, original, _, command = await pending_delivery(tmp_path, backend)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    assert isinstance(command.response, ChooseDefense)
    with pytest.raises(ValidationError, match="Invalid private task command"):
        await TaskService(play).execute(
            cid, command.model_dump() | {KEY: ["grenade-fuse"]}, principal_id="a"
        )
    with pytest.raises((AuthorizationError, ValidationError)):
        await TaskService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid,
            command.model_copy(
                update={
                    "expected_revision": command.expected_revision - 1,
                    "response": command.response.model_copy(
                        update={"expected_revision": command.expected_revision - 1}
                    ),
                }
            ),
            principal_id="a",
        )
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice(()), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await TaskService(failing).execute(cid, command, principal_id="a")
    assert (
        before == await play.store.read(cid)
        and history == await play.store.history(cid)
        and events == await play.store.stream(cid)
    )
    with monkeypatch.context() as era:
        if legacy:
            era.setattr(task_combat_generations, "ACTIVE", frozenset())
        result = await TaskService(play).execute(cid, command, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    values = blasts(state.resources)
    assert len(values) == (2 if legacy else 1)
    assert (values[0].id, values[0].payload, values[0].due, values[0].fuse_dice) == (
        original.id,
        original.payload,
        original.due,
        original.fuse_dice,
    )
    item = next(i for i in state.resources.expended_items if i.id == "sword-a")
    assert item.owner_id == "b" and item.condition and item.condition.hp == 100
    row = (await play.store.history(cid))[-1]
    assert row.command_input
    payload = json.loads(intent_input(row.command_input))
    assert (KEY in payload) is not legacy
    assert command.model_dump(mode="json") == payload["command"]
    assert same_input(
        CommandInput(row.payload_hash, row.command_input),
        combat_intent(intent_input(row.command_input)),
    )
    public_raw = canonical(
        {"operation": "task-host", "principal_id": "a", "command": command.model_dump(mode="json")}
    )
    receipt_history, receipt_events = await play.store.history(cid), await play.store.stream(cid)
    assert await play.store.duplicate(cid, command.id, public_raw) == final
    with pytest.raises(ConflictError, match="different input"):
        await play.store.duplicate(cid, command.id, public_raw + " ")
    assert await play.store.read(cid) == final
    assert (
        await play.store.history(cid) == receipt_history
        and await play.store.stream(cid) == receipt_events
    )
    restarted = build_play(
        tmp_path, play.engine, store=play.store, rng=RecordedDice(()), instants=play.instants
    )
    with monkeypatch.context() as new_default:
        new_default.setattr(
            task_combat_generations,
            "ACTIVE",
            frozenset() if not legacy else frozenset({"grenade-fuse"}),
        )
        assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_task_rethrow_seed_reexecutes_exact_recorded_generation(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, original, _, command = await pending_delivery(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    with monkeypatch.context() as era:
        if legacy:
            era.setattr(task_combat_generations, "ACTIVE", frozenset())
        await TaskService(play).execute(cid, command, principal_id="a")
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 1 and all(c.folded and c.reexecuted for c in checks) and replayed == final
    values = blasts(play._load(final).resources)
    assert len(values) == (2 if legacy else 1) and values[0].due == original.due


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_task_producer_binds_latest_fragment_luck_and_seed_reexecutes(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original, source, command = await pending_delivery(tmp_path, backend)
    play.rng = RecordedDice(())
    await TaskService(play).execute(cid, command, principal_id="a")
    for _ in range(2):
        await turn(cid, play, "a", "do_nothing")
        await turn(cid, play, "b", "do_nothing")
    initial = await play.store.read(cid)
    history = await play.store.history(cid)
    producer_index = next(i for i, row in enumerate(history) if row.command_id == command.id)
    producer_row = history[producer_index]
    assert producer_row.command_input
    payload = json.loads(intent_input(producer_row.command_input))
    for altered in ("command", "principal"):
        forged_payload = json.loads(canonical(payload))
        if altered == "command":
            forged_payload["command"]["id"] = "forged-id"
            forged_payload["command"]["response"]["id"] = "forged-id"
        else:
            forged_payload["principal_id"] = "gm"
        encoded = canonical(forged_payload)
        forged_history = history.copy()
        forged_history[producer_index] = replace(
            producer_row, command_input=encoded, payload_hash=payload_digest({"input": encoded})
        )
        with pytest.raises(ValidationError, match="receipt identity"):
            latest_delivery(forged_history, original.id)
    count = len(history)
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    await TaskService(play).execute(cid, preparation(play, initial, source, "a"), principal_id="gm")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert (
        isinstance(pending, OpponentFragmentPending)
        and pending.attacker_id == "b"
        and pending.actor_id == "a"
    )
    assert (
        pending.launch.producer_command_id == command.id
        and pending.launch.origin_attack_id == original.id.rpartition(":blast:")[0]
    )
    await TaskService(play).execute(
        cid,
        ChooseOpponentFragment(
            id="fragment-after-task",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="a",
    )
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "fragment-reexecuted"),
    )
    assert len(checks) == 2 and all(c.folded and c.reexecuted for c in checks) and replayed == final
    assert (
        len(blasts(play._load(final).resources)) == 1
        and blasts(play._load(final).resources)[0].resolved
    )


@pytest.mark.parametrize(
    "change",
    [
        {KEY: ["grenade-fuse", "grenade-fuse"]},
        {KEY: ["unknown"]},
        {KEY: "grenade-fuse"},
        {"operation": "combat"},
        {"combat_protocol_features": ["grenade-fuse"]},
        {"command": {"kind": "choose-owner-damage", "response": {"kind": "choose_defense"}}},
        {
            "command": {
                "kind": "choose-opponent-attack",
                "response": {"kind": "resist_composed_attack"},
            }
        },
    ],
)
def test_task_metadata_rejects_wrong_route_types_and_features(change: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "operation": "task-host",
        KEY: ["grenade-fuse"],
        "command": {"kind": "choose-opponent-attack", "response": {"kind": "choose_defense"}},
    }
    encoded = canonical(payload | change)
    with pytest.raises(ValidationError):
        combat_intent(encoded)


def test_task_metadata_is_byte_exact_and_cannot_be_publicly_authored() -> None:
    payload: dict[str, object] = {
        "operation": "task-host",
        KEY: ["grenade-fuse"],
        "command": {"kind": "choose-opponent-attack", "response": {"kind": "choose_defense"}},
    }
    encoded = canonical(payload)
    assert combat_intent(encoded) == canonical({k: v for k, v in payload.items() if k != KEY})
    with pytest.raises(ValidationError):
        combat_intent(encoded + " ")
    with pytest.raises(ValidationError, match="digest"):
        features(CommandInput("0" * 64, encoded))
