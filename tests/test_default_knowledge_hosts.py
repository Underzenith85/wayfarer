"""Actual B208 Staff and B201 Innate Attack consumers obey explicit GM facts."""

import secrets
from pathlib import Path

import pytest
from support.runtime import played
from test_composed_attack_host import declare as declare_composed
from test_composed_attack_host import defense as defend_composed
from test_composed_attack_host import fixture as composed_fixture
from test_default_roll_staff_host import prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.default_knowledge import (
    AdoptDefaultKnowledge,
    DeclareDefaultKnowledge,
)
from wayfarer.engine.simulation.campaign.default_knowledge_policy import PREFIX
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.errors import ValidationError
from wayfarer.orchestration import default_knowledge_generations as generations
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def adopt(
    play: PlayService,
    cid: str,
    known: tuple[str, ...] = (),
    *,
    actor_id: str = "a",
    command_id: str = "society-adopt",
) -> None:
    state = play._load(await play.store.read(cid))
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id=command_id,
            actor_id="gm",
            expected_revision=state.revision,
            performer_id=actor_id,
            known_skill_ids=known,
            reason="The GM explicitly determines the actor's society-known skills",
        ),
        principal_id="gm",
    )


async def staff_attack(play: PlayService, cid: str) -> None:
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="staff-default",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="blade",
            mode_id="staff-thrust",
            target_id="b",
        ),
        principal_id="a",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trained", [False, True])
@pytest.mark.parametrize("adoption", [False, True])
async def test_actual_staff_unknown_fact_refuses_only_untrained_use_before_entropy(
    tmp_path: Path, backend: str, trained: bool, adoption: bool
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, True, trained=trained)
    if adoption:
        await adopt(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    if trained:
        await staff_attack(play, cid)
        pending = play._load(await play.store.read(cid)).encounters[0].pending_defense
        assert pending and play.rng.exhausted()
    else:
        with pytest.raises(ValidationError, match="no trained or legal default"):
            await staff_attack(play, cid)
        assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version,adopt_at_launch", [(0, False), (1, False), (1, True), (2, True)])
async def test_staff_policy_is_launch_bound_and_both_legacy_and_source_commands_reexecute(
    tmp_path: Path,
    backend: str,
    version: int,
    adopt_at_launch: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # These launches are genuinely authored under retained versions 0/1/2.
    monkeypatch.setattr(generations, "ACTIVE", version > 0)
    monkeypatch.setattr(generations, "CURRENT_VERSION", version)
    cid, play, initial = await prepare(tmp_path, backend, True)
    if adopt_at_launch:
        await adopt(play, cid, ("skill:staff",))
    await staff_attack(play, cid)
    if not adopt_at_launch:
        await adopt(play, cid)
    state = play._load(await play.store.read(cid))
    policies = [event for event in state.resources.events if event.id.startswith(PREFIX)]
    assert bool(policies) == adopt_at_launch
    monkeypatch.setattr(generations, "ACTIVE", True)
    monkeypatch.setattr(generations, "CURRENT_VERSION", 2)
    play.rng, play.seeds = secrets, lambda: f"{11:064x}"
    response = ChooseDefense(
        id="resolve",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, response, principal_id="b")
    assert result.injury and result.injury.attack.effective_target == 5
    assert result.injury.attack.total == 5 and result.injury.injury == 2
    final = await play.store.read(cid)
    assert await CombatService(play).execute(cid, response, principal_id="b") == result
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-replay"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_innate_default_cannot_ignore_a_false_fact_by_recreating_dx_fallback(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await composed_fixture(tmp_path, backend, skill_points=0, levels=1)
    await adopt(play, cid, ("skill:innate-attack-beam",))
    state = play._load(await play.store.read(cid))
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="beam-not-known",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            skill_ids=("skill:innate-attack-beam",),
            society_known=False,
            reason="The GM withdraws the prior societal observation before this original",
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="society knowledge"):
        await declare_composed(play, cid, source)
    assert await play.store.read(cid) == before and play.rng.exhausted()
    state = play._load(before)
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="beam-now-known",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            skill_ids=("skill:innate-attack-beam",),
            society_known=True,
            reason="The GM establishes a valid current observation",
        ),
        principal_id="gm",
    )
    await declare_composed(play, cid, source)
    play.rng = RecordedDice((1, 2, 2, 1))
    _, result = await defend_composed(play, cid)
    assert result.injury and result.injury.attack.effective_target == 6
    assert result.injury.injury == 1 and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_recorded_absent_generation_remains_legacy_after_gm_policy_adoption(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, initial = await prepare(tmp_path, backend, True)
    await adopt(play, cid)
    monkeypatch.setattr(generations, "ACTIVE", False)
    await staff_attack(play, cid)
    marker = await play.store.command_input(cid, "staff-default")
    assert marker and marker.text and "default_knowledge_generation" not in marker.text
    monkeypatch.setattr(generations, "ACTIVE", True)
    state = play._load(await play.store.read(cid))
    play.rng, play.seeds = secrets, lambda: f"{11:064x}"
    response = ChooseDefense(
        id="resolve",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, response, principal_id="b")
    assert (
        result.injury and result.injury.attack.effective_target == 5 and result.injury.injury == 2
    )
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "legacy-reexecute"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_composed_launch_retains_adopted_policy_for_current_facts_before_original(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await composed_fixture(tmp_path, backend, skill_points=0, levels=1)
    await adopt(play, cid, ("skill:innate-attack-beam",))
    await declare_composed(play, cid, source)
    state = play._load(await play.store.read(cid))
    assert any(event.id.startswith(PREFIX) for event in state.resources.events)
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="withdraw-before-original",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            skill_ids=("skill:innate-attack-beam",),
            society_known=False,
            reason="Correct the societal fact before this attack's first original",
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="society knowledge"):
        await defend_composed(play, cid)
    assert await play.store.read(cid) == before and play.rng.exhausted()
