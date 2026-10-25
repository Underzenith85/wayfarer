"""Captured B377 policy owns replay, retries and derived mapless feasibility."""

import secrets
from functools import cache
from pathlib import Path

import pytest
from support.runtime import build_play
from test_retreat_postures import FEATURE, Posture, Representation, battlefield, pending

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext, RetreatSpatialFact
from wayfarer.engine.simulation.combat.tactical import pose
from wayfarer.errors import ValidationError
from wayfarer.orchestration import defense_posture_generations, task_combat_generations
from wayfarer.orchestration.combat import (
    CombatService,
    HexPlacement,
    MigrateEncounterBasic,
    MigrateEncounterHex,
    generations,
)
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.task_records import SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands


@cache
def consequence_seed(typed: bool) -> str:
    for number in range(100000):
        value = f"{number:064x}"
        rng = SeededRandom(value)
        attack = 9 if typed else sum(rng.randbelow(6) + 1 for _ in range(3))
        dodge = sum(rng.randbelow(6) + 1 for _ in range(3))
        damage = rng.randbelow(6) + 1
        if attack == 9 and dodge == 10 and damage == 1:
            return value
    raise AssertionError("No retreat source-consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("representation", ["basic", "hex"])
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_exact_recorded_direct_and_task_posture_policy_reexecutes_actual_injury(
    tmp_path: Path,
    backend: str,
    representation: Representation,
    typed: bool,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Old Basic wrongly admitted sitting; old hex wrongly admitted kneeling.
    # Fresh source semantics admit rolling from prone, retaining its −3 penalty.
    posture: Posture = (
        "prone" if enabled else "sitting" if representation == "basic" else "kneeling"
    )
    cid, play, response = await pending(tmp_path, backend, representation, posture)
    pending_id = None
    if typed:
        task = TaskService(play)
        await task.execute(
            cid,
            SetRealPlayClock(
                id="clock",
                actor_id="gm",
                expected_revision=response.expected_revision,
                running=True,
            ),
            principal_id="gm",
        )
        state = play._load(await play.store.read(cid))
        incoming = state.encounters[0].pending_defense
        assert incoming
        play.rng = RecordedDice([3, 3, 3])
        opened = await task.execute(
            cid,
            BeginOpponentAttack(
                id="original",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                attack_id=incoming.id,
            ),
            principal_id="gm",
        )
        pending_id = opened.pending_id
        assert pending_id
        state = play._load(await play.store.read(cid))
        response = response.model_copy(update={"expected_revision": state.revision})
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
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
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", enabled)
    play.rng, play.seeds = secrets, lambda: consequence_seed(typed)
    service = TaskService(play) if typed else CombatService(play)
    result = await service.execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    updated = play._load(final)
    assert updated.encounters[0].participants[1].posture == posture
    assert next(p.current for p in updated.resources.pools if p.id == "hp:b") == (
        7 if enabled else 10
    )
    records = (await play.store.history(cid))[count:]
    raw = records[0].command_input
    assert raw
    payload = replay_payload(raw)
    assert isinstance(payload, dict)
    key = task_combat_generations.KEY if typed else generations.KEY
    assert (FEATURE in payload[key]) == enabled
    monkeypatch.setattr(module, "ACTIVE", features - {FEATURE} if enabled else features)
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", not enabled)
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id == command.id],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "retreat-seed-only"),
    )
    assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
    assert replayed == final
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    service = TaskService(restarted) if typed else CombatService(restarted)
    assert await service.execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_historical_hex_prone_keeps_original_refusal(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, command = await pending(tmp_path, backend, "hex", "prone")
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {FEATURE})
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Retreat is unavailable"):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("representation", ["basic", "hex"])
async def test_forbidden_kneeling_retreat_grants_no_bonus_to_a_later_ordinary_defense(
    tmp_path: Path, backend: str, representation: Representation
) -> None:
    cid, play, command = await pending(tmp_path, backend, representation, "kneeling")
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Retreat is unavailable"):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3, 1])
    result = await CombatService(play).execute(
        cid,
        command.model_copy(update={"id": "ordinary", "basic_retreat": False, "retreat": None}),
        principal_id="b",
    )
    assert result.injury and result.injury.defense
    assert (
        result.injury.defense.effective_target == 7 and not result.injury.defense.outcome.succeeded
    )
    assert result.injury.injury == 3 and result.injury.hp_after == 7 and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture", ["standing", "kneeling", "prone"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_actual_basic_hex_roundtrip_uses_captured_source_posture_feasibility(
    tmp_path: Path, backend: str, posture: Posture, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, _ = await pending(tmp_path, backend, "hex", posture, attack_pending=False)
    state = play._load(await play.store.read(cid))
    placements = tuple(
        HexPlacement(actor_id=actor.actor_id, pose=pose(actor))
        for actor in state.encounters[0].participants
    )
    features = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", features if enabled else features - {FEATURE})
    play.rng = RecordedDice([])
    await CombatService(play).execute(
        cid,
        MigrateEncounterBasic(
            id="project-basic",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    spatial = state.encounters[0].spatial
    assert isinstance(spatial, BasicSpatialContext)
    fact = spatial.active("retreat", "b", "a")
    assert isinstance(fact, RetreatSpatialFact)
    expected = posture != "kneeling" if enabled else posture != "prone"
    assert fact.feasible == expected
    await CombatService(play).execute(
        cid,
        MigrateEncounterHex(
            id="restore-hex",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            battlefield=battlefield(),
            placements=placements,
        ),
        principal_id="gm",
    )
    restored = await play.store.read(cid)
    play = play.for_campaign(restored)
    state = play._load(restored)
    assert state.encounters[0].spatial_kind == "hex"
    assert state.encounters[0].participants[1].posture == posture
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
