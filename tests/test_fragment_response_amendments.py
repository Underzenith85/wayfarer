"""Director amendments change only later responses in a durable fragment phase."""

import asyncio
from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_opponent_fragment_host import prepare
from test_prepared_fragment_attack import launched_blast

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.explosion import BlastResponse
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.opponent_fragment_records import (
    AmendFragmentResponses,
    ChooseOpponentFragment,
    OpponentFragmentPending,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock


async def opened(
    path: Path, backend: str, *, later: bool = True
) -> tuple[str, PlayService, OpponentFragmentPending]:
    cid, play, _, _ = await launched_blast(path, backend)
    if later:

        def reverse(state: PlayState) -> PlayState:
            encounter = state.encounters[0]
            return state.model_copy(
                update={
                    "encounters": (
                        encounter.model_copy(update={"participants": encounter.participants[::-1]}),
                    )
                }
            )

        await change(play, cid, reverse)
    play.rng = RecordedDice((1, 5, 5, 6) if later else (1, 5, 5, 6, 1, 5, 5, 6))
    _, result = await prepare(play, cid)
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, OpponentFragmentPending)
    assert result.pending_id == pending.id and pending.original
    return cid, play, pending


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_director_amendment_preserves_roll_and_blast_then_later_target_uses_it(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, pending = await opened(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    response = BlastResponse(
        actor_id="a", cover_dr=100, covered_locations=("torso",), size_modifier=-10
    )
    command = AmendFragmentResponses(
        id="amend",
        actor_id="b",
        expected_revision=before.revision,
        pending_id=pending.id,
        responses=(response,),
    )
    play.rng = RecordedDice(())
    result = await TaskService(play).execute(cid, command, principal_id="gm")
    after = play._load(await play.store.read(cid))
    amended = snapshot(after).pending
    assert isinstance(amended, OpponentFragmentPending)
    assert amended.original == pending.original
    assert amended.preparation.target == pending.preparation.target
    assert amended.preparation.spec == pending.preparation.spec
    assert amended.preparation.progress.responses == (
        response,
        pending.preparation.progress.responses[1],
    )
    assert after.resources.pools == before.resources.pools
    assert after.resources.expended_items == before.resources.expended_items
    assert after.resources.game_time == before.resources.game_time
    assert real_play_clock(after).cooldowns == real_play_clock(before).cooldowns
    assert play.rng.exhausted()
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    saved = await play.store.read(cid)
    assert await TaskService(restarted).execute(cid, command, principal_id="gm") == result
    assert saved == await play.store.read(cid) == await play.store.replay(cid)
    play.rng = RecordedDice((1, 3, 3, 3))
    await TaskService(play).execute(
        cid,
        ChooseOpponentFragment(
            id="accept",
            actor_id="b",
            expected_revision=after.revision,
            pending_id=pending.id,
            choice="accept",
        ),
        principal_id="b",
    )
    final = play._load(await play.store.read(cid))
    assert snapshot(final).pending is None and play.rng.exhausted()
    assert next(pool.current for pool in final.resources.pools if pool.id == "hp:a") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("invalid", ["pending", "current", "duplicate", "previous"])
async def test_invalid_amendment_is_atomic(tmp_path: Path, backend: str, invalid: str) -> None:
    cid, play, pending = await opened(tmp_path, backend, later=invalid != "previous")
    before = await play.store.read(cid)
    state = play._load(before)
    response = BlastResponse(
        actor_id="b" if invalid == "current" else "a", cover_dr=0, size_modifier=0
    )
    command = AmendFragmentResponses(
        id="invalid",
        actor_id="b",
        expected_revision=state.revision,
        pending_id="wrong" if invalid == "pending" else pending.id,
        responses=(response, response) if invalid == "duplicate" else (response,),
    )
    play.rng = RecordedDice(())
    with pytest.raises((ConflictError, ValidationError)):
        await TaskService(play).execute(cid, command, principal_id="gm")
    assert before == await play.store.read(cid) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_amendment_and_selection_race_commits_only_one_revision(
    tmp_path: Path, backend: str
) -> None:
    cid, play, pending = await opened(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    amend = AmendFragmentResponses(
        id="racing-amend",
        actor_id="b",
        expected_revision=before.revision,
        pending_id=pending.id,
        responses=(BlastResponse(actor_id="a", cover_dr=0, size_modifier=-10),),
    )
    select = ChooseOpponentFragment(
        id="racing-select",
        actor_id="b",
        expected_revision=before.revision,
        pending_id=pending.id,
        choice="accept",
    )
    # Both services represent concurrent commands at the same recorded instant.
    # A restarted default fixture clock would run backwards after an amendment wins.
    instant = real_play_clock(before).observed_at_us
    assert instant is not None
    play.instants = lambda: CommandInstant(instant + 1)
    selector = build_play(
        tmp_path,
        play.engine,
        store=play.store,
        rng=RecordedDice((1, 5, 5, 6)),
        instants=play.instants,
    )
    play.rng = RecordedDice(())
    results = await asyncio.gather(
        TaskService(play).execute(cid, amend, principal_id="gm"),
        TaskService(selector).execute(cid, select, principal_id="b"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1, results
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    after = play._load(await play.store.read(cid))
    assert after.revision == before.revision + 1
    records = await play.store.history(cid)
    assert sum(row.command_id in (amend.id, select.id) for row in records) == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_amendment_requires_current_director_even_on_exact_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, pending = await opened(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    command = AmendFragmentResponses(
        id="amend",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        responses=(BlastResponse(actor_id="a", cover_dr=0, size_modifier=1),),
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises((AuthorizationError, ValidationError)):
        await TaskService(play).execute(cid, command, principal_id="b")
    assert before == await play.store.read(cid)
    await TaskService(play).execute(cid, command, principal_id="gm")

    def demote(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "members": tuple(
                    member.model_copy(update={"role": "player", "actor_ids": ("b",)})
                    if member.principal_id == "gm"
                    else member
                    for member in state.members
                )
            }
        )

    await change(play, cid, demote)
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await TaskService(play).execute(cid, command, principal_id="gm")
    assert before == await play.store.read(cid) and play.rng.exhausted()
