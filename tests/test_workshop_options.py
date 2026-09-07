"""Profile preview, typed metadata, authority and ledger integration contracts."""

import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from test_wave9 import prepare

from scripts.workshop_contracts import contract
from wayfarer.character.compiler import CharacterDraft, Purchase
from wayfarer.character.power import CharacterProposal
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.workshop import DraftCommand, WorkshopService
from wayfarer.orchestration.workshop_options import ProfilePreviewRequest, preview_profile
from wayfarer.transport.campaign_api import create_campaign_app


def test_profile_preview_compiles_service_totals_and_keeps_gates() -> None:
    draft = CharacterDraft(
        name="Scholar",
        purchases=tuple(
            Purchase(definition_id="attribute:" + key, amount=10)
            for key in ("st", "dx", "iq", "ht")
        )
        + (
            Purchase(definition_id="secondary:hp", amount=12),
            Purchase(definition_id="skill:karate", amount=4),
        ),
    )
    result = preview_profile(
        ProfilePreviewRequest(
            profile_id="profile:gurps-basic-set-4e-2004",
            version=3,
            proposal=CharacterProposal(draft=draft),
        )
    )
    assert result.legal and result.spent == 8 and result.remaining == 92
    assert not result.profile.supported and result.profile.blockers
    assert dict(result.derived)["secondary:hp"] == "12"
    assert any(d.skill and d.skill.technique for d in result.catalog)
    assert json.loads(Path("contracts/workshop/v1/openapi.json").read_text()) == json.loads(
        contract()
    )


async def test_http_workshop_metadata_and_profile_preview(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    app = create_campaign_app(
        CampaignAccess(play), {"alice-token": "alice", "bob-token": "bob"}, legacy_routes=True
    )
    async with TestClient(TestServer(app)) as client:
        headers = {"Authorization": "Bearer alice-token"}
        response = await client.get(f"/campaigns/{cid}/workshop/a", headers=headers)
        assert response.status == 200
        data = await response.json()
        assert data["options"]["build_revision"] and data["options"]["catalog"]
        assert data["options"]["points_available"] == 0
        result = await client.post(
            f"/campaigns/{cid}/workshop-profile-preview",
            headers=headers,
            json={
                "profile_id": "profile:gurps-lite-4e-2004",
                "version": 3,
                "proposal": data["proposal"],
            },
        )
        assert result.status == 200
        assert (await result.json())["profile"]["supported"] is False
        denied = await client.get(
            f"/campaigns/{cid}/workshop/a", headers={"Authorization": "Bearer bob-token"}
        )
        assert denied.status == 403
        # A browser may not change the build or invent earned points.
        bad = await client.post(
            f"/campaigns/{cid}/workshop-advancement/apply",
            headers=headers,
            json={
                "id": "stale",
                "actor_id": "a",
                "expected_revision": 0,
                "expected_build_revision": "wrong",
                "draft": data["proposal"]["draft"],
                "reason": "Training",
            },
        )
        assert bad.status == 409


async def test_setup_reactivation_does_not_heal(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    proposal = state.actors[0].proposal

    def injure(campaign: Campaign) -> Event:
        current = play._load(campaign)
        resources = current.resources.model_copy(
            update={
                "pools": tuple(
                    p.model_copy(update={"current": 3}) if p.id == "hp:a" else p
                    for p in current.resources.pools
                ),
                "revision": 1,
            }
        )
        campaign["revision"] = 1
        campaign["play_json"] = current.model_copy(
            update={"revision": 1, "resources": resources}
        ).model_dump_json()
        return Event(input="injure", action="resource", outcome="hurt", roll=None)

    await play.store.commit_turn(cid, "injury", 0, "injure", injure, actor_id="a")
    workshop = WorkshopService(CampaignAccess(play))
    await workshop.execute(
        cid,
        DraftCommand(
            id="save",
            draft_id="hero",
            actor_id="a",
            expected_revision=1,
            expected_draft_revision=0,
            content_json=proposal.model_dump_json(),
        ),
        principal_id="alice",
    )
    await workshop.execute(
        cid,
        DraftCommand(
            id="activate",
            draft_id="hero",
            actor_id="a",
            expected_revision=2,
            expected_draft_revision=1,
            operation="activate",
        ),
        principal_id="alice",
    )
    updated = play._load(await play.store.read(cid))
    assert next(p for p in updated.resources.pools if p.id == "hp:a").current == 3
