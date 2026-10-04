"""Actual pending positive-health/Reflexes Rooted Dodge preserves protocol boundaries."""

import json
from pathlib import Path

import pytest
from support.rooted_dodge_composition import fixture, prepare
from support.rooted_feet import revision
from support.runtime import build_runtime

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService


async def pending(path: Path, backend: str) -> tuple[str, PlayService, ChooseDefense]:
    cid, play, _ = await fixture(path, backend, hp=3, fp=3, cr=True)
    await prepare(play, cid)
    return (
        cid,
        play,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="dodge",
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_composition_unauthorized_and_stale_refuse_without_dice(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await pending(tmp_path, backend)
    before, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    play.rng = RecordedDice(())
    with pytest.raises((AuthorizationError, ValidationError)):
        await build_runtime(play).submit_json(
            cid, command.model_dump(mode="json"), principal_id="alice"
        )
    with pytest.raises(ConflictError):
        await build_runtime(play).submit_json(
            cid,
            command.model_copy(
                update={"expected_revision": command.expected_revision - 1}
            ).model_dump(mode="json"),
            principal_id="bob",
        )
    assert play.rng.exhausted()
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_scored_candidate_rolls_back_at_commit_boundary(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, command = await pending(tmp_path, backend)
    before, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    original = PlayService.commit
    visited = []

    def refuse(self: PlayService, campaign: Campaign, state: PlayState) -> None:
        result = state.last_combat_result
        assert (
            result is not None and result.injury is not None and result.injury.defense is not None
        )
        assert result.injury.defense.effective_target == 2
        original(self, campaign, state)
        visited.append(state.revision)
        # Explicit late commit-conflict injection, not a claim of a concurrent
        # external writer: the real store transaction must discard its candidate.
        raise ConflictError("injected late candidate commit conflict")

    play.rng = RecordedDice((3, 3, 3, 1, 1, 1, 3, 3, 3))
    with monkeypatch.context() as patch:
        patch.setattr(PlayService, "commit", refuse)
        with pytest.raises(ConflictError, match="late candidate"):
            await CombatService(play).execute(cid, command, principal_id="b")
    assert visited == [command.expected_revision + 1]
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)
    assert play._load(before).encounters[0].pending_defense is not None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_completed_composition_does_not_project_private_defense_trace(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await pending(tmp_path, backend)
    play.rng = RecordedDice((3, 3, 3, 1, 1, 1, 3, 3, 3))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 2
    before = await play.store.read(cid)
    runtime = build_runtime(play)
    for route in ("campaign", "stream"):
        for principal in ("alice", "bob", "cora", "watcher"):
            projected = await runtime.project(route, cid, principal_id=principal)
            encoded = json.dumps(projected)
            assert "effective_target" not in encoded and "base_target" not in encoded
            assert "combat_protocol_features" not in encoded and "rooted-feet:" not in encoded
            assert "last_combat_result" not in encoded
        gm = await runtime.project(route, cid, principal_id="gm")
        assert gm["role"] == "gm"
    # No assertion treats the legitimately purchased Combat Reflexes trait or
    # actor's own health as secret; only canonical rolled/private proof is masked.
    assert await play.store.read(cid) == before == await play.store.replay(cid)
