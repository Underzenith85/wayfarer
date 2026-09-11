"""Revisioned, private author drafts and engine-validated character activation."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Literal

from pydantic import Field, TypeAdapter

from wayfarer.character.power import Approval, CharacterProposal
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Campaign, CommandReceipt, Id, Record
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.advancement import _refreshed
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.providers import Orchestrator, ProviderRequest
from wayfarer.rules.catalog import CampaignPolicy
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.director import AuthorDraft


class DraftCommand(Record):
    id: Id
    draft_id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    expected_draft_revision: int = Field(ge=0)
    operation: Literal["save", "submit", "approve", "activate"] = "save"
    kind: Literal["character", "scenario"] = "character"
    content_json: str | None = Field(default=None, max_length=100000)
    reason: str = Field(default="", max_length=2000)


class WorkshopService:
    def __init__(self, access: CampaignAccess) -> None:
        self.access, self.play = access, access.play

    def _get(self, state: PlayState, draft_id: str, principal_id: str) -> AuthorDraft:
        member = self.access._member(state, principal_id)
        draft = next((d for d in state.drafts if d.id == draft_id), None)
        if draft is None or (
            draft.owner_id != principal_id
            and (
                member.role != "gm"
                or principal_id not in self.play.engine.reviewer.gm_ids
                or draft.kind == "character"
                and draft.submitted_revision is None
            )
        ):
            raise AuthorizationError("Draft unavailable")
        return draft

    def _preview(self, draft: AuthorDraft) -> dict[str, object]:
        result: dict[str, object] = draft.model_dump(mode="json", exclude={"approval_json"})
        if draft.kind == "scenario":
            return result
        proposal = CharacterProposal.model_validate_json(draft.content_json)
        reviewer = self.play.engine.reviewer
        review = reviewer.review(proposal)
        compilation = review.compilation
        repair = reviewer.compiler.repair(proposal.draft)
        result.update(
            {
                "status": review.status,
                "approved": draft.approval_json is not None,
                "spent": compilation.spent,
                "remaining": compilation.remaining,
                "diagnostics": [asdict(d) for d in compilation.diagnostics],
                "findings": [f.model_dump(mode="json") for f in review.findings],
                "repair": repair.draft.model_dump(mode="json") if repair else None,
                "breakdown": [asdict(p) for p in compilation.build.purchases]
                if compilation.build
                else [],
                "derived": [
                    {"target": v.target, "value": str(v.value)}
                    for v in compilation.build.sheet.values
                ]
                if compilation.build
                else [],
                "patch": self._patch(draft),
                "statistics": json.loads(
                    TypeAdapter(type(compilation.build.statistics)).dump_json(
                        compilation.build.statistics
                    )
                )
                if compilation.build and compilation.build.statistics
                else None,
                "build_revision": compilation.build.revision if compilation.build else None,
            }
        )
        return result

    @staticmethod
    def _patch(draft: AuthorDraft) -> list[dict[str, object]]:
        old = json.loads(draft.previous_json) if draft.previous_json else {}
        new = json.loads(draft.content_json)
        return [
            {"field": k, "before": old.get(k), "after": new.get(k)}
            for k in sorted(old.keys() | new.keys())
            if old.get(k) != new.get(k)
        ]

    async def read(self, cid: str, draft_id: str, *, principal_id: str) -> dict[str, object]:
        return self._preview(
            self._get(self.play._load(await self.play.store.read(cid)), draft_id, principal_id)
        )

    async def execute(
        self, cid: str, command: DraftCommand, *, principal_id: str
    ) -> dict[str, object]:
        state = self.play._load(await self.play.store.read(cid))
        member = self.access._member(state, principal_id)
        if command.operation == "approve":
            if member.role != "gm" or principal_id not in self.play.engine.reviewer.gm_ids:
                raise AuthorizationError("Approval requires campaign GM")
        else:
            self.access._control(member, command.actor_id)
        payload = json.dumps(
            {"principal_id": principal_id, "command": command.model_dump(mode="json")},
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            current = self.play._load(campaign)
            old = next((d for d in current.drafts if d.id == command.draft_id), None)
            if old is not None:
                self._get(current, old.id, principal_id)
                if (old.kind, old.actor_id) != (command.kind, command.actor_id):
                    raise ConflictError("Draft identity cannot change")
                if old.activated_revision is not None:
                    raise ConflictError("Activated draft is immutable; create a new draft")
            if (old.revision if old else 0) != command.expected_draft_revision:
                raise ConflictError("Draft revision changed")
            revision = current.revision + 1
            if command.operation == "save":
                if command.content_json is None:
                    raise ValidationError("Draft content required")
                if command.kind == "character":
                    content = CharacterProposal.model_validate_json(
                        command.content_json
                    ).model_dump_json()
                else:
                    if member.role != "gm":
                        raise AuthorizationError("Scenario authoring requires GM")
                    from wayfarer.simulation.studio import ScenarioGraph

                    content = ScenarioGraph.model_validate_json(
                        command.content_json
                    ).model_dump_json()
                draft = AuthorDraft(
                    id=command.draft_id,
                    owner_id=old.owner_id if old else principal_id,
                    actor_id=command.actor_id,
                    kind=command.kind,
                    revision=command.expected_draft_revision + 1,
                    content_json=content,
                    previous_json=old.content_json if old else None,
                )
            else:
                if old is None or old.kind != "character":
                    raise ValidationError("Character draft required")
                proposal = CharacterProposal.model_validate_json(old.content_json)
                reviewer = self.play.engine.reviewer
                if command.operation == "submit":
                    review = reviewer.review(proposal)
                    if review.status in ("illegal", "blocked"):
                        raise ValidationError("Only legal, unblocked drafts can be submitted")
                    draft = old.model_copy(
                        update={
                            "submitted_revision": old.revision + 1,
                            "revision": old.revision + 1,
                        }
                    )
                elif command.operation == "approve":
                    if old.submitted_revision is None:
                        raise ValidationError("Submit the draft before GM approval")
                    approval = reviewer.approve(
                        proposal,
                        campaign_id=cid,
                        actor_id=old.actor_id,
                        revision=revision,
                        approver_id=principal_id,
                        reason=command.reason,
                    )
                    draft = old.model_copy(
                        update={
                            "approval_json": approval.model_dump_json(),
                            "revision": old.revision + 1,
                        }
                    )
                else:
                    if old.submitted_revision is not None and old.approval_json is None:
                        raise ValidationError("Submitted draft requires explicit GM approval")
                    # Creation is separate from advancement: never reset spent resources or bypass XP.
                    if (
                        current.resources.game_time
                        or current.advancement
                        or current.encounters
                        or current.scene_events
                        and any(e.revision for e in current.scene_events)
                        or current.recovery.setbacks
                    ):
                        raise ConflictError(
                            "Active characters must use advancement; workshop activation is setup-only"
                        )
                    approval = (
                        Approval.model_validate_json(old.approval_json)
                        if old.approval_json
                        else reviewer.approve(
                            proposal, campaign_id=cid, actor_id=old.actor_id, revision=revision
                        )
                    )
                    build, runtime = reviewer.activate(
                        proposal, approval, campaign_id=cid, actor_id=old.actor_id
                    )
                    if not any(a.actor_id == old.actor_id for a in current.actors):
                        raise ValidationError("Setup actor does not exist")
                    current = current.model_copy(
                        update={
                            "actors": tuple(
                                a.model_copy(update={"proposal": proposal, "approval": approval})
                                if a.actor_id == old.actor_id
                                else a
                                for a in current.actors
                            ),
                            "approvals": current.approvals + (approval,),
                            "resources": current.resources.model_copy(
                                update={
                                    "owners": tuple(
                                        o.model_copy(
                                            update={
                                                "definitions": tuple(
                                                    p.definition_id for p in build.purchases
                                                )
                                            }
                                        )
                                        if o.actor_id == old.actor_id
                                        else o
                                        for o in current.resources.owners
                                    ),
                                    "pools": tuple(
                                        _refreshed(
                                            p,
                                            runtime.hp if p.id.startswith("hp:") else runtime.fp,
                                            build,
                                        )
                                        if p.id in (f"hp:{old.actor_id}", f"fp:{old.actor_id}")
                                        else p
                                        for p in current.resources.pools
                                    ),
                                }
                            ),
                        }
                    )
                    draft = old.model_copy(
                        update={
                            "activated_revision": revision,
                            "approval_json": approval.model_dump_json(),
                            "revision": old.revision + 1,
                        }
                    )
            current = current.model_copy(
                update={
                    "revision": revision,
                    "resources": current.resources.model_copy(update={"revision": revision}),
                    "rulings": tuple(
                        r.model_copy(update={"valid_revision": revision})
                        if command.operation != "activate"
                        and r.current_status(current.revision, current.resources.game_time)
                        in ("pending", "approved")
                        else r
                        for r in current.rulings
                    ),
                    "drafts": tuple(d for d in current.drafts if d.id != draft.id) + (draft,),
                }
            )
            self.play.commit(campaign, current)
            return CommandReceipt(action="workshop", outcome=command.operation)

        await commit_command(
            self.play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=self.play.rng,
        )
        return await self.read(cid, command.draft_id, principal_id=principal_id)

    async def generate(
        self, cid: str, command: DraftCommand, *, principal_id: str, prompt: str, llm: Orchestrator
    ) -> dict[str, object]:
        if command.operation != "save" or command.kind != "character":
            raise ValidationError("Character generation requires a save command")
        state = self.play._load(await self.play.store.read(cid))
        self.access._control(self.access._member(state, principal_id), command.actor_id)
        if state.revision != command.expected_revision:
            raise ConflictError("Generation context changed")
        compiler = self.play.engine.reviewer.compiler
        old = next((d for d in state.drafts if d.id == command.draft_id), None)
        if old:
            self._get(state, old.id, principal_id)
            if (old.kind, old.actor_id) != (command.kind, command.actor_id):
                raise ConflictError("Draft identity cannot change")
            if old.activated_revision is not None:
                raise ConflictError("Activated draft is immutable; create a new draft")
        if (old.revision if old else 0) != command.expected_draft_revision:
            raise ConflictError("Draft revision changed")
        context = json.dumps(
            {
                "catalog_ids": sorted(compiler.definitions),
                "policy": json.loads(TypeAdapter(CampaignPolicy).dump_json(compiler.policy)),
                "previous": old.content_json if old else None,
            }
        )
        raw = await llm._call(
            ProviderRequest(
                operation="character_draft",
                session_id=f"workshop:{cid}:{principal_id}:{command.draft_id}:{command.expected_draft_revision}",
                context_json=context,
                prompt=prompt,
                output_schema=CharacterProposal.model_json_schema(),
            )
        )
        proposal = CharacterProposal.model_validate_json(raw)
        # Save uses the original CAS after the provider returns. Illegal drafts remain editable.
        return await self.execute(
            cid,
            command.model_copy(update={"content_json": proposal.model_dump_json()}),
            principal_id=principal_id,
        )
