"""B345 final skill admission for real public Inspect and Social commands."""

import asyncio
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_prepared_task_checks import gurps_state, ordinary_command

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.default_knowledge import (
    AdoptDefaultKnowledge,
    known_skills,
    observations,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands


async def fixture(
    tmp_path: Path, backend: str, target: int, *, known_observation: bool = False
) -> tuple[Campaign, PlayService, PlayState]:
    original, foundation = gurps_state()
    # Observation defaults to Per14-5=9; authored darkness is -2. Purchased
    # Diplomacy has level10. The literal task modifiers produce targets2/3.
    reducer = ActionEngine(
        original.reviewer,
        original.resources,
        original.rules.model_copy(
            update={
                "checks": tuple(
                    r.model_copy(
                        update={
                            "modifier": target - (7 if r.action == "inspect" else 10),
                            "duration": 4,
                        }
                    )
                    for r in original.rules.checks
                ),
            }
        ),
    )
    play = build_play(tmp_path, reducer, backend=backend, seeds=lambda: f"{1:064x}")
    initial = campaign(reducer)
    genesis = play.initial_state(
        initial,
        foundation.world,
        foundation.resources,
        tuple(
            ActorSetup.model_validate(a.model_dump(exclude={"approval"})) for a in foundation.actors
        ),
    )
    initial["play_json"] = genesis.model_dump_json()
    await seed_campaign(play.store, initial)
    if known_observation:
        await declare_observation(play, initial["id"])
    return initial, play, genesis


async def declare_observation(play: PlayService, cid: str) -> None:
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="observation-society-knowledge",
            actor_id="gm",
            expected_revision=(await play.store.read(cid))["revision"],
            performer_id="a",
            known_skill_ids=("skill:observation",),
            reason="The GM confirms Observation is known for this actor's explicit Inspect scenario",
        ),
        principal_id="gm",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["inspect", "social"])
async def test_preview_and_execute_reject_final_two_without_any_effect(
    tmp_path: Path, backend: str, kind: Literal["inspect", "social"]
) -> None:
    initial, play, _ = await fixture(tmp_path, backend, 2, known_observation=kind == "inspect")
    cid = initial["id"]
    command = ordinary_command(kind, (await play.store.read(cid))["revision"])
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    dice = RecordedDice(())
    play.rng = dice
    preview = await play.preview(cid, command, principal_id="a")
    result = await play.execute(cid, command, principal_id="a")
    assert preview == result
    assert result.status == "rejected" and result.code == "check.minimum_skill"
    assert result.check is None and dice.exhausted()
    assert await play.store.read(cid) == before and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, command.id) is None
    # The transaction guard independently checks the same prepared final target.
    with pytest.raises(ValidationError, match="effective skill must be at least 3"):
        play.plan(command, before, minimum_effective_skill=True).resolve(before.copy())
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind,fact", [("inspect", "clue"), ("social", "promise")])
async def test_final_three_uses_actual_darkness_and_advances_once_on_success(
    tmp_path: Path, backend: str, kind: Literal["inspect", "social"], fact: str
) -> None:
    initial, play, _ = await fixture(tmp_path, backend, 3, known_observation=kind == "inspect")
    cid = initial["id"]
    command = ordinary_command(kind, (await play.store.read(cid))["revision"])
    play.rng = RecordedDice((1, 1, 1))
    assert (await play.preview(cid, command, principal_id="a")).status == "feasible"
    result = await play.execute(cid, command, principal_id="a")
    assert result.check is not None and result.check.effective_target == result.check.total == 3
    assert result.revealed_fact_ids == (fact,)
    if kind == "inspect":
        assert [(m.reason, m.value) for m in result.check.modifiers] == [
            ("inspect", -4),
            ("inspect:darkness", -2),
        ]
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 4
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    assert fact in {f.id for f in state.world.perspective("a").facts}
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await restarted.execute(cid, command, principal_id="a") == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["inspect", "social"])
@pytest.mark.parametrize("current", [False, True])
async def test_current_and_historical_public_checks_keep_seeded_retry_and_reexecution(
    tmp_path: Path, backend: str, kind: Literal["inspect", "social"], current: bool
) -> None:
    initial, play, genesis = await fixture(
        tmp_path, backend, 3 if current else 2, known_observation=kind == "inspect"
    )
    cid = initial["id"]
    current_campaign = await play.store.read(cid)
    command = ordinary_command(kind, current_campaign["revision"])
    if current:
        result, duplicate = await asyncio.gather(
            *(play.execute(cid, command, principal_id="a") for _ in range(2))
        )
        assert result == duplicate
    else:
        result = await submit(play, cid, play.plan(command, current_campaign), principal_id="a")
    assert result.check is not None and result.check.effective_target == (3 if current else 2)
    assert result.check.total == 9 and not result.check.outcome.succeeded
    assert result.revealed_fact_ids == ()
    stored = await play.store.command_input(cid, command.id)
    assert stored is not None and stored.text is not None
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict)
    assert payload.get("minimum_effective_skill_generation") == (1 if current else None)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await restarted.execute(cid, command, principal_id="a") == result
    assert await play.store.command_input(cid, command.id) == stored
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=genesis.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid) == await play.store.replay(cid)


async def test_minimum_preview_and_execution_retain_authority_and_revision(tmp_path: Path) -> None:
    initial, play, _ = await fixture(tmp_path, "sqlite", 2)
    cid = initial["id"]
    command = ordinary_command("inspect")
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="authorized"):
        await play.preview(cid, command, principal_id="b")
    with pytest.raises(ValidationError, match="authorized"):
        await play.execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError, match="revision"):
        await play.execute(
            cid, command.model_copy(update={"expected_revision": 1}), principal_id="a"
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unknown_observation_refuses_then_explicit_fact_admits_same_source_target(
    tmp_path: Path, backend: str
) -> None:
    initial, play, genesis = await fixture(tmp_path, backend, 3)
    cid = initial["id"]
    before = await play.store.read(cid)
    command = ordinary_command("inspect", before["revision"])
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    assert not observations(genesis.resources)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="GM-known default"):
        await play.preview(cid, command, principal_id="a")
    with pytest.raises(ValidationError, match="GM-known default"):
        await play.execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before and play.rng.exhausted()
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, command.id) is None
    play.rng = secrets
    await declare_observation(play, cid)
    known = play._load(await play.store.read(cid))
    assert known_skills(known, "a") == frozenset({"skill:observation"})
    assert not known_skills(known, "b")
    assert known.actors == genesis.actors and known.resources.game_time == 0
    command = ordinary_command("inspect", known.revision)
    assert (await play.preview(cid, command, principal_id="a")).status == "feasible"
    result = await play.execute(cid, command, principal_id="a")
    assert result.check and result.check.effective_target == 3 and result.check.total == 9
    assert not result.check.outcome.succeeded and result.revealed_fact_ids == ()
    assert [(m.reason, m.value) for m in result.check.modifiers] == [
        ("inspect", -4),
        ("inspect:darkness", -2),
    ]
    final = play._load(await play.store.read(cid))
    assert final.resources.game_time == 4
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=genesis.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "known-reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)
