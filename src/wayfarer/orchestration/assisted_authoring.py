"""Durable, review-first scenario co-authoring over the configured provider.

Provider output is always an untrusted proposal. It never publishes, activates, or
replaces a catalog revision; the author must explicitly save/validate/publish it.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import Field

from wayfarer.errors import ConflictError, ProviderError, ValidationError
from wayfarer.orchestration.catalog import ScenarioCatalog
from wayfarer.orchestration.providers import Orchestrator, ProviderRequest
from wayfarer.orchestration.scenario_documents import parse_document
from wayfarer.simulation.catalog import AssistScenario, GenerationJob
from wayfarer.simulation.resources import Record
from wayfarer.simulation.scenario_document import (
    PartyRequirements,
    PortableGraph,
    Provenance,
    PublicBrief,
    ScenarioDocument,
)


class NotesProposal(Record):
    gm_notes: str = Field(default="", max_length=20000)


class AssistedAuthoring:
    def __init__(self, catalog: ScenarioCatalog) -> None:
        self.catalog = catalog

    async def start(self, cid: str, principal: str, command: AssistScenario) -> GenerationJob:
        self.catalog.documents.authorize(principal)
        entry = await self.catalog.store.read(cid)
        self.catalog.owner(entry, principal)
        if entry.archived:
            raise ConflictError("Archived scenarios are read-only; duplicate to edit")
        if command.expected_version != entry.version:
            raise ConflictError("Scenario changed; reload before generating")
        revision = self.catalog.revision(entry, command.revision)
        try:
            parse_document(revision.draft.content_json)
        except ValueError as exc:
            raise ValidationError(
                "AI refinement requires a structurally valid saved scenario revision"
            ) from exc
        return await self.catalog.store.create_job(
            GenerationJob(
                id=command.id,
                scenario_id=cid,
                owner_id=principal,
                base_revision=command.revision,
                base_version=command.expected_version,
                instruction=command.instruction,
                section=command.section,
                status="queued",
            )
        )

    async def read(self, cid: str, job_id: str, principal: str) -> GenerationJob:
        self.catalog.documents.authorize(principal)
        entry = await self.catalog.store.read(cid)
        self.catalog.owner(entry, principal)
        return await self.catalog.store.read_job(cid, job_id, principal)

    async def listing(self, cid: str, principal: str) -> tuple[GenerationJob, ...]:
        self.catalog.documents.authorize(principal)
        entry = await self.catalog.store.read(cid)
        self.catalog.owner(entry, principal)
        return await self.catalog.store.jobs(cid, principal)

    async def cancel(self, cid: str, job_id: str, principal: str) -> GenerationJob:
        self.catalog.documents.authorize(principal)
        entry = await self.catalog.store.read(cid)
        self.catalog.owner(entry, principal)

        def resolve(job: GenerationJob) -> GenerationJob:
            if job.status in ("needs_review", "failed", "cancelled"):
                return job
            return job.model_copy(update={"status": "cancelled", "error": None})

        return await self.catalog.store.update_job(cid, job_id, principal, resolve)

    @staticmethod
    def _context(document: ScenarioDocument, section: str) -> str:
        selected: object
        if section == "public":
            selected = document.public.model_dump(mode="json")
        elif section == "graph":
            selected = document.graph.model_dump(mode="json")
        elif section == "party":
            selected = document.party.model_dump(mode="json")
        elif section == "gm_notes":
            selected = {"gm_notes": document.gm_notes}
        else:
            selected = {
                "public": document.public.model_dump(mode="json"),
                "party": document.party.model_dump(mode="json"),
                "gm_notes": document.gm_notes,
            }
        payload = {
            "current": selected,
            "compatibility": document.compatibility.model_dump(mode="json"),
            "capability_rule": "Only use mechanics representable by the supplied schema and compatibility.",
        }
        encoded = json.dumps(payload, sort_keys=True)
        if len(encoded.encode("utf-8")) > 24000:
            payload["current"] = {
                "public": document.public.model_dump(mode="json"),
                "party_size": len(document.party.slots),
                "note": "The selected section was too large for provider context; regenerate from the brief.",
            }
            encoded = json.dumps(payload, sort_keys=True)
        if len(encoded.encode("utf-8")) > 24000:
            raise ValidationError("Scenario authoring context exceeds provider budget")
        return encoded

    @staticmethod
    def _schema(section: str) -> dict[str, object]:
        model = {
            "public": PublicBrief,
            "graph": PortableGraph,
            "party": PartyRequirements,
            "gm_notes": NotesProposal,
            "all": ScenarioDocument,
        }[section]
        return model.model_json_schema()

    @staticmethod
    def _merge(
        base: ScenarioDocument, section: str, raw: str, principal: str
    ) -> ScenarioDocument:
        update: dict[str, object]
        if section == "public":
            update = {"public": PublicBrief.model_validate_json(raw)}
        elif section == "graph":
            graph = PortableGraph.model_validate_json(raw).model_copy(update={"id": base.scenario_id})
            update = {"graph": graph}
        elif section == "party":
            update = {"party": PartyRequirements.model_validate_json(raw)}
        elif section == "gm_notes":
            update = {"gm_notes": NotesProposal.model_validate_json(raw).gm_notes}
        else:
            candidate = ScenarioDocument.model_validate_json(raw)
            update = {
                "public": candidate.public,
                "party": candidate.party,
                "graph": candidate.graph.model_copy(update={"id": base.scenario_id}),
                "npc_actors": candidate.npc_actors,
                "pregenerated": candidate.pregenerated,
                "gm_notes": candidate.gm_notes,
            }
        return base.model_copy(
            update={
                **update,
                "compatibility": base.compatibility,
                "provenance": Provenance(
                    kind="generated",
                    author=principal,
                    source="assisted-authoring",
                    generator="configured-provider",
                    source_digest=base.digest,
                ),
            }
        )

    async def run(
        self,
        cid: str,
        job_id: str,
        principal: str,
        orchestrator: Orchestrator,
    ) -> GenerationJob:
        job = await self.read(cid, job_id, principal)
        if job.status != "queued":
            return job

        def running(current: GenerationJob) -> GenerationJob:
            return current.model_copy(update={"status": "running"}) if current.status == "queued" else current

        job = await self.catalog.store.update_job(cid, job_id, principal, running)
        if job.status != "running":
            return job
        try:
            entry = await self.catalog.store.read(cid)
            self.catalog.owner(entry, principal)
            revision = self.catalog.revision(entry, job.base_revision)
            base = parse_document(revision.draft.content_json)
            session = hashlib.sha256(f"scenario:{cid}:{job.id}".encode()).hexdigest()
            raw = await orchestrator._call(
                ProviderRequest(
                    operation="scenario_draft",
                    session_id=session,
                    context_json=self._context(base, job.section),
                    prompt=(
                        "Create a reviewable scenario proposal from the author instruction. "
                        "Never invent credentials, approvals, runtime state, or mechanics outside the schema. "
                        "Preserve established content unless the requested section or instruction requires a change. "
                        f"Author instruction: {job.instruction}"
                    ),
                    output_schema=self._schema(job.section),
                )
            )
            proposal = self._merge(base, job.section, raw, principal)
            source = proposal.canonical()
            report = self.catalog.documents.validate(source)
        except (ProviderError, ValidationError, ValueError) as exc:
            def failed(current: GenerationJob) -> GenerationJob:
                if current.status == "cancelled":
                    return current
                return current.model_copy(
                    update={"status": "failed", "error": str(exc)[:1000] or "Generation failed"}
                )

            return await self.catalog.store.update_job(cid, job_id, principal, failed)

        def complete(current: GenerationJob) -> GenerationJob:
            if current.status == "cancelled":
                return current
            return current.model_copy(
                update={
                    "status": "needs_review",
                    "proposal_json": source,
                    "report": report,
                    "error": None,
                }
            )

        return await self.catalog.store.update_job(cid, job_id, principal, complete)
