"""Opponent original timing, persistent authority, atomicity and seed replay."""

import asyncio
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_composed_attack_host import declare
from test_gadgeteer_gizmos_persistence import FailingCommitPlay, RevokingStore
from test_opponent_attack_host import begin, choose, fixture, next_attack
from test_symptom_attribute_consumers import attribute_penalty

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.opponent_attack_records import ChooseOpponentAttack
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seed_reexecution_and_full_fold_match_actual_hp(tmp_path: Path, backend: str) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    _, original = await begin(play, cid)
    _, result = await choose(play, cid, original.pending_id)
    assert result.check and result.check.total == 15
    assert result.luck and tuple(map(sum, result.luck.attempts)) == (15, 15, 7)
    assert result.luck.chosen_index == 0
    committed = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(c.folded and c.reexecuted for c in checks)
    assert replayed == committed == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("points,seconds", [(15, 3600), (30, 1800), (60, 600)])
@pytest.mark.parametrize("offset", [-1, 0])
async def test_original_microsecond_deadline_prevents_waiting_for_luck(
    tmp_path: Path, backend: str, points: int, seconds: int, offset: int
) -> None:
    cid, play, source = await fixture(tmp_path, backend, points=points)
    clock = real_play_clock(play._load(await play.store.read(cid)))
    assert clock.observed_at_us
    origin = clock.observed_at_us
    now = [origin + 900001]
    play.instants = lambda: CommandInstant(now[0])
    await declare(play, cid, source)
    play.rng = RecordedDice((5, 5, 5))
    _, first = await begin(play, cid)
    play.rng = RecordedDice((4, 4, 5, 5, 5, 5))
    await choose(play, cid, first.pending_id)
    deadline = seconds * 1_000_000 + 900001
    assert (
        real_play_clock(play._load(await play.store.read(cid)))
        .cooldowns[0]
        .available_at_microseconds
        == deadline
    )
    play.rng = RecordedDice(())
    await next_attack(play, cid, source, "second-attack")
    now[0] = origin + deadline + offset
    play.rng = RecordedDice((5, 5, 5))
    _, second = await begin(play, cid, identifier="second-original")
    if offset < 0:
        now[0] += 10_000_000
        play.rng = RecordedDice(())
        before = await play.store.read(cid)
        with pytest.raises(ValidationError, match="when this original was rolled"):
            await choose(play, cid, second.pending_id, identifier="late-use")
        assert await play.store.read(cid) == before
        await choose(play, cid, second.pending_id, identifier="accept", luck=False)
    else:
        play.rng = RecordedDice((4, 4, 5, 5, 5, 5))
        await choose(play, cid, second.pending_id, identifier="boundary-use")
        assert len(snapshot(play._load(await play.store.read(cid))).luck.receipts) == 2


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_pause_retains_original_and_accepts_without_spending(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    play.rng = RecordedDice((5, 5, 5))
    _, original = await begin(play, cid)
    pending = snapshot(play._load(await play.store.read(cid))).pending
    play.rng = RecordedDice(())
    for identifier, running in (("pause", False), ("resume", True)):
        state = play._load(await play.store.read(cid))
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id=identifier, actor_id="gm", expected_revision=state.revision, running=running
            ),
            principal_id="gm",
        )
        assert snapshot(play._load(await play.store.read(cid))).pending == pending
    _, result = await choose(play, cid, original.pending_id, luck=False)
    assert result.check == original.check and result.luck is None
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_candidate_commit_failure_rolls_back_luck_clock_and_injury(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    play.rng = RecordedDice((1, 1, 1))
    _, original = await begin(play, cid)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    faces = (1, 2, 2, 3, 3, 3, 2, 2)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice(faces), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await choose(failing, cid, original.pending_id)
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == events
    play.rng = RecordedDice(faces)
    _, result = await choose(play, cid, original.pending_id)
    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.hp_after == 6


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("same", [True, False])
async def test_independent_store_races_commit_one_choice(
    tmp_path: Path, backend: str, same: bool
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    play.rng = RecordedDice((5, 5, 5))
    _, original = await begin(play, cid)
    assert original.pending_id
    revision = play._load(await play.store.read(cid)).revision
    other = build_play(tmp_path, play.engine, backend=backend, instants=play.instants)
    play.rng = secrets
    commands = tuple(
        ChooseOpponentAttack(
            id=name,
            actor_id="b",
            expected_revision=revision,
            pending_id=original.pending_id,
            choice="use-luck",
            response=ChooseDefense(
                id=name,
                actor_id="b",
                expected_revision=revision,
                encounter_id="fight",
                defense="none",
            ),
        )
        for name in ("one", "one" if same else "two")
    )
    results = await asyncio.gather(
        TaskService(play).execute(cid, commands[0], principal_id="bob"),
        TaskService(other).execute(cid, commands[1], principal_id="bob"),
        return_exceptions=True,
    )
    if same:
        assert results[0] == results[1]
    else:
        assert sum(isinstance(r, ConflictError) for r in results) == 1
    state = play._load(await play.store.read(cid))
    assert state.revision == revision + 1 and len(snapshot(state).luck.receipts) == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


def revoke_owner(state: PlayState) -> PlayState:
    return state.model_copy(
        update={
            "members": tuple(
                m.model_copy(update={"actor_ids": ()}) if m.principal_id == "bob" else m
                for m in state.members
            )
        }
    )


@pytest.mark.parametrize("retry", [False, True])
async def test_current_owner_rechecked_after_duplicate_lookup(tmp_path: Path, retry: bool) -> None:
    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)
    play.rng = RecordedDice((5, 5, 5))
    _, original = await begin(play, cid)
    assert original.pending_id
    state = play._load(await play.store.read(cid))
    command = ChooseOpponentAttack(
        id="choose",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=original.pending_id,
        choice="use-luck",
        response=ChooseDefense(
            id="choose",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
    )
    if retry:
        play.rng = RecordedDice((4, 4, 5, 5, 5, 5))
        await TaskService(play).execute(cid, command, principal_id="bob")
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    guarded = build_play(
        tmp_path, play.engine, store=store, rng=RecordedDice(()), instants=play.instants
    )
    store.revoke = lambda: change(play, cid, revoke_owner)
    with pytest.raises(AuthorizationError if retry else (AuthorizationError, ConflictError)):
        await TaskService(guarded).execute(cid, command, principal_id="bob")
    assert len(snapshot(play._load(await play.store.read(cid))).luck.receipts) == int(retry)


async def test_later_attacker_condition_does_not_rescore_captured_original(tmp_path: Path) -> None:
    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)
    play.rng = RecordedDice((3, 3, 3))
    _, original = await begin(play, cid)
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={"resources": attribute_penalty(s.resources, "a", "dx", level=8)}
        ),
    )
    play.rng = RecordedDice((2, 2))
    _, result = await choose(play, cid, original.pending_id, luck=False, principal="gm")
    assert result.check and result.check == original.check and result.check.effective_target == 12
    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.hp_after == 6


async def test_captured_attack_can_finish_after_attacker_approval_is_revoked(
    tmp_path: Path,
) -> None:
    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)
    play.rng = RecordedDice((3, 3, 3))
    _, original = await begin(play, cid)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "actors": tuple(
                    actor.model_copy(update={"approval": None}) if actor.actor_id == "a" else actor
                    for actor in state.actors
                )
            }
        ),
    )
    play.rng = RecordedDice((2, 2))
    _, result = await choose(play, cid, original.pending_id, luck=False, principal="gm")
    assert result.check == original.check and result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.hp_after == 6
    state = play._load(await play.store.read(cid))
    assert state.actors[0].approval is None
    assert snapshot(state).pending is None and state.encounters[0].current_actor_id == "b"
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("selected,following,hp", [((5, 5, 5), (), 10), ((3, 3, 3), (2, 2), 6)])
async def test_trusted_public_roll_does_not_grant_unaware_owner_a_defense(
    tmp_path: Path,
    backend: str,
    selected: tuple[int, int, int],
    following: tuple[int, ...],
    hp: int,
) -> None:
    from test_blindness_combat_consumers import blind

    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    await change(play, cid, blind)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="director authority"):
        await begin(play, cid, principal="bob")
    assert await play.store.read(cid) == before and play.rng.exhausted()
    play.rng = RecordedDice((3, 3, 3))
    _, begun = await begin(play, cid)
    assert begun.pending_id and begun.check and begun.check.total == 9
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await choose(play, cid, begun.pending_id, defense="dodge", luck=False)
    assert await play.store.read(cid) == before and play.rng.exhausted()
    play.rng = RecordedDice((2, 2, 2) + selected + following)
    _, result = await choose(play, cid, begun.pending_id)
    assert result.check and result.check.total == sum(selected) and result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.defense is None and combat.injury.hp_after == hp
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)
