"""Current B173 admission requires real GM facts without rewriting old producers."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime
from test_default_knowledge_hosts import adopt
from test_live_skill_prerequisite_context import background_fixture, reexecute

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Inspect, Social
from wayfarer.engine.simulation.campaign.default_knowledge import adopted, observations
from wayfarer.errors import ValidationError
from wayfarer.orchestration import default_knowledge_generations as generations
from wayfarer.orchestration.adjudication import AdjudicationService, RequestRuling
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.persistence.command_inputs import (
    DEFAULT_KNOWLEDGE_KEY,
    default_knowledge_version,
    same_input,
    stamp,
    stamp_default_knowledge,
)
from wayfarer.persistence.events import CommandInput, payload_digest


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [0, 1, 2])
@pytest.mark.parametrize("trained", [False, True])
@pytest.mark.parametrize("social", [False, True])
async def test_current_unknown_refuses_but_old_producers_and_training_survive(
    tmp_path: Path,
    backend: str,
    version: int,
    trained: bool,
    social: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", version > 0)
    monkeypatch.setattr(generations, "CURRENT_VERSION", version)
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=trained, authored_modifier=0
    )
    cid = initial["id"]
    before = await play.store.read(cid)
    original = play._load(before)
    assert not adopted(original, "a") and not observations(original.resources)
    command = (Social if social else Inspect)(
        id="source-check",
        actor_id="a",
        expected_revision=original.revision,
        target_id="b" if social else "chest",
    )
    if version == 2 and not trained:
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="GM-known default"):
            await play.execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before and play.rng.exhausted()
        assert not observations(play._load(await play.store.read(cid)).resources)
        view = await build_runtime(play).read(cid, principal_id="a")
        characters = view["characters"]
        assert isinstance(characters, tuple)
        denied_skill = "skill:diplomacy" if social else "skill:observation"
        assert denied_skill not in {value["target"] for value in characters[0]["values"]}
        play.rng = secrets
        await adopt(play, cid, (denied_skill,))
        command = command.model_copy(
            update={"expected_revision": (await play.store.read(cid))["revision"]}
        )
    play.rng = secrets
    play.seeds = lambda: f"{11:064x}"
    result = await play.execute(cid, command, principal_id="a")
    assert result.check
    assert result.check.base_target == ((10 if social else 9) if trained else (4 if social else 5))
    assert result.check.total == 5
    final = await play.store.read(cid)
    state = play._load(final)
    assert (
        state.actors == original.actors
        and state.configuration_digest == original.configuration_digest
    )
    record = (await play.store.history(cid))[-1]
    assert (
        default_knowledge_version(CommandInput(record.payload_hash, record.command_input))
        == version
    )
    # Change the current implementation after commit: exact retries and seeded
    # reexecution must use their actual recorded 0/1/2 origin, never fresh facts.
    monkeypatch.setattr(generations, "ACTIVE", True)
    monkeypatch.setattr(generations, "CURRENT_VERSION", 2)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await restarted.execute(cid, command, principal_id="a") == result
    assert await play.store.read(cid) == final
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("version", [1, 2])
def test_versions_keep_original_bytes_and_reject_unrecorded_future_semantics(version: int) -> None:
    original = '{ "principal_id":"director:alice", "actor_id":"mage:a" }'
    old = stamp(original, version=3)
    text = stamp_default_knowledge(old, version=version)
    record = CommandInput(payload_digest({"input": text}), text)
    assert default_knowledge_version(record) == version and same_input(record, original)
    assert json.loads(text)[DEFAULT_KNOWLEDGE_KEY] == version
    for malformed in (True, 1.0, 0, 3, "2", None):
        with pytest.raises(ValidationError, match="Unsupported recorded default knowledge"):
            stamp_default_knowledge(old, version=malformed)  # type: ignore[arg-type]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("family", ["queue", "ruling"])
async def test_unadopted_current_deferred_default_refuses_before_pending_or_clock_cost(
    tmp_path: Path, backend: str, family: str
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=False, deferred=True, authored_modifier=0
    )
    cid = initial["id"]
    party = PartyService(play)
    if family == "queue":
        await party.execute(
            cid,
            PartyCommand(
                id="split",
                kind="split_party",
                actor_id="a",
                expected_revision=(await play.store.read(cid))["revision"],
                target_id="scouts",
            ),
            principal_id="a",
        )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="GM-known default"):
        if family == "queue":
            await party.execute(
                cid,
                PartyCommand(
                    id="unknown-queued",
                    kind="queue_activity",
                    actor_id="a",
                    expected_revision=before["revision"],
                    activity_json=Inspect(
                        id="observe",
                        actor_id="a",
                        expected_revision=before["revision"],
                        target_id="chest",
                    ).model_dump_json(),
                ),
                principal_id="a",
            )
        else:
            await AdjudicationService(play).submit(
                cid,
                RequestRuling(
                    id="unknown-request",
                    actor_id="a",
                    expected_revision=before["revision"],
                    action=Social(
                        id="social",
                        actor_id="a",
                        expected_revision=before["revision"],
                        target_id="b",
                        approach="intimidation",
                    ),
                ),
                principal_id="a",
            )
    assert await play.store.read(cid) == before and play.rng.exhausted()
    assert not observations(play._load(before).resources)
