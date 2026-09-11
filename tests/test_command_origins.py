"""Private proposal provenance survives commit, retries and director recovery."""

import json
from pathlib import Path

import pytest
from test_wave9 import FakeProvider, prepare

from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.director import DirectorService
from wayfarer.orchestration.providers import Orchestrator
from wayfarer.persistence.events import CommandOrigin, payload_digest
from wayfarer.simulation.actions import Wait


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_origin_is_private_and_does_not_change_receipts(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend=backend)
    access = CampaignAccess(play)
    proposal = {"kind": "wait", "ticks": 1}
    origin = CommandOrigin.proposal("Intent", proposal, provider="fake", model="test-model")
    command = Wait(id="origin", actor_id="a", expected_revision=0, ticks=1)
    await access.execute(cid, command.model_dump(mode="json"), principal_id="alice", origin=origin)
    row = (await play.store.history(cid))[0]
    assert row.origin == origin
    assert origin.digest == payload_digest(proposal)
    payload = json.dumps(
        {"operation": "typed-action", "command": command.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert row.payload_hash == payload_digest({"input": payload})
    await access.execute(cid, command.model_dump(mode="json"), principal_id="alice")
    assert (await play.store.history(cid))[0] == row
    assert "test-model" not in json.dumps(await access.read(cid, principal_id="alice"))
    assert "test-model" not in json.dumps(row.state_after)
    assert "test-model" not in repr(row)
    await access.execute(
        cid,
        Wait(id="direct", actor_id="a", expected_revision=1, ticks=1).model_dump(mode="json"),
        principal_id="alice",
    )
    assert (await play.store.history(cid))[-1].origin is None


async def test_provider_origin_and_replay_never_call_provider(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    provider = FakeProvider()
    await Orchestrator(CampaignAccess(play), provider).interpret_and_execute(
        cid, principal_id="alice", actor_id="a", command_id="wait", text="Wait"
    )
    row = (await play.store.history(cid))[0]
    assert row.origin is not None and row.origin.proposal_type == "Intent"
    assert json.loads(row.origin.proposal_json)["ticks"] == 1
    calls = len(provider.requests)
    assert await play.store.replay(cid) == await play.store.read(cid)
    assert len(provider.requests) == calls


async def test_director_origin_survives_restart_before_resolution(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    provider = FakeProvider()

    def crash(phase: str) -> None:
        if phase == "resolution":
            raise RuntimeError("restart")

    with pytest.raises(RuntimeError, match="restart"):
        await DirectorService(Orchestrator(CampaignAccess(play), provider)).run(
            cid,
            principal_id="alice",
            actor_id="a",
            command_id="turn",
            text="wait",
            checkpoint=crash,
        )
    saved = (await play.store.history(cid))[-1].origin
    assert saved is not None
    await DirectorService(Orchestrator(CampaignAccess(play), provider)).run(
        cid, principal_id="alice", actor_id="a", command_id="turn", text="wait"
    )
    domain = next(e for e in await play.store.history(cid) if e.event["action"] == "typed-action")
    assert domain.origin == saved
    assert sum(r.operation == "intent" for r in provider.requests) == 1


def test_origin_rejects_tampering() -> None:
    origin = CommandOrigin.proposal("Intent", {"kind": "wait", "ticks": 1}, provider="fake")
    with pytest.raises(ValueError, match="digest"):
        CommandOrigin.model_validate({**origin.model_dump(), "proposal_json": '{"kind":"inspect"}'})


async def test_npc_proposal_uses_same_origin_and_scope_resets_on_failure(tmp_path: Path) -> None:
    from test_wave10 import prepare as npc_prepare

    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.npcs import NPCProposal, NPCService
    from wayfarer.orchestration.origins import current_origin

    cid, play = await npc_prepare(tmp_path)
    command = NPCProposal(
        id="npc-origin", actor_id="gm", expected_revision=0, plan_id="patrol", action_id="unknown"
    )
    origin = CommandOrigin.proposal("NPCProposal", command.model_dump(mode="json"), provider="fake")
    await NPCService(play).propose(cid, command, authenticated_gm_id="gm", origin=origin)
    assert (await play.store.history(cid))[-1].origin == origin
    with pytest.raises(ValidationError):
        await CampaignAccess(play).execute(cid, {}, principal_id="alice", origin=origin)
    assert current_origin.get() is None
