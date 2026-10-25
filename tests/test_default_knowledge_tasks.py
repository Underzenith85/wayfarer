"""Prepared ordinary and secret originals preserve B173 producer semantics."""

import secrets
from pathlib import Path

import pytest
from test_default_knowledge_hosts import adopt
from test_live_skill_prerequisite_context import background_fixture, reexecute, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.errors import ValidationError
from wayfarer.orchestration import default_knowledge_generations as generations
from wayfarer.orchestration.task_records import (
    BeginTaskCheck,
    ChooseSecretTaskCheck,
    ChooseTaskCheck,
    PrepareSecretTaskCheck,
    SecretTaskPending,
    SetRealPlayClock,
    snapshot,
)
from wayfarer.orchestration.tasks import TaskService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
@pytest.mark.parametrize("adoption", [False, True])
async def test_unknown_default_is_refused_before_task_time_cost_or_original_entropy(
    tmp_path: Path, backend: str, secret: bool, adoption: bool
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=False, authored_modifier=0
    )
    cid = initial["id"]
    if adoption:
        await adopt(play, cid)
    tasks = TaskService(play)
    await tasks.execute(
        cid,
        SetRealPlayClock(
            id="clock", actor_id="gm", expected_revision=await revision(play, cid), running=True
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    command = (PrepareSecretTaskCheck if secret else BeginTaskCheck)(
        id="prepare", actor_id="a", expected_revision=await revision(play, cid), check_id="inspect"
    )
    with pytest.raises(ValidationError, match="GM-known default"):
        await tasks.execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [0, 1, 2])
async def test_secret_predeclaration_pins_its_own_policy_before_fresh_resolution(
    tmp_path: Path, backend: str, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=False, authored_modifier=0
    )
    cid = initial["id"]
    await adopt(play, cid, () if version == 0 else ("skill:observation",))
    tasks = TaskService(play)
    await tasks.execute(
        cid,
        SetRealPlayClock(
            id="clock", actor_id="gm", expected_revision=await revision(play, cid), running=True
        ),
        principal_id="gm",
    )
    monkeypatch.setattr(generations, "ACTIVE", version > 0)
    monkeypatch.setattr(generations, "CURRENT_VERSION", version)
    prepared = await tasks.execute(
        cid,
        PrepareSecretTaskCheck(
            id="prepare",
            actor_id="a",
            expected_revision=await revision(play, cid),
            check_id="inspect",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, SecretTaskPending)
    assert pending.default_knowledge_generation == (version or None)
    assert prepared.pending_id == pending.id
    monkeypatch.setattr(generations, "ACTIVE", True)
    monkeypatch.setattr(generations, "CURRENT_VERSION", 2)
    play.rng, play.seeds = secrets, lambda: f"{11:064x}"
    result = await tasks.execute(
        cid,
        ChooseSecretTaskCheck(
            id="resolve",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="resolve",
        ),
        principal_id="gm",
    )
    assert result.secret and result.check and result.check.base_target == 5
    final = play._load(await play.store.read(cid))
    assert "clue" in {fact.id for fact in final.world.perspective("a").facts}
    assert snapshot(final).pending is None
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_recorded_ordinary_original_keeps_its_source_default_under_fresh_flag_change(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=False, authored_modifier=0
    )
    cid = initial["id"]
    await adopt(play, cid, ("skill:observation",))
    tasks = TaskService(play)
    await tasks.execute(
        cid,
        SetRealPlayClock(
            id="clock", actor_id="gm", expected_revision=await revision(play, cid), running=True
        ),
        principal_id="gm",
    )
    play.rng, play.seeds = secrets, lambda: f"{11:064x}"
    original = await tasks.execute(
        cid,
        BeginTaskCheck(
            id="original",
            actor_id="a",
            expected_revision=await revision(play, cid),
            check_id="inspect",
        ),
        principal_id="gm",
    )
    assert original.check and original.check.base_target == original.check.total == 5
    assert original.pending_id
    monkeypatch.setattr(generations, "ACTIVE", False)
    result = await tasks.execute(
        cid,
        ChooseTaskCheck(
            id="accept",
            actor_id="a",
            expected_revision=await revision(play, cid),
            pending_id=original.pending_id,
            kind="accept-check",
        ),
        principal_id="a",
    )
    assert result.action and result.action.check == original.check
    assert result.action.revealed_fact_ids == ("clue",)
    await reexecute(initial, play, tmp_path / "reexecute")
