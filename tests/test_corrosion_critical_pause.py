"""Corrosion criticals retain native dice across retries and explicit GM policy."""

import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, played
from test_composed_attack_host import declare
from test_corrosion_composed_host import fixture

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.equipment.corrosion_state import erosion
from wayfarer.engine.simulation.traits.composed_records import ContinueComposedCritical
from wayfarer.engine.simulation.traits.innate_criticals import (
    innate_critical_outcome,
    load_innate_critical,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.owner_damage_records import PrepareOwnerDamage
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


async def establish_beam_knowledge(play: PlayService, cid: str) -> None:
    """The GM supplies the actual cultural fact for this unpaid Beam default."""
    state = play._load(await play.store.read(cid))
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="gm-beam-knowledge",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            known_skill_ids=("skill:innate-attack-beam",),
            reason="The GM confirms Innate Attack (Beam) is known in the actor's society",
        ),
        principal_id="gm",
    )


async def prepare_response(
    play: PlayService, cid: str, task: bool
) -> ChooseDefense | PrepareOwnerDamage:
    state = play._load(await play.store.read(cid))
    response = ChooseDefense(
        id="critical-response",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    return (
        PrepareOwnerDamage(
            id=response.id, actor_id="b", expected_revision=state.revision, response=response
        )
        if task
        else response
    )


async def execute(play: PlayService, cid: str, value: ChooseDefense | PrepareOwnerDamage) -> object:
    if isinstance(value, PrepareOwnerDamage):
        return await TaskService(play).execute(cid, value, principal_id="bob")
    return await CombatService(play).execute(cid, value, principal_id="bob")


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["hit", "miss"])
@pytest.mark.parametrize("task", [False, True])
async def test_native_critical_pause_retains_original_table_seed_and_gm_continuation(
    tmp_path: Path,
    backend: str,
    kind: Literal["hit", "miss"],
    task: bool,
) -> None:
    cid, play, source, _ = await fixture(
        tmp_path, backend, native_criticals=kind != "hit", innate_beam_skill=True
    )
    await establish_beam_knowledge(play, cid)
    if task:
        state = play._load(await play.store.read(cid))
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id="clock", actor_id="gm", expected_revision=state.revision, running=True
            ),
            principal_id="gm",
        )
    await declare(play, cid, source)
    command = await prepare_response(play, cid, task)
    before = play._load(await play.store.read(cid))
    dice = (1, 1, 1, 1, 1, 1) if kind == "hit" else (6, 6, 6, 1, 2, 2, 2, 2, 2)
    play.rng = RecordedDice(dice)
    result = await execute(play, cid, command)
    assert play.rng.exhausted()
    paused = await play.store.read(cid)
    state = play._load(paused)
    encounter = state.encounters[0]
    pending = encounter.pending_defense
    assert encounter.blocked_reason and pending and pending.attack_roll
    assert pending.attack_roll.dice == dice[:3]
    assert pending.attack_roll.outcome == (
        Outcome.CRITICAL_SUCCESS if kind == "hit" else Outcome.CRITICAL_FAILURE
    )
    critical_id = encounter.blocked_reason.removeprefix("innate-critical:")
    capture = load_innate_critical(state.resources, critical_id)
    assert capture and capture.critical_kind == kind and capture.attack == pending.attack_roll
    assert capture.table_rolls == (((1, 1, 1),) if kind == "hit" else ((1, 2, 2), (2, 2, 2)))
    assert innate_critical_outcome(state.resources, critical_id).status == "awaiting-adjudication"
    assert (
        state.resources.pools == before.resources.pools
        and erosion(state.resources, "plate-b") is None
    )
    assert snapshot(state).pending is None
    history = await played(play.store, cid)
    record = history[-1]
    assert record.command_id == command.id and record.entropy_seed
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await execute(restarted, cid, command) == result
    assert await played(play.store, cid) == history and await play.store.read(cid) == paused
    changed = command.model_copy(update={"id": "reroll", "expected_revision": state.revision})
    if isinstance(changed, PrepareOwnerDamage):
        changed = changed.model_copy(
            update={
                "response": changed.response.model_copy(
                    update={
                        "id": "reroll",
                        "expected_revision": state.revision,
                    }
                )
            }
        )
    with pytest.raises((ValidationError, ConflictError)):
        await execute(restarted, cid, changed)
    assert await play.store.read(cid) == paused and await played(play.store, cid) == history
    continuation = ContinueComposedCritical(
        id="gm-policy",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        pending_id=pending.id,
        critical_id=critical_id,
        context_digest=capture.digest,
        policy_id="corrosion-critical-campaign-policy",
        reason="Use an explicit timed source disable while the native effect is unsupported",
        effect="disable-source",
        duration_seconds=60,
    )
    with pytest.raises((AuthorizationError, ValidationError)):
        await ComposedAttackService(restarted).execute(cid, continuation, principal_id="alice")
    resolved = await ComposedAttackService(restarted).execute(cid, continuation, principal_id="gm")
    final = await play.store.read(cid)
    state = play._load(final)
    assert (
        state.encounters[0].blocked_reason is None and state.encounters[0].pending_defense is None
    )
    outcome = innate_critical_outcome(state.resources, critical_id)
    assert outcome.status == "resolved" and outcome.basis == "gm-adjudication"
    assert (
        outcome.effect == "disable-source"
        and outcome.disabled_until == capture.context.created_at + 60
    )
    assert load_innate_critical(state.resources, critical_id) == capture
    assert [(p.id, p.current, p.maximum) for p in state.resources.pools] == [
        (p.id, p.current, p.maximum) for p in before.resources.pools
    ]
    assert erosion(state.resources, "plate-b") is None
    assert resolved.attack is None
    assert (
        await ComposedAttackService(restarted).execute(cid, continuation, principal_id="gm")
        == resolved
    )
    state = play._load(await play.store.read(cid))
    await CombatService(restarted).execute(
        cid,
        TakeCombatTurn(
            id="wait-b",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    before_disabled = await play.store.read(cid)
    with pytest.raises(ValidationError, match="still disabled"):
        await declare(restarted, cid, source, identifier="disabled-use")
    assert await play.store.read(cid) == before_disabled


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind,seed", [("hit", 103), ("miss", 540)])
@pytest.mark.parametrize("task", [False, True])
async def test_native_paused_critical_and_policy_reexecute_from_genesis(
    tmp_path: Path,
    backend: str,
    kind: str,
    seed: int,
    task: bool,
) -> None:
    cid, play, source, initial = await fixture(
        tmp_path, backend, native_criticals=kind != "hit", innate_beam_skill=True
    )
    await establish_beam_knowledge(play, cid)
    if task:
        state = play._load(await play.store.read(cid))
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id="clock", actor_id="gm", expected_revision=state.revision, running=True
            ),
            principal_id="gm",
        )
    await declare(play, cid, source)
    play.rng = secrets
    play.seeds = lambda: f"{seed:064x}"
    await execute(play, cid, await prepare_response(play, cid, task))
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert (
        encounter.blocked_reason
        and encounter.pending_defense
        and encounter.pending_defense.attack_roll
    )
    critical_id = encounter.blocked_reason.removeprefix("innate-critical:")
    capture = load_innate_critical(state.resources, critical_id)
    assert capture and capture.critical_kind == kind
    await ComposedAttackService(play).execute(
        cid,
        ContinueComposedCritical(
            id="gm-policy",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=encounter.pending_defense.id,
            critical_id=critical_id,
            context_digest=capture.digest,
            policy_id="corrosion-critical-campaign-policy",
            reason="Explicit source disable pending a supported native critical consumer",
            effect="disable-source",
            duration_seconds=60,
        ),
        principal_id="gm",
    )
    final = await play.store.read(cid)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("native", [False, True])
async def test_native_normal_damage_critical_hit_erodes_armor_without_dodge_roll(
    tmp_path: Path, backend: str, native: bool
) -> None:
    cid, play, source, _ = await fixture(
        tmp_path, backend, native_criticals=native, innate_beam_skill=True
    )
    await establish_beam_knowledge(play, cid)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="critical",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
    )
    play.rng = RecordedDice((1, 1, 1, 3, 3, 3, 2, 3))
    result = await CombatService(play).execute(cid, command, principal_id="bob")
    state = play._load(await play.store.read(cid))
    loss = erosion(state.resources, "plate-b")
    assert result.injury and result.injury.attack.outcome is Outcome.CRITICAL_SUCCESS
    assert result.injury.defense is None and result.injury.critical_table == (3, 3, 3)
    assert result.injury.injury == 0 and loss and loss.total_loss == 1
    assert (
        state.encounters[0].blocked_reason is None and state.encounters[0].pending_defense is None
    )
    assert play.rng.exhausted()
