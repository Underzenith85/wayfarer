"""Each registered authorizer refuses on its own terms, through the real runtime."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_wave10 import prepare

from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.errors import AuthorizationError
from wayfarer.orchestration.commands import (
    All,
    Any_,
    Submission,
    controls_actor,
    director_controls_npc,
    family_for,
    is_director,
    service_authorizes,
)

GM = CampaignMember(principal_id="gm", role="gm")


def wait(actor_id: str) -> dict[str, object]:
    return {"id": "w", "actor_id": actor_id, "expected_revision": 1, "kind": "wait", "ticks": 1}


async def submission(tmp_path: Path, principal_id: str, command: dict[str, object]) -> Submission:
    """One real campaign command, assembled exactly as the runtime assembles it."""
    cid, play = await prepare(tmp_path)
    campaign = await play.store.read(cid)
    state = play._load(campaign)
    family = family_for(command["kind"])
    return Submission(
        play=play,
        cid=cid,
        campaign=campaign,
        command=family.parse(json.dumps(command)),
        state=state,
        member=next(m for m in state.members if m.principal_id == principal_id),
        principal_id=principal_id,
    )


async def test_controls_actor_admits_the_controller_and_refuses_everyone_else(
    tmp_path: Path,
) -> None:
    controls_actor(await submission(tmp_path, "alice", wait("a")))
    with pytest.raises(AuthorizationError, match="control"):
        controls_actor(await submission(tmp_path, "alice", wait("b")))


async def test_is_director_refuses_a_player(tmp_path: Path) -> None:
    player = await submission(tmp_path, "alice", wait("a"))
    with pytest.raises(AuthorizationError, match="director"):
        is_director(player)
    is_director(replace(player, member=GM))


async def test_director_controls_npc_needs_authority_and_an_authored_npc(
    tmp_path: Path,
) -> None:
    player = await submission(tmp_path, "alice", wait("a"))
    with pytest.raises(AuthorizationError, match="director"):
        director_controls_npc(player)
    # This fixture pins no scenario graph, so no actor is an authored NPC.
    with pytest.raises(AuthorizationError, match="NPC"):
        director_controls_npc(replace(player, member=GM))


async def test_all_requires_every_rule_and_any_requires_one(tmp_path: Path) -> None:
    player = await submission(tmp_path, "alice", wait("a"))
    # The player controls the actor but is not the director.
    with pytest.raises(AuthorizationError, match="director"):
        All(is_director, controls_actor)(player)
    All(controls_actor)(player)
    Any_(controls_actor, is_director)(player)
    Any_(is_director, controls_actor)(player)
    with pytest.raises(AuthorizationError, match="director"):
        Any_(is_director)(player)


async def test_service_authorizes_defers_without_refusing(tmp_path: Path) -> None:
    # A spectator would be refused by every other rule; this family delegates.
    player = await submission(tmp_path, "alice", wait("a"))
    service_authorizes(replace(player, member=CampaignMember(principal_id="x", role="spectator")))
