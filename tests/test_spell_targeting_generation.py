"""New targeting evidence must preserve old command replay and live retries."""

from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_spell_bindings import command as spell_command

from scripts.replay_fixtures import FIXTURES, ReplayFixture, engine_for
from wayfarer import contracts, validation
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.events import fold
from wayfarer.persistence.replay import command_text


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_retry_and_new_capture_keep_their_recorded_generation(
    tmp_path: Path, backend: str
) -> None:
    fixture = ReplayFixture.model_validate_json((FIXTURES / "spell.json").read_text())
    engine = await engine_for("spell", tmp_path / "engine")
    initial = contracts.campaign(validation.decode(fixture.initial_json))
    play = build_play(tmp_path, engine, backend=backend)
    await seed_campaign(play.store, initial)
    before = initial
    for entry in fixture.commands:
        expected = fold(before, list(entry.events))
        await execute_recorded(play, entry.record(before, expected))
        before = await play.store.read(initial["id"])
        assert before == expected

    cid = initial["id"]
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    first = next(record for record in history if record.command_id == "spell-0")
    assert "targeting_generation" not in command_text(first)
    assert not any(
        event.id.startswith("casting-targeting:") for event in play._load(before).resources.events
    )
    service = SpellService(play)
    await service.execute(cid, spell_command(0), principal_id="a")
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == stream

    await service.execute(
        cid,
        spell_command(before["revision"]).model_copy(
            update={"id": "finish-historical", "kind": "complete"}
        ),
        principal_id="a",
    )
    before = await play.store.read(cid)
    new_cast = spell_command(before["revision"]).model_copy(
        update={"id": "new-targeting", "cast_id": "new-cast", "kind": "start"}
    )
    result = await service.execute(cid, new_cast, principal_id="a")
    after = await play.store.read(cid)
    assert any(
        event.id.startswith("casting-targeting:") for event in play._load(after).resources.events
    )
    current = next(
        record for record in await play.store.history(cid) if record.command_id == new_cast.id
    )
    assert validation.mapping(validation.decode(command_text(current)))["targeting_generation"] == 1
    assert await service.execute(cid, new_cast, principal_id="a") == result
    assert await play.store.read(cid) == after
    assert await play.store.replay(cid) == after
