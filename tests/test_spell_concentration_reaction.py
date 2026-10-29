"""B366/B421 reactive preservation saves; historical spell generations stay exact."""

import secrets
from pathlib import Path

import pytest
from support.runtime import played
from test_spell_bindings import command
from test_symptom_casting_checks import change_symptoms, finish, prepare, symptoms

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.events import document
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import command_text, verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("current", [False, True])
async def test_reactive_save_generation_preserves_old_reexecution_and_retries(
    tmp_path: Path, backend: str, current: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, active=False, tipsy=True)
    start = command()
    await SpellService(play).execute(cid, start, principal_id="a")
    await change_symptoms(play, cid, symptoms, "health-boundary")
    complete, _ = await finish(play, cid, start)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    for principal, expected_revision, error in (
        ("b", complete.expected_revision, AuthorizationError),
        ("a", 0, ConflictError),
    ):
        with pytest.raises(error):
            await SpellService(play).execute(
                cid,
                complete.model_copy(update={"expected_revision": expected_revision}),
                principal_id=principal,
            )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = secrets
    play.seeds = lambda: format(1, "064x")
    state = play._load(before)
    result = await submit(
        play,
        cid,
        SpellService(play).plan(
            play,
            member_for(state, "a"),
            complete,
            principal_id="a",
            concentration_reaction=current,
        ),
        principal_id="a",
    )
    assert result.checks[0].effective_target == (9 if current else 8)
    assert [modifier.value for modifier in result.checks[0].modifiers] == ([] if current else [-1])
    if current:
        assert result.outcome == "active" and result.checks[-1].effective_target == 11
        assert result.energy_spent == 1
    else:
        assert result.outcome == "interrupted" and len(result.checks) == 1
        assert result.energy_spent == 0
    records = await played(play.store, cid)
    record = next(r for r in records if r.command_id == complete.id)
    payload = validation.mapping(validation.decode(command_text(record)))
    assert payload["check_generation"] == 1
    assert payload.get("concentration_reaction_generation") == (1 if current else None)
    final = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    assert await SpellService(play).execute(cid, complete, principal_id="a") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    # The external fixture health mutation is not an executable seed command.
    # Replay the actual spell command against its retained immediate before image.
    replayed, checks = await verify_commands(
        before,
        [record],
        [e for e in stream if e.command_id == complete.id],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert document(replayed) == document(final)
    assert checks and all(c.folded and c.reexecuted for c in checks)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_reactive_save_can_still_fail_without_casting_or_payment(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, active=False, tipsy=True)
    start = command()
    await SpellService(play).execute(cid, start, principal_id="a")
    await change_symptoms(play, cid, symptoms, "health-boundary")
    complete, _ = await finish(play, cid, start)
    play.rng = RecordedDice((3, 3, 4))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    assert len(result.checks) == 1
    assert result.checks[0].effective_target == 9 and result.checks[0].total == 10
    assert not result.checks[0].modifiers
    assert result.outcome == "interrupted" and result.energy_spent == 0


@pytest.mark.parametrize(
    "operation,generation",
    [("spell-lifecycle", value) for value in [None, False, True, 0, 2, 1.0, "1"]] + [("combat", 1)],
)
async def test_malformed_private_reaction_generation_rejected_before_mutation(
    tmp_path: Path, operation: str, generation: object
) -> None:
    import json
    from dataclasses import replace

    from pydantic import ValidationError as PydanticValidationError
    from support.runtime import build_play, seed_campaign

    from scripts.replay_fixtures import FIXTURES, ReplayFixture, engine_for
    from wayfarer import contracts
    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.replay import execute_recorded
    from wayfarer.orchestration.replay_inputs import replay_inputs
    from wayfarer.orchestration.spell_generations import recorded_generations
    from wayfarer.persistence.events import fold, payload_digest

    fixture = ReplayFixture.model_validate_json((FIXTURES / "spell.json").read_text())
    engine = await engine_for("spell", tmp_path / "engine")
    initial = contracts.campaign(validation.decode(fixture.initial_json))
    play = build_play(tmp_path, engine)
    await seed_campaign(play.store, initial)
    entry = fixture.commands[0]
    record = entry.record(initial, fold(initial, list(entry.events)))
    payload = json.loads(command_text(record))
    payload["operation"] = operation
    payload["concentration_reaction_generation"] = generation
    text = json.dumps(payload)
    malformed = replace(record, command_input=text, payload_hash=payload_digest({"input": text}))
    before = await play.store.read(initial["id"])
    history, stream = (
        await play.store.history(initial["id"]),
        await play.store.stream(initial["id"]),
    )
    if operation != "spell-lifecycle":
        with pytest.raises(PydanticValidationError, match="union_tag_invalid"):
            await execute_recorded(play, malformed)
        assert await play.store.read(initial["id"]) == before
        assert await play.store.history(initial["id"]) == history
        assert await play.store.stream(initial["id"]) == stream
    with pytest.raises(ValidationError, match="concentration reaction generation"):
        if operation == "spell-lifecycle":
            await execute_recorded(play, malformed)
        else:
            with replay_inputs(malformed):
                await recorded_generations(play, play._load(initial), command())
    assert await play.store.read(initial["id"]) == before
    assert await play.store.history(initial["id"]) == history
    assert await play.store.stream(initial["id"]) == stream
