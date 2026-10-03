"""Private missile-roll generations preserve legacy bytes, replay and scope."""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from support.runtime import build_play, seed_campaign
from test_spell_bindings import command as spell_command

from scripts.replay_fixtures import FIXTURES, ReplayFixture, engine_for
from wayfarer import contracts, validation
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.generations import (
    combat_generation,
    missile_interposition_enabled,
    preserve_grenade_fuse,
)
from wayfarer.engine.simulation.magic.spell_transitions import SpellExecutionContext
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellResult
from wayfarer.errors import ValidationError
from wayfarer.orchestration import spells
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.spell_generations import SpellGenerations, recorded_generations
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.events import fold, payload_digest
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.persistence.replay import command_text


@pytest.fixture
async def pg_schemas() -> AsyncIterator[list[str]]:
    created: list[str] = []
    yield created
    if created:
        async with await psycopg.AsyncConnection.connect(
            os.environ["WAYFARER_TEST_DATABASE_URL"], autocommit=True
        ) as db:
            for schema in created:
                await db.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))


async def fixture_play(
    path: Path, engine: ActionEngine, backend: str, schemas: list[str]
) -> PlayService:
    if backend != "postgres":
        return build_play(path, engine, backend=backend)
    url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
    schema = "spell_missile_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as db:
        await db.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    schemas.append(schema)
    return build_play(
        path,
        engine,
        store=AsyncPostgresStore(make_conninfo(url, options="-csearch_path=" + schema), 10),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_legacy_spell_reexecution_and_live_retry_retain_absent_missile_generation(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch, pg_schemas: list[str]
) -> None:
    fixture = ReplayFixture.model_validate_json((FIXTURES / "spell.json").read_text())
    engine = await engine_for("spell", tmp_path / "engine")
    initial = contracts.campaign(validation.decode(fixture.initial_json))
    play = await fixture_play(tmp_path, engine, backend, pg_schemas)
    await seed_campaign(play.store, initial)
    observed: list[bool] = []
    original = spells.reduce_spell

    def observing(
        before: PlayState, value: RuntimeSpellCommand, context: SpellExecutionContext
    ) -> tuple[PlayState, SpellResult]:
        observed.append(missile_interposition_enabled())
        return original(before, value, context)

    monkeypatch.setattr(spells, "reduce_spell", observing)
    before = initial
    for entry in fixture.commands:
        expected = fold(before, list(entry.events))
        recorded = entry.record(before, expected)
        await execute_recorded(play, recorded)
        before = await play.store.read(initial["id"])
        assert before == expected
        copied = (await play.store.history(initial["id"]))[-1]
        assert copied.payload_hash == recorded.payload_hash
        assert command_text(copied) == command_text(recorded)
        assert (
            tuple(
                e.event
                for e in await play.store.stream(initial["id"])
                if e.command_id == recorded.command_id
            )
            == entry.events
        )
        assert copied.entropy_seed == recorded.entropy_seed
    assert observed and not any(observed)
    assert not missile_interposition_enabled()
    cid = initial["id"]
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    first = next(r for r in history if r.command_id == "spell-0")
    assert "missile_attack_generation" not in command_text(first)
    await SpellService(play).execute(cid, spell_command(0), principal_id="a")
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    await SpellService(play).execute(
        cid,
        spell_command(before["revision"]).model_copy(
            update={"id": "finish-legacy", "kind": "complete"}
        ),
        principal_id="a",
    )
    assert observed[-1] is True and not missile_interposition_enabled()
    saved = await play.store.read(cid)
    record = (await play.store.history(cid))[-1]
    assert json.loads(command_text(record))["missile_attack_generation"] == 1
    clone = await fixture_play(tmp_path / "clone", engine, backend, pg_schemas)
    await seed_campaign(clone.store, before)
    await execute_recorded(clone, record)
    assert await clone.store.read(cid) == saved
    cloned_record = (await clone.store.history(cid))[-1]
    assert cloned_record.payload_hash == record.payload_hash
    assert cloned_record.event == record.event
    assert cloned_record.entropy_seed == record.entropy_seed
    after = await play.store.read(cid)
    await SpellService(play).execute(
        cid,
        spell_command(before["revision"]).model_copy(
            update={"id": "finish-legacy", "kind": "complete"}
        ),
        principal_id="a",
    )
    assert await play.store.read(cid) == after


@pytest.mark.parametrize("generation", [None, False, True, 0, 2, 1.0, "1"])
async def test_invalid_recorded_missile_generation_refuses_authenticated_envelope(
    tmp_path: Path, generation: object
) -> None:
    fixture = ReplayFixture.model_validate_json((FIXTURES / "spell.json").read_text())
    engine = await engine_for("spell", tmp_path / "engine")
    initial = contracts.campaign(validation.decode(fixture.initial_json))
    play = build_play(tmp_path, engine)
    record = fixture.commands[0].record(initial, fold(initial, list(fixture.commands[0].events)))
    source = json.loads(command_text(record))
    source["missile_attack_generation"] = generation
    text = json.dumps(source)
    forged = replace(record, command_input=text, payload_hash=payload_digest({"input": text}))
    with replay_inputs(forged), pytest.raises(ValidationError, match="missile attack generation"):
        await recorded_generations(play, play._load(initial), spell_command(0))
    assert not missile_interposition_enabled()


async def test_absent_unrecoverable_wrong_family_and_tampered_missile_records(
    tmp_path: Path,
) -> None:
    fixture = ReplayFixture.model_validate_json((FIXTURES / "spell.json").read_text())
    engine = await engine_for("spell", tmp_path / "engine")
    initial = contracts.campaign(validation.decode(fixture.initial_json))
    play = build_play(tmp_path, engine)
    state = play._load(initial)
    assert (await recorded_generations(play, state, spell_command(0))).missile_attack
    record = fixture.commands[0].record(initial, fold(initial, list(fixture.commands[0].events)))
    with replay_inputs(record):
        assert not (await recorded_generations(play, state, spell_command(0))).missile_attack
    with replay_inputs(replace(record, command_input=None)):
        assert await recorded_generations(play, state, spell_command(0)) == SpellGenerations(
            False, False, False, False, False
        )
    payload = json.loads(command_text(record))
    payload.update(operation="combat", missile_attack_generation=1)
    text = json.dumps(payload)
    wrong = replace(record, command_input=text, payload_hash=payload_digest({"input": text}))
    with replay_inputs(wrong), pytest.raises(ValidationError, match="missile attack generation"):
        await recorded_generations(play, state, spell_command(0))
    with (
        replay_inputs(replace(record, command_input=text)),
        pytest.raises(ValidationError, match="digest"),
    ):
        await recorded_generations(play, state, spell_command(0))


@pytest.mark.parametrize("enabled", [False, True])
async def test_spell_reducer_generation_scope_resets_on_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    fixture = ReplayFixture.model_validate_json((FIXTURES / "spell.json").read_text())
    engine = await engine_for("spell", tmp_path / "engine")
    initial = contracts.campaign(validation.decode(fixture.initial_json))
    play = build_play(tmp_path, engine)
    state = play._load(initial)
    member = next(m for m in state.members if m.principal_id == "a")

    def reject(
        before: PlayState, value: RuntimeSpellCommand, context: SpellExecutionContext
    ) -> tuple[PlayState, SpellResult]:
        assert missile_interposition_enabled() is enabled and not preserve_grenade_fuse()
        raise ValidationError("injected reducer rollback")

    monkeypatch.setattr(spells, "reduce_spell", reject)
    plan = SpellService(play).plan(
        play, member, spell_command(0), principal_id="a", state=state, missile_attack=enabled
    )
    assert ("missile_attack_generation" in json.loads(plan.payload)) is enabled
    with combat_generation(frozenset({"grenade-fuse"})):
        with pytest.raises(ValidationError, match="rollback"):
            plan.resolve(initial)
        assert preserve_grenade_fuse() and not missile_interposition_enabled()
    assert not preserve_grenade_fuse() and not missile_interposition_enabled()
