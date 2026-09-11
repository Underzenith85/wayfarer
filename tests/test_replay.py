"""Real receipts prove fold checks and seeded replay are independent of providers."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from test_wave9 import prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.errors import ValidationError
from wayfarer.persistence.replay import verify_commands
from wayfarer.simulation.actions import Inspect


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_durable_replay_reports_legacy_and_detects_tampering(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path / "original", backend=backend)
    play.rng = secrets
    initial = await play.store.read(cid)
    configuration = play._load(initial).configuration_digest
    await play.execute(
        cid,
        Inspect(id="replay", actor_id="a", expected_revision=0, target_id="chest"),
        authenticated_actor_id="a",
    )
    records = await play.store.history(cid)
    assert len(records) == 1 and records[0].command_input is not None
    stream = await play.store.stream(cid)
    after, checks = await verify_commands(
        initial,
        records,
        stream,
        configuration_digest=configuration,
        execute=FixtureExecutor(play.engine, tmp_path / "execute"),
    )
    assert after == await play.store.read(cid)
    assert checks[0].folded and checks[0].reexecuted
    legacy = replace(records[0], entropy_seed=None)
    _, checks = await verify_commands(
        initial,
        [legacy],
        stream,
        configuration_digest=configuration,
        execute=FixtureExecutor(play.engine, tmp_path / "legacy"),
    )
    assert (
        checks[0].folded and not checks[0].reexecuted and "no entropy seed" in str(checks[0].reason)
    )
    with pytest.raises(ValidationError, match="configuration"):
        await verify_commands(initial, records, stream, configuration_digest="different-rules")
    corrupt = dict(records[0].state_after)
    corrupt["hp"] = 999
    from wayfarer import validation

    with pytest.raises(ValidationError, match="Snapshot"):
        await verify_commands(
            initial,
            [replace(records[0], state_after=validation.campaign(corrupt))],
            stream,
            configuration_digest=configuration,
        )
    with pytest.raises(ValidationError, match="digest"):
        await verify_commands(
            initial,
            [replace(records[0], command_input="tampered")],
            stream,
            configuration_digest=configuration,
        )
    with pytest.raises(ValidationError, match="events"):
        await verify_commands(initial, records, [], configuration_digest=configuration)
