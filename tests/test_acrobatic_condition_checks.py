"""Selected B361/B375/B421 text: direct skill penalties affect actual Dodge."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_acrobatic_trait_bonuses import reaction_fixture
from test_combat_sensory_authority import change
from test_symptom_attribute_consumers import penalize

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.fright import fright_effect
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, level
from wayfarer.engine.simulation.health.fright import apply_effect, effects, recover
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration import task_combat_generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.task_records import SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands

FEATURE = "acrobatic-condition-checks"


def recovered_coma(state: PlayState, *, expired: bool = False) -> PlayState:
    """Run the existing row-28 producer and actual HT recovery, then defend."""
    resources = apply_effect(
        state.resources,
        fright_effect(28, 10, rng=RecordedDice([])),
        actor_id="b",
        trigger_id="private-fear",
        command_id="coma",
        ht=10,
        will=10,
        modified_will=10,
        rng=RecordedDice([]),
    )
    due = effects(resources)[0].due
    assert due == state.resources.game_time + 1800
    resources, passed = recover(
        resources.model_copy(update={"game_time": due}),
        actor_id="b",
        trigger_id="private-fear",
        command_id="recover-coma",
        rng=RecordedDice([3, 3, 3]),
    )
    assert passed
    episode = effects(resources)[0]
    assert not episode.active and episode.aftermath_until == due + 21600
    resources = resources.model_copy(update={"game_time": due + 21600 if expired else due + 21599})
    return state.model_copy(update={"resources": resources})


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("flying", [False, True])
@pytest.mark.parametrize("expired", [False, True])
async def test_current_direct_skill_penalty_changes_actual_reaction_and_injury(
    tmp_path: Path, backend: str, flying: bool, expired: bool
) -> None:
    cid, play = await reaction_fixture(tmp_path, backend, flying, ())
    await change(play, cid, lambda state: recovered_coma(state, expired=expired))
    state = play._load(await play.store.read(cid))
    skill = "skill:aerobatics" if flying else "skill:acrobatics"
    # Aftermath changes rolls, never the approved purchased skill or base Dodge.
    assert level(build(play.rules_context, state, "b", defensive=True), skill).value == 8
    command = ChooseDefense(
        id="condition-reaction",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(update={"id": "stale", "expected_revision": state.revision - 1}),
            principal_id="b",
        )
    play.rng = RecordedDice([2, 2, 3])
    with pytest.raises(ValidationError, match="dice"):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([2, 2, 3, 3, 3, 3, 3, 3, 3] + ([] if expired else [1]))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    updated = play._load(final)
    trace = updated.encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == (8 if expired else 6)
    assert trace.outcome.succeeded == expired
    assert tuple(m.value for m in trace.modifiers) == (() if expired else (-2,))
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == (11 if expired else 7)
    assert result.injury.injury == (0 if expired else 3)
    assert updated.actors == state.actors and play.rng.exhausted()
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("flying", [False, True])
async def test_direct_skill_penalty_composes_with_trait_bonus_and_exempt_attribute_loss(
    tmp_path: Path, backend: str, flying: bool
) -> None:
    cid, play = await reaction_fixture(tmp_path, backend, flying, ("perfect-balance",))
    await change(play, cid, lambda state: penalize(recovered_coma(state), "b"))
    state = play._load(await play.store.read(cid))
    skill = "skill:aerobatics" if flying else "skill:acrobatics"
    assert level(build(play.rules_context, state, "b"), skill).value == 4
    play.rng = RecordedDice([2, 2, 3, 3, 3, 3, 3, 3, 3])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="combined-reaction",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            acrobatic_dodge=True,
        ),
        principal_id="b",
    )
    final = play._load(await play.store.read(cid))
    trace = final.encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == 7 and trace.outcome.succeeded
    assert tuple(m.value for m in trace.modifiers) == (1, -2)
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == 11 and result.injury.injury == 0
    assert final.actors == state.actors and play.rng.exhausted()


def consequence_seed(typed: bool) -> str:
    for number in range(100000):
        value = f"{number:064x}"
        rng = SeededRandom(value)
        skill = sum(rng.randbelow(6) + 1 for _ in range(3))
        attack = 9 if typed else sum(rng.randbelow(6) + 1 for _ in range(3))
        dodge = sum(rng.randbelow(6) + 1 for _ in range(3))
        damage = rng.randbelow(6) + 1
        if skill == 7 and 7 <= attack <= 13 and dodge == 9 and damage == 1:
            return value
    raise AssertionError("No source-consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_recorded_direct_and_task_generation_reexecutes_old_and_new_consequences(
    tmp_path: Path, backend: str, typed: bool, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await reaction_fixture(tmp_path, backend, False, ())
    await change(play, cid, recovered_coma)
    state = play._load(await play.store.read(cid))
    pending_id = None
    if typed:
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id="clock", actor_id="gm", expected_revision=state.revision, running=True
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
        pending_id = opened.pending_id
        assert pending_id
        state = play._load(await play.store.read(cid))
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    response = ChooseDefense(
        id="condition-reaction",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
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
    module = task_combat_generations if typed else generations
    features = module.ACTIVE
    monkeypatch.setattr(module, "ACTIVE", features if enabled else features - {FEATURE})
    play.rng, play.seeds = secrets, lambda: consequence_seed(typed)
    service = TaskService(play) if typed else CombatService(play)
    result = await service.execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == (6 if enabled else 8)
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
    assert await service.execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final and play.rng.exhausted()
