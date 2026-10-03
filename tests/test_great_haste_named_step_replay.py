"""Generation four stays private and reexecutes actual selected movement/casting."""

import json
import secrets
from pathlib import Path

import pytest
from test_great_haste_named_step import command, other_turns
from test_great_haste_named_step_wait import paused_named_step
from test_great_haste_named_subject import prepare_named

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.great_haste_named import HOST_ADAPTER
from wayfarer.engine.simulation.magic.great_haste_step_state import PREFIX
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.great_haste_generation import KEY, ORIGINAL, features
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.views import campaign_view
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_named_step_seeded_full_cast_and_exact_retry(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=False)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, lambda: "00" * 32
    service = GreatHasteService(play)
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await service.execute(cid, command(state.revision, index), principal_id="alice")
    await other_turns(cid, play)
    state = play._load(await play.store.read(cid))
    last = command(state.revision, 2, blocked=False)
    receipt = await service.execute(cid, last, principal_id="alice")
    assert receipt.outcome == "active"
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert len(checks) == 5 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)
    restarted = PlayService(play.store, play.engine)
    restarted.rng = RecordedDice([])
    assert await GreatHasteService(restarted).execute(cid, last, principal_id="alice") == receipt
    with pytest.raises(ConflictError):
        await GreatHasteService(restarted).execute(
            cid,
            last.model_copy(update={"known_fact_id": "other"}),
            principal_id="alice",
        )
    assert await play.store.read(cid) == final
    state = play._load(final)
    for member in state.members:
        projection = json.dumps(campaign_view(state, member, play.engine.rules.combat))
        assert PREFIX not in projection and "named_origin_json" not in projection


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_named_step_paused_start_lease_resumes_once_after_restart(
    tmp_path: Path, backend: str
) -> None:
    cid, play, selected = await paused_named_step(tmp_path, backend, continuing=False, seeded=True)
    initial = await play.store.replay(cid, selected.expected_revision)
    count = len(
        [
            r
            for r in await play.store.history(cid)
            if r.expected_revision < selected.expected_revision
        ]
    )
    paused = await play.store.read(cid)
    state = play._load(paused)
    assert "named" not in latest(state.resources)
    restarted = PlayService(play.store, play.engine)
    restarted.rng, restarted.seeds = secrets, lambda: "00" * 32
    prior = await GreatHasteService(restarted).execute(cid, selected, principal_id="alice")
    assert prior.outcome == "paused" and await play.store.read(cid) == paused
    await CombatService(restarted).execute(
        cid,
        TakeCombatTurn(
            id="decline",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    resumed = ResumeInterruptedTurn(
        id="resume",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
    )
    result = await CombatService(restarted).execute(cid, resumed, principal_id="a")
    final = await play.store.read(cid)
    assert latest(play._load(final).resources)["named"].concentration_seconds == 1
    assert await CombatService(restarted).execute(cid, resumed, principal_id="a") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "paused-seed-only"),
    )
    assert len(checks) == 3 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)


@pytest.mark.parametrize("generation", [1, 2, 3, 5, True, 4.0])
def test_named_step_generation_requires_exact_typed_pair(generation: int | bool | float) -> None:
    payload = {
        "operation": "great-haste",
        "generation": generation,
        "principal_id": "alice",
        "command": command(1, 0).model_dump(mode="json"),
    }
    raw = json.dumps(payload, sort_keys=True)
    wrapped = json.dumps({**payload, KEY: 1, ORIGINAL: raw}, sort_keys=True)
    with pytest.raises(ValidationError, match="casting generation"):
        features(CommandInput(payload_digest({"input": wrapped}), wrapped))


def test_named_step_generation_four_is_authenticated_and_private() -> None:
    payload = {
        "operation": "great-haste",
        "generation": 4,
        "principal_id": "alice",
        "command": command(1, 0).model_dump(mode="json"),
    }
    raw = json.dumps(payload, sort_keys=True)
    wrapped = json.dumps({**payload, KEY: 1, ORIGINAL: raw}, sort_keys=True)
    assert features(CommandInput(payload_digest({"input": wrapped}), wrapped))
    assert not features(CommandInput(payload_digest({"input": raw}), raw))
    assert (
        HOST_ADAPTER.validate_json(json.dumps(payload["command"])).kind == "named-step-great-haste"
    )
