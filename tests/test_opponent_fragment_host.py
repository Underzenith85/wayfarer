"""Actual B415 fragment choices preserve prior blast consequences on both stores."""

from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime
from test_prepared_fragment_attack import launched_blast, responses

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.commands import ResolveWeaponExplosion
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.combat.fragment_state import active_fragment
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.opponent_fragment_records import (
    ChooseOpponentFragment,
    PrepareOpponentFragment,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import TaskResult, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock


async def prepare(
    play: PlayService, cid: str, *, secret: bool = False, identifier: str = "fragment"
) -> tuple[PrepareOpponentFragment, TaskResult]:
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    source = ResolveWeaponExplosion(
        id=identifier,
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blast.id,
        responses=responses(),
        object_cover={},
        object_sizes={},
        environment="air",
    )
    command = PrepareOpponentFragment(
        id=identifier,
        actor_id="b",
        expected_revision=state.revision,
        resolution=source,
        launch_command_id="launch",
        secret=secret,
    )
    return command, await TaskService(play).execute(cid, command, principal_id="gm")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
@pytest.mark.parametrize(
    "selected,following,hp", [((5, 5, 6), (), 9), ((3, 3, 3), (3, 3, 3, 1) * 3, 6)]
)
async def test_worst_fragment_check_changes_hits_without_rewriting_prior_blast(
    tmp_path: Path,
    backend: str,
    secret: bool,
    selected: tuple[int, int, int],
    following: tuple[int, ...],
    hp: int,
) -> None:
    cid, play, before, blast = await launched_blast(tmp_path, backend)
    play.rng = RecordedDice((1, 5, 5, 6, 1) + (() if secret else (1, 1, 1)))
    source, begun = await prepare(play, cid, secret=secret)
    assert begun.pending_id and play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 9
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    assert not blasts(state.resources)[0].resolved
    assert state.resources.expended_items == before.resources.expended_items
    assert active_fragment(state.resources, blast.id)
    command = ChooseOpponentFragment(
        id="choice",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=begun.pending_id,
        choice="use-luck",
    )
    play.rng = RecordedDice(((1, 1, 1) if secret else ()) + (2, 2, 2) + selected + following)
    result = await TaskService(play).execute(cid, command, principal_id="b")
    assert result.status == "completed"
    if secret:
        assert result.check is None and result.luck is None and result.combat_json is None
    else:
        assert (
            result.check and result.check.dice == selected and result.check.effective_target == 15
        )
        assert result.luck and result.luck.chosen_index == 2
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == hp
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    assert (
        blasts(state.resources)[0].resolved and active_fragment(state.resources, blast.id) is None
    )
    assert snapshot(state).pending is None and play.rng.exhausted()
    assert {entry.actor_id for entry in real_play_clock(state).cooldowns} == {"b"}
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, command, principal_id="b") == result
    with pytest.raises(ConflictError):
        await prepare(restarted, cid, secret=secret, identifier="late")
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_cancel_resumes_ordinary_blast_without_repeat_injury_or_disclosure(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, blast = await launched_blast(tmp_path, backend)
    play.rng = RecordedDice((1, 5, 5, 6, 1))
    source, begun = await prepare(play, cid, secret=True)
    assert begun.pending_id and play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    cancel = ChooseOpponentFragment(
        id="cancel",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=begun.pending_id,
        choice="cancel",
    )
    await TaskService(play).execute(cid, cancel, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert snapshot(state).pending is None and active_fragment(state.resources, blast.id)
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 9
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice((5, 5, 6)), instants=play.instants
    )
    ordinary = source.resolution.model_copy(
        update={"id": "ordinary", "expected_revision": state.revision}
    )
    result = await CombatService(restarted).execute(cid, ordinary, principal_id="gm")
    after = restarted._load(await restarted.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 9
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 10
    assert (
        blasts(after.resources)[0].resolved and active_fragment(after.resources, blast.id) is None
    )
    assert not snapshot(after).luck.receipts and not real_play_clock(after).cooldowns
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    assert await CombatService(restarted).execute(cid, ordinary, principal_id="gm") == result
    for event in await build_runtime(restarted).events(
        cid, principal_id="b", after=source.expected_revision
    ):
        assert "fragment_attack" not in event.model_dump_json()
        assert "effective_target" not in event.outcome
        assert "opponent-fragment:" not in event.model_dump_json()
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_cancel_reprepare_retains_settled_source_and_prior_injury(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, blast = await launched_blast(tmp_path, backend)
    play.rng = RecordedDice((1, 5, 5, 6, 1))
    source, begun = await prepare(play, cid, secret=True)
    assert begun.pending_id and play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        ChooseOpponentFragment(
            id="cancel",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=begun.pending_id,
            choice="cancel",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    reopening, reopened = await prepare(play, cid, secret=True, identifier="reprepare")
    assert reopened.pending_id and reopened.pending_id != begun.pending_id and play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    cursor = active_fragment(state.resources, blast.id)
    assert cursor and cursor.preparation
    assert cursor.resolution == source.resolution
    assert cursor.preparation.progress.command_id == source.id
    assert reopening.id == "reprepare"
    assert next(pool.current for pool in state.resources.pools if pool.id == "hp:b") == 9
    command = ChooseOpponentFragment(
        id="accept",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=reopened.pending_id,
        choice="accept",
    )
    play.rng = RecordedDice((5, 5, 6))
    result = await TaskService(play).execute(cid, command, principal_id="gm")
    after = play._load(await play.store.read(cid))
    assert next(pool.current for pool in after.resources.pools if pool.id == "hp:b") == 9
    assert blasts(after.resources)[0].resolved and play.rng.exhausted()
    assert active_fragment(after.resources, blast.id) is None
    assert not real_play_clock(after).cooldowns
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await TaskService(restarted).execute(cid, command, principal_id="gm") == result
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)
