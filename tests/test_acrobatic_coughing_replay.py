"""Typed defense hosts capture B428 skill-condition semantics privately."""

import secrets
from pathlib import Path

import pytest
from test_acrobatic_coughing_conditions import FEATURE, coughing
from test_acrobatic_reaction_replay import reaction_seed
from test_acrobatic_trait_bonuses import reaction_fixture
from test_combat_sensory_authority import change

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.acrobatic_conditions import modifiers
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.orchestration import task_combat_generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.task_records import SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("flying", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_typed_task_captures_coughing_and_preserves_absent_generation(
    tmp_path: Path,
    flying: bool,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play = await reaction_fixture(tmp_path, "sqlite", flying, ())
    await change(play, cid, lambda state: coughing(state, "symptoms"))
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    play.rng = RecordedDice([3, 3, 3])
    opened = await task.execute(
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
    state = play._load(await play.store.read(cid))
    assert opened.pending_id
    command = ChooseOpponentAttack(
        id="choose",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=opened.pending_id,
        choice="accept",
        response=ChooseDefense(
            id="choose",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            acrobatic_dodge=True,
        ),
    )
    features = task_combat_generations.ACTIVE
    monkeypatch.setattr(
        task_combat_generations, "ACTIVE", features | {FEATURE} if enabled else features - {FEATURE}
    )
    play.rng = RecordedDice([2, 3, 3, 3, 3, 3] + ([1] if enabled else []))
    result = await task.execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    state = play._load(final)
    trace = state.encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == (5 if enabled else 8)
    assert trace.outcome.succeeded is not enabled
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        7 if enabled else 10
    )
    assert play.rng.exhausted()
    monkeypatch.setattr(task_combat_generations, "ACTIVE", features | {FEATURE})
    play.rng = RecordedDice([])
    assert await task.execute(cid, command, principal_id="b") == result
    assert final == await play.store.replay(cid)


async def test_multiple_coughing_carriers_apply_one_condition_penalty(tmp_path: Path) -> None:
    cid, play = await reaction_fixture(tmp_path, "sqlite", False, ())
    state = play._load(await play.store.read(cid))
    for carrier in ("symptoms", "hazard", "toxin"):
        state = coughing(state, carrier)
    with combat_generation(frozenset({FEATURE})):
        assert sum(m.value for m in modifiers(state.resources, "b")) == -3


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_seeded_coughing_reexecutes_private_direct_and_typed_inputs(
    tmp_path: Path, backend: str, typed: bool, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await reaction_fixture(tmp_path, backend, False, ())
    await change(play, cid, lambda state: coughing(state, "symptoms"))
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
    assert trace and trace.effective_target == (5 if enabled else 8)
    assert next(p.current for p in play._load(final).resources.pools if p.id == "hp:b") == (
        7 if enabled else 10
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
