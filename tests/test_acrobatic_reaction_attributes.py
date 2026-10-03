"""B375/B421 reaction attributes differ from ordinary governed-skill checks."""

import json
from pathlib import Path

import pytest
from test_acrobatic_trait_bonuses import reaction_fixture
from test_combat_sensory_authority import change
from test_symptom_attribute_consumers import penalize

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actors import build, level
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.task_records import SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.orchestration.views import campaign_view

FEATURE = "acrobatic-reaction-attributes"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("flying", [False, True])
@pytest.mark.parametrize("penalty", [False, True])
async def test_reaction_exempts_current_dx_reduction_but_ordinary_skill_does_not(
    tmp_path: Path, backend: str, flying: bool, penalty: bool
) -> None:
    cid, play = await reaction_fixture(tmp_path, backend, flying, ())
    if penalty:
        await change(play, cid, lambda state: penalize(penalize(state, "b"), "a"))
    state = play._load(await play.store.read(cid))
    skill = "skill:aerobatics" if flying else "skill:acrobatics"
    ordinary = build(play.rules_context, state, "b")
    assert int(level(ordinary, skill).value) == (4 if penalty else 8)
    before, history = await play.store.read(cid), await play.store.history(cid)
    command = ChooseDefense(
        id="reaction",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(update={"id": "stale", "expected_revision": state.revision - 1}),
            principal_id="b",
        )
    play.rng = RecordedDice([2, 3, 3])
    with pytest.raises(ValidationError, match="dice"):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([2, 3, 3, 3, 3, 3, 3, 3, 3])
    result = await CombatService(play).execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == 8 and trace.outcome.succeeded
    assert result.injury and result.injury.defense
    assert result.injury.attack.effective_target == (9 if penalty else 13)
    assert result.injury.defense.effective_target == 11 and result.injury.injury == 0
    assert result.injury.hp_before == result.injury.hp_after == 10 and play.rng.exhausted()
    member = next(m for m in state.members if m.principal_id == "b")
    assert "hp:a" not in json.dumps(campaign_view(play._load(final), member), default=str)
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("flying", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_typed_task_reaction_captures_its_own_attribute_generation(
    tmp_path: Path, backend: str, flying: bool, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.orchestration import task_combat_generations

    cid, play = await reaction_fixture(tmp_path, backend, flying, ())
    await change(play, cid, lambda state: penalize(state, "b"))
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
    with pytest.raises(AuthorizationError):
        await task.pending(cid, principal_id="a")
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
        task_combat_generations, "ACTIVE", features if enabled else features - {FEATURE}
    )
    play.rng = RecordedDice([2, 3, 3, 3, 3, 3] + ([] if enabled else [1]))
    result = await task.execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    state = play._load(final)
    trace = state.encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == (8 if enabled else 4)
    assert trace.outcome.succeeded == enabled
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        10 if enabled else 7
    )
    assert play.rng.exhausted()
    monkeypatch.setattr(task_combat_generations, "ACTIVE", features)
    play.rng = RecordedDice([])
    assert await task.execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_skill_is_still_minus_two_with_corrected_attributes(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await reaction_fixture(tmp_path, backend, False, ())
    await change(play, cid, lambda state: penalize(state, "b"))
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3, 3, 3, 3, 3, 3, 3, 1])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="failed",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            acrobatic_dodge=True,
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    trace = state.encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == 8 and not trace.outcome.succeeded
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == 7 and result.injury.injury == 3
    assert play.rng.exhausted()
