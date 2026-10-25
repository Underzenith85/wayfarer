"""B345 refuses an authored nondefense attempt below final effective skill 3."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, played
from test_enchanting_noncombat_nighttime import configured, start_choice

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.noncombat import NoncombatService
from wayfarer.orchestration.noncombat_generations import minimum_effective_skill
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("equipment,authored", [(0, -15), (1, -16), (-1, -14)])
async def test_final_effective_skill_two_refuses_before_entropy_and_all_effects(
    tmp_path: Path, backend: str, equipment: int, authored: int
) -> None:
    initial, play, _ = await configured(
        tmp_path,
        backend,
        enroll=False,
        equipment_modifier=equipment,
        check_modifier=authored,
    )
    cid = initial["id"]
    command = await start_choice(play, cid)
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    dice = RecordedDice(())
    play.rng = dice
    with pytest.raises(ValidationError, match="effective skill must be at least 3"):
        await NoncombatService(play).execute(cid, command, principal_id="a")
    assert dice.exhausted()
    assert await play.store.read(cid) == before
    assert await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, command.id) is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("equipment,authored", [(0, -14), (1, -15), (-1, -13)])
async def test_final_effective_skill_three_can_succeed_and_spends_real_costs(
    tmp_path: Path, backend: str, equipment: int, authored: int
) -> None:
    initial, play, _ = await configured(
        tmp_path,
        backend,
        enroll=False,
        equipment_modifier=equipment,
        check_modifier=authored,
    )
    cid = initial["id"]
    command = await start_choice(play, cid)
    play.rng = RecordedDice((1, 1, 1))
    result = await NoncombatService(play).execute(cid, command, principal_id="a")
    assert result.status == "success"
    assert result.checks[0].effective_target == result.checks[0].total == 3
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 10
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    assert "night-clue" in {f.id for f in state.world.perspective("a").facts}
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await NoncombatService(restarted).execute(cid, command, principal_id="a") == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("current", [False, True])
async def test_current_and_unmarked_historical_attempts_retry_and_seed_reexecute(
    tmp_path: Path, backend: str, current: bool
) -> None:
    initial, play, genesis = await configured(
        tmp_path, backend, enroll=False, check_modifier=-14 if current else -15
    )
    cid = initial["id"]
    command = await start_choice(play, cid)
    service = NoncombatService(play)
    if current:
        first, duplicate = await asyncio.gather(
            *(service.execute(cid, command, principal_id="a") for _ in range(2))
        )
        assert first == duplicate
    else:
        # Earlier enchanting-rest commands did not capture the independent floor.
        first = await submit(
            play, cid, service.plan(command, enchanting_rest=True), principal_id="a"
        )
    assert first.checks[0].effective_target == (3 if current else 2)
    assert first.status == "failure" and first.checks[0].total == 9
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 10
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 9
    stored = await play.store.command_input(cid, command.id)
    assert stored is not None and stored.text is not None
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict)
    assert payload["enchanting_rest_generation"] == 1
    assert payload.get("minimum_effective_skill_generation") == (1 if current else None)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await NoncombatService(restarted).execute(cid, command, principal_id="a") == first
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


async def test_floor_does_not_bypass_authority_or_revision_checks(tmp_path: Path) -> None:
    initial, play, _ = await configured(tmp_path, enroll=False, check_modifier=-15)
    cid = initial["id"]
    command = await start_choice(play, cid)
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError), match="actor|authorized"):
        await NoncombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await NoncombatService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("generation", [True, 2, "1", None])
async def test_invalid_recorded_floor_generation_refuses(
    tmp_path: Path, generation: object
) -> None:
    initial, play, _ = await configured(tmp_path, enroll=False)
    cid = initial["id"]
    command = await start_choice(play, cid)
    await NoncombatService(play).execute(cid, command, principal_id="a")
    record = next(r for r in await played(play.store, cid) if r.command_id == command.id)
    assert record.command_input is not None
    payload = replay_payload(record.command_input)
    assert isinstance(payload, dict)
    payload["minimum_effective_skill_generation"] = generation
    encoded = json.dumps(payload, sort_keys=True)
    changed = replace(
        record, command_input=encoded, payload_hash=payload_digest({"input": encoded})
    )
    with replay_inputs(changed):
        with pytest.raises(ValidationError, match="minimum-effective-skill generation"):
            await minimum_effective_skill(play, cid, command.id)
