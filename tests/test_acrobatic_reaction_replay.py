"""Captured reaction policy preserves old injury, canonical bytes and seeded replay."""

import secrets
from pathlib import Path

import pytest
from test_acrobatic_reaction_attributes import FEATURE
from test_acrobatic_trait_bonuses import reaction_fixture
from test_combat_sensory_authority import change
from test_symptom_attribute_consumers import penalize

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.orchestration import task_combat_generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.task_records import SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands


def reaction_seed(task: bool) -> str:
    for number in range(100000):
        value = f"{number:064x}"
        rng = SeededRandom(value)
        skill = sum(rng.randbelow(6) + 1 for _ in range(3))
        attack = 9 if task else sum(rng.randbelow(6) + 1 for _ in range(3))
        dodge = sum(rng.randbelow(6) + 1 for _ in range(3))
        damage = rng.randbelow(6) + 1
        if skill == 8 and 7 <= attack <= 13 and dodge == 9 and damage == 1:
            return value
    raise AssertionError("No actual source consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_recorded_reaction_generation_reexecutes_exact_old_and_new_results(
    tmp_path: Path, backend: str, typed: bool, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await reaction_fixture(tmp_path, backend, False, ())
    await change(play, cid, lambda state: penalize(state, "b"))
    state = play._load(await play.store.read(cid))
    if typed:
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id="clock",
                actor_id="gm",
                expected_revision=state.revision,
                running=True,
            ),
            principal_id="gm",
        )
        state = play._load(await play.store.read(cid))
        pending = state.encounters[0].pending_defense
        assert pending
        play.rng = RecordedDice([3, 3, 3])
        opened = await TaskService(play).execute(
            cid,
            BeginOpponentAttack(
                id="original",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                attack_id=pending.id,
            ),
            principal_id="gm",
        )
        assert opened.pending_id
        state = play._load(await play.store.read(cid))
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    response = ChooseDefense(
        id="reaction",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
    module = task_combat_generations if typed else generations
    features = module.ACTIVE
    monkeypatch.setattr(module, "ACTIVE", features if enabled else features - {FEATURE})
    play.rng, play.seeds = secrets, lambda: reaction_seed(typed)
    if typed:
        assert opened.pending_id is not None
        command = ChooseOpponentAttack(
            id=response.id,
            actor_id="b",
            expected_revision=state.revision,
            pending_id=opened.pending_id,
            choice="accept",
            response=response,
        )
        task_result = await TaskService(play).execute(cid, command, principal_id="b")
    else:
        combat_result = await CombatService(play).execute(cid, response, principal_id="b")
    final = await play.store.read(cid)
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == (8 if enabled else 4)
    assert next(p.current for p in play._load(final).resources.pools if p.id == "hp:b") == (
        10 if enabled else 7
    )
    records = (await play.store.history(cid))[count:]
    raw = records[0].command_input
    assert raw
    payload = replay_payload(raw)
    assert isinstance(payload, dict)
    key = task_combat_generations.KEY if typed else generations.KEY
    assert (FEATURE in payload[key]) == enabled
    monkeypatch.setattr(module, "ACTIVE", features)
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id == response.id],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
    assert replayed == final
    play.rng = RecordedDice([])
    if typed:
        assert await TaskService(play).execute(cid, command, principal_id="b") == task_result
    else:
        assert await CombatService(play).execute(cid, response, principal_id="b") == combat_result
    assert await play.store.read(cid) == final and play.rng.exhausted()
    assert (await play.store.history(cid))[count:] == records
