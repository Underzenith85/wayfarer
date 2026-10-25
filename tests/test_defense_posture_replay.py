"""Independent per-command posture capture preserves old and new actual injury."""

import json
import secrets
from functools import cache
from pathlib import Path

import pytest
from support.runtime import build_play
from test_defense_postures import DefenseKind, pending

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.tables.combat import Posture
from wayfarer.errors import ValidationError
from wayfarer.orchestration import defense_posture_generations
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, PrepareOwnerDamage
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import DEFENSE_POSTURE_KEY, original_input
from wayfarer.persistence.replay import verify_commands


@cache
def consequence_seed(kind: DefenseKind, typed: bool) -> str:
    for number in range(100000):
        value = f"{number:064x}"
        rng = SeededRandom(value)
        attack = 9 if typed else sum(rng.randbelow(6) + 1 for _ in range(3))
        defense = sum(rng.randbelow(6) + 1 for _ in range(3))
        damage = rng.randbelow(6) + 1
        if (
            attack == 9
            and defense == (7 if kind == "bare-parry" else 8)
            and damage == (5 if kind == "bare-parry" else 3)
        ):
            return value
    raise AssertionError("No actual defense-posture consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture", ["sitting", "crawling"])
@pytest.mark.parametrize("kind,typed", [("dodge", False), ("dodge", True), ("bare-parry", False)])
@pytest.mark.parametrize("enabled", [False, True])
async def test_old_and_new_direct_and_task_scores_reexecute_from_captured_metadata(
    tmp_path: Path,
    backend: str,
    posture: Posture,
    kind: DefenseKind,
    typed: bool,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play, response = await pending(tmp_path, backend, posture, kind)
    pending_id = None
    if typed:
        task = TaskService(play)
        await task.execute(
            cid,
            SetRealPlayClock(
                id="clock",
                actor_id="gm",
                expected_revision=response.expected_revision,
                running=True,
            ),
            principal_id="gm",
        )
        state = play._load(await play.store.read(cid))
        incoming = state.encounters[0].pending_defense
        assert incoming
        play.rng = RecordedDice([3, 3, 3])
        opened = await task.execute(
            cid,
            BeginOpponentAttack(
                id="original",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                attack_id=incoming.id,
            ),
            principal_id="gm",
        )
        pending_id = opened.pending_id
        assert pending_id
        state = play._load(await play.store.read(cid))
        response = response.model_copy(update={"expected_revision": state.revision})
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    command = (
        ChooseOpponentAttack(
            id=response.id,
            actor_id="b",
            expected_revision=state.revision,
            pending_id=pending_id,
            choice="accept",
            response=response,
        )
        if pending_id
        else response
    )
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", enabled)
    play.rng, play.seeds = secrets, lambda: consequence_seed(kind, typed)
    service = TaskService(play) if typed else CombatService(play)
    result = await service.execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    final_state = play._load(final)
    assert next(p.current for p in final_state.resources.pools if p.id == "hp:b") == (
        8 if enabled else 10
    ), result.model_dump_json()
    assert final_state.encounters[0].participants[1].posture == posture
    records = (await play.store.history(cid))[count:]
    raw = records[0].command_input
    assert raw
    metadata = json.loads(raw)
    assert metadata.get(DEFENSE_POSTURE_KEY) == (1 if enabled else None)
    assert DEFENSE_POSTURE_KEY not in original_input(raw)
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", not enabled)
    with replay_inputs(records[0]):
        assert play.rules_context.correct_defense_postures == enabled
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id == command.id],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "defense-posture-seed-only"),
    )
    assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
    assert replayed == final
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    service = TaskService(restarted) if typed else CombatService(restarted)
    assert await service.execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_source_penalty_failure_rolls_back_before_a_successful_exact_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await pending(tmp_path, backend, "sitting", "dodge")
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3])
    with pytest.raises(ValidationError, match="dice"):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3, 3])
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == 7 and result.injury.injury == 2
    final = await play.store.read(cid)
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    assert final == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_owner_damage_stage_retains_its_already_rolled_posture_defense(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, response = await pending(tmp_path, backend, "sitting", "dodge")
    task = TaskService(play)
    await task.execute(
        cid,
        SetRealPlayClock(
            id="clock", actor_id="gm", expected_revision=response.expected_revision, running=True
        ),
        principal_id="gm",
    )
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    response = response.model_copy(update={"expected_revision": state.revision})
    preparation = PrepareOwnerDamage(
        id=response.id, actor_id="b", expected_revision=state.revision, response=response
    )
    play.rng, play.seeds = secrets, lambda: consequence_seed("dodge", False)
    opened = await task.execute(cid, preparation, principal_id="b")
    staged = play._load(await play.store.read(cid))
    damage = snapshot(staged).pending
    assert isinstance(damage, InventoryDamagePending) and damage.actor_id == "a"
    assert next(p.current for p in staged.resources.pools if p.id == "hp:b") == 10
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", False)
    choice = ChooseOwnerDamage(
        id="accept",
        actor_id="a",
        expected_revision=staged.revision,
        pending_id=damage.id,
        choice="accept",
    )
    await task.execute(cid, choice, principal_id="a")
    final = await play.store.read(cid)
    assert next(p.current for p in play._load(final).resources.pools if p.id == "hp:b") == 8
    records = (await play.store.history(cid))[count:]
    assert len(records) == 2 and records[0].command_input and records[1].command_input
    assert json.loads(records[0].command_input)[DEFENSE_POSTURE_KEY] == 1
    assert DEFENSE_POSTURE_KEY not in json.loads(records[1].command_input)
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", True)
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "posture-staged-seed-only"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final
    play.rng = RecordedDice([])
    assert await task.execute(cid, preparation, principal_id="b") == opened
    assert final == await play.store.read(cid) == await play.store.replay(cid)
