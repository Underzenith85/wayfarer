"""Single-use invitation receipts; grants cannot assign actors or promote roles."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.orchestration.clock import CommandInstant, capture_instant
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import record_play_state

from .common import Fault, Obj, encoded, obj, uid
from .service import V1Service


def claim_is_valid(invite: Obj, key: str, instant: CommandInstant, *, retry: bool) -> bool:
    """A saved claim survives expiry; a different principal never inherits it."""
    return (invite["claim"] is None or invite["claim"] == key) and (
        retry or float(str(invite["expires"])) >= instant.seconds
    )


async def invitation(
    service: V1Service, principal: str, cid: str, path: str, data: Obj, *, redeem: bool
) -> Obj:
    instant = capture_instant()
    key = "receipt:" + encoded([principal, data["command_id"]])
    fingerprint = encoded(["POST", path, data])
    token_key = "invite:" + hashlib.sha256(str(data.get("token", "")).encode()).hexdigest()
    async with service.ledger.transaction(instant=instant) as tx:
        old = await tx.get(key)
        if old and old["fingerprint"] != fingerprint:
            raise Fault(409, "idempotency_conflict")
        if old and old.get("recorded_at_us") is not None:
            instant = CommandInstant(int(str(old["recorded_at_us"])))
        if not redeem:
            view = await service.view(tx, cid, principal)
            if view.member.role != "gm":
                raise Fault(403, "forbidden")
            if old:
                return obj(old["value"])
            if data["expected_membership_version"] != obj(view.campaign["membership"])["version"]:
                raise Fault(409, "stale_version")
            token = secrets.token_urlsafe(40)
            expires = instant.seconds + int(str(data["expires_in_seconds"]))
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
            await tx.put(
                key,
                {
                    "fingerprint": fingerprint,
                    "value": value,
                    "recorded_at_us": instant.unix_microseconds,
                },
            )
            return value
        invite = await tx.get(token_key)
        if not invite:
            raise Fault(404, "not_found")
        value = obj(invite["value"])
        cid = str(value["campaign_id"])
        if old and old.get("complete"):
            return obj((await service.view(tx, cid, principal)).campaign["membership"])
        if not claim_is_valid(invite, key, instant, retry=old is not None):
            raise Fault(404, "not_found")
        invite["claim"] = key
        await tx.put(token_key, invite)
        await tx.put(
            key,
            {
                "fingerprint": fingerprint,
                "complete": False,
                "recorded_at_us": instant.unix_microseconds,
            },
        )
    # The token claim is committed before domain work, so a process failure cannot
    # allow another principal to redeem an already-applied grant.
    async with service.ledger.transaction(instant=instant) as tx:
        raw = await service.play.store.read(cid)
        payload = encoded(
            {"operation": "v1-invitation", "invitation": value["id"], "principal": principal}
        )
        internal_id = service.projector.token(principal, cid, "invitation", data["command_id"])

        def grant(campaign: Campaign) -> CommandReceipt:
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
            record_play_state(campaign, state)
            return CommandReceipt(action="v1-membership", outcome="Membership granted")

        await commit_command(
            service.play.store,
            cid,
            internal_id,
            raw["revision"],
            payload,
            grant,
            actor_id=principal,
            rng=service.play.rng,
            instant=instant,
        )
        membership = obj((await service.view(tx, cid, principal)).campaign["membership"])
        await tx.put(
            key,
            {
                "fingerprint": fingerprint,
                "complete": True,
                "recorded_at_us": instant.unix_microseconds,
            },
        )
        return membership
