"""Single-use invitation receipts; grants cannot assign actors or promote roles."""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime

from wayfarer.models import Campaign, Event
from wayfarer.simulation.access import CampaignMember

from .common import Fault, Obj, encoded, obj, uid
from .service import V1Service


async def invitation(
    service: V1Service, principal: str, cid: str, path: str, data: Obj, *, redeem: bool
) -> Obj:
    key = "receipt:" + encoded([principal, data["command_id"]])
    fingerprint = encoded(["POST", path, data])
    token_key = "invite:" + hashlib.sha256(str(data.get("token", "")).encode()).hexdigest()
    async with service.ledger.transaction() as tx:
        old = await tx.get(key)
        if old and old["fingerprint"] != fingerprint:
            raise Fault(409, "idempotency_conflict")
        if not redeem:
            view = await service.view(tx, cid, principal)
            if view.member.role != "gm":
                raise Fault(403, "forbidden")
            if old:
                return obj(old["value"])
            if data["expected_membership_version"] != obj(view.campaign["membership"])["version"]:
                raise Fault(409, "stale_version")
            token = secrets.token_urlsafe(40)
            expires = time.time() + int(str(data["expires_in_seconds"]))
            value: Obj = {
                "id": uid(),
                "campaign_id": cid,
                "token": token,
                "role": data["role"],
                "expires_at": datetime.fromtimestamp(expires, UTC)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            await tx.put(
                "invite:" + hashlib.sha256(token.encode()).hexdigest(),
                {"value": value, "expires": expires, "claim": None},
            )
            await tx.put(key, {"fingerprint": fingerprint, "value": value})
            return value
        invite = await tx.get(token_key)
        if not invite:
            raise Fault(404, "not_found")
        value = obj(invite["value"])
        cid = str(value["campaign_id"])
        if old and old.get("complete"):
            return obj((await service.view(tx, cid, principal)).campaign["membership"])
        if (invite["claim"] is not None and invite["claim"] != key) or (
            old is None and float(str(invite["expires"])) < time.time()
        ):
            raise Fault(404, "not_found")
        invite["claim"] = key
        await tx.put(token_key, invite)
        await tx.put(key, {"fingerprint": fingerprint, "complete": False})
    # The token claim is committed before domain work, so a process failure cannot
    # allow another principal to redeem an already-applied grant.
    async with service.ledger.transaction() as tx:
        raw = await service.play.store.read(cid)
        payload = encoded(
            {"operation": "v1-invitation", "invitation": value["id"], "principal": principal}
        )
        internal_id = service.projector.token(principal, cid, "invitation", data["command_id"])

        def grant(campaign: Campaign) -> Event:
            state = service.play.for_campaign(campaign)._load(campaign)
            if not any(m.principal_id == principal for m in state.members):
                member = CampaignMember.model_validate(
                    {"principal_id": principal, "role": value["role"]}
                )
                state = state.model_copy(update={"members": state.members + (member,)})
            state = state.model_copy(
                update={
                    "revision": state.revision + 1,
                    "resources": state.resources.model_copy(
                        update={"revision": state.revision + 1}
                    ),
                }
            )
            campaign["play_json"] = state.model_dump_json()
            campaign["revision"] = state.revision
            return Event(
                input=payload, action="v1-membership", outcome="Membership granted", roll=None
            )

        await service.play.store.commit_turn(
            cid, internal_id, raw["revision"], payload, grant, actor_id=principal
        )
        membership = obj((await service.view(tx, cid, principal)).campaign["membership"])
        await tx.put(key, {"fingerprint": fingerprint, "complete": True})
        return membership
