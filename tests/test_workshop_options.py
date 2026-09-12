"""Profile preview, typed metadata, authority and ledger integration contracts."""

import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from test_wave9 import prepare

from scripts.workshop_contracts import contract
from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import CharacterDraft, Purchase
from wayfarer.engine.character.power import CharacterProposal
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

    def injure(campaign: Campaign) -> CommandReceipt:
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
        return CommandReceipt(action="resource", outcome="hurt")

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


async def test_active_preview_is_read_only_authorized_and_rejects_client_costs(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path)
    app = create_campaign_app(
        CampaignAccess(play), {"alice-token": "alice", "bob-token": "bob"}, legacy_routes=True
    )
    proposal = CharacterProposal(
        draft=CharacterDraft(
            name="Preview hero",
            purchases=tuple(
                Purchase(definition_id="attribute:" + key, amount=10)
                for key in ("st", "dx", "iq", "ht")
            )
            + (Purchase(definition_id="skill:observation", amount=4),),
        )
    )
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    async with TestClient(TestServer(app)) as client:
        path = f"/campaigns/{cid}/workshop/a/preview"
        headers = {"Authorization": "Bearer alice-token"}
        body = {"proposal": proposal.model_dump(mode="json")}
        response = await client.post(path, headers=headers, json=body)
        assert response.status == 200
        result = await response.json()
        # Prototype policy: baseline attributes are free; four skill points cost four.
        assert result["spent"] == 4 and result["remaining"] == 96
        assert {p["definition_id"]: p["cost"] for p in result["breakdown"]} == {
            "attribute:st": 0,
            "attribute:dx": 0,
            "attribute:iq": 0,
            "attribute:ht": 0,
            "skill:observation": 4,
        }
        assert result["legal"]
        overspent = proposal.model_copy(
            update={
                "draft": proposal.draft.model_copy(
                    update={
                        "purchases": proposal.draft.purchases[:-1]
                        + (Purchase(definition_id="skill:observation", amount=101),)
                    }
                )
            }
        )
        response = await client.post(
            path, headers=headers, json={"proposal": overspent.model_dump(mode="json")}
        )
        result = await response.json()
        assert result["spent"] == 101 and result["remaining"] == -1
        assert not result["legal"] and result["diagnostics"]
        assert result["derived"] == [] and result["breakdown"] == []
        assert (await client.post(path, json=body)).status == 401
        assert (
            await client.post(path, headers={"Authorization": "Bearer bob-token"}, json=body)
        ).status == 403
        assert (await client.post(path, headers=headers, json={**body, "spent": 0})).status == 400
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history


async def test_setup_preview_uses_exact_profile_and_host_authority(tmp_path: Path) -> None:
    from test_profiles import ALICE, EXTENDED, TOKENS, extended_graph, runtime

    from wayfarer.engine.simulation.campaign.setup import CreateSetup, SetupCommand
    from wayfarer.orchestration.setup import SetupService

    profiles = runtime(tmp_path)
    setup = SetupService(CampaignAccess(profiles.play))
    graph = extended_graph(trait=True)
    created = await setup.create(
        CreateSetup(id="preview", brief=graph.brief, graph=graph, rules_profile=EXTENDED),
        principal_id="alice",
    )
    cid = str(created["id"])
    await setup.execute(
        cid,
        SetupCommand(id="invite", expected_revision=0, operation="invite", principal_id="bob"),
        principal_id="alice",
    )
    before = await profiles.store.read(cid)
    app = create_campaign_app(CampaignAccess(profiles.play), TOKENS)
    async with TestClient(TestServer(app)) as client:
        path = f"/setups/{cid}/character-preview"
        body = {"proposal": graph.actors[0].proposal.model_dump(mode="json")}
        result = await client.post(path, headers=ALICE, json=body)
        assert result.status == 200
        preview = await result.json()
        assert any(
            p["definition_id"] == "trait:lantern-bearer" and p["cost"] == 5
            for p in preview["breakdown"]
        )
        assert (
            await client.post(path, headers={"Authorization": "Bearer bob-token"}, json=body)
        ).status == 403
        assert (await client.post(path, json=body)).status == 401
    assert await profiles.store.read(cid) == before
