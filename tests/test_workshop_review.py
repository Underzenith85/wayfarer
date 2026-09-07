"""Submitted revisions are private, GM-reviewable and receipt/CAS protected."""

import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from test_wave9 import prepare

from wayfarer.orchestration.access import CampaignAccess
from wayfarer.transport.campaign_api import create_campaign_app


async def test_review_submission_authority_stale_approval_and_retry(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    app = create_campaign_app(
        CampaignAccess(play), {"alice": "alice", "bob": "bob", "gm": "gm"}, legacy_routes=True
    )
    async with TestClient(TestServer(app)) as client:
        alice = {"Authorization": "Bearer alice"}
        gm = {"Authorization": "Bearer gm"}
        bob = {"Authorization": "Bearer bob"}
        prefix = f"/campaigns/{cid}"
        original = await (await client.get(prefix + "/workshop/a", headers=alice)).json()
        command = {
            "id": "save",
            "draft_id": "hero",
            "actor_id": "a",
            "expected_revision": 0,
            "expected_draft_revision": 0,
            "content_json": json.dumps(original["proposal"]),
        }
        assert (await client.post(prefix + "/drafts", headers=alice, json=command)).status == 200
        assert (await client.get(prefix + "/workshop/a", headers=gm)).status == 403
        assert (await client.get(prefix + "/drafts/hero", headers=gm)).status == 403
        queue = await (await client.get(prefix + "/workshop-reviews", headers=gm)).json()
        assert queue["submissions"] == []
        assert (await client.get(prefix + "/workshop-reviews", headers=bob)).status == 403
        command.update(
            id="submit", operation="submit", expected_revision=1, expected_draft_revision=1
        )
        assert (await client.post(prefix + "/drafts", headers=alice, json=command)).status == 200
        assert (await client.post(prefix + "/drafts", headers=alice, json=command)).status == 200
        review = await (await client.get(prefix + "/workshop/a?draft_id=hero", headers=gm)).json()
        assert review["options"]["can_approve"] and not review["options"]["can_edit"]
        assert review["draft"]["submitted_revision"] == 2
        assert (await client.get(prefix + "/workshop/missing", headers=gm)).status == 403
        approval = dict(
            command,
            id="approve",
            operation="approve",
            expected_revision=2,
            expected_draft_revision=2,
            reason="Reviewed concept",
        )
        assert (await client.post(prefix + "/drafts", headers=alice, json=approval)).status == 403
        assert (
            await client.post(prefix + "/drafts", headers=gm, json=dict(approval, operation="save"))
        ).status == 403
        assert (
            await client.post(
                prefix + "/drafts", headers=alice, json=dict(approval, operation="activate")
            )
        ).status == 400
        # A player edit invalidates the submission and the GM's displayed CAS token.
        edited = dict(
            command, id="edit", operation="save", expected_revision=2, expected_draft_revision=2
        )
        assert (await client.post(prefix + "/drafts", headers=alice, json=edited)).status == 200
        assert (await client.post(prefix + "/drafts", headers=gm, json=approval)).status == 409
        assert (await client.get(prefix + "/workshop/a", headers=gm)).status == 403
        command.update(id="resubmit", expected_revision=3, expected_draft_revision=3)
        assert (await client.post(prefix + "/drafts", headers=alice, json=command)).status == 200
        approval.update(id="approve-current", expected_revision=4, expected_draft_revision=4)
        assert (await client.post(prefix + "/drafts", headers=gm, json=approval)).status == 200
        assert (await client.post(prefix + "/drafts", headers=gm, json=approval)).status == 200
        activated = dict(
            approval,
            id="activate",
            operation="activate",
            expected_revision=5,
            expected_draft_revision=5,
        )
        assert (await client.post(prefix + "/drafts", headers=alice, json=activated)).status == 200
        assert (await client.post(prefix + "/drafts", headers=alice, json=activated)).status == 200
        queue = await (await client.get(prefix + "/workshop-reviews", headers=gm)).json()
        assert queue["submissions"] == []
        grant = {
            "id": "reward",
            "actor_id": "gm",
            "target_actor_id": "a",
            "expected_revision": 6,
            "points": 4,
            "reason": "Completed objective",
        }
        assert (
            await client.post(prefix + "/workshop-grants", headers=alice, json=grant)
        ).status == 403
        assert (
            await client.post(prefix + "/workshop-grants", headers=gm, json=grant)
        ).status == 200
        assert (
            await client.post(prefix + "/workshop-grants", headers=gm, json=grant)
        ).status == 200
        state = play._load(await play.store.read(cid))
        assert state.revision == 7 and sum(e.points for e in state.advancement) == 4
        assert next(m for m in state.members if m.principal_id == "gm").actor_ids == ()
        current = await (await client.get(prefix + "/workshop/a", headers=alice)).json()
        draft = current["proposal"]["draft"]
        next(p for p in draft["purchases"] if p["definition_id"] == "skill:observation")[
            "amount"
        ] = 8
        purchase = {
            "id": "advance",
            "actor_id": "a",
            "expected_revision": 7,
            "expected_build_revision": current["options"]["build_revision"],
            "draft": draft,
            "reason": "Observation training",
        }
        preview = await client.post(
            prefix + "/workshop-advancement/preview", headers=alice, json=purchase
        )
        assert preview.status == 200
        assert (await preview.json())["points_delta"] == 4
        assert (
            await client.post(prefix + "/workshop-advancement/apply", headers=gm, json=purchase)
        ).status == 403
        assert (
            await client.post(prefix + "/workshop-advancement/apply", headers=alice, json=purchase)
        ).status == 200
        assert (
            await client.post(prefix + "/workshop-advancement/apply", headers=alice, json=purchase)
        ).status == 200
        assert (
            await client.post(
                prefix + "/workshop-advancement/preview", headers=alice, json=purchase
            )
        ).status == 409
        final = play._load(await play.store.read(cid))
        assert final.revision == 8 and sum(e.points for e in final.advancement) == 0
        assert [(p.id, p.current) for p in final.resources.pools] == [
            (p.id, p.current) for p in state.resources.pools
        ]
