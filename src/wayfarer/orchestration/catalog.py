"""Owner-authorized reusable scenarios, using the shared document validator."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.providers import Orchestrator, ProviderRequest
from wayfarer.orchestration.scenario_documents import ScenarioDocuments, bind_party, parse_document
from wayfarer.orchestration.setup import SetupService
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.persistence.catalog import CatalogStore
from wayfarer.simulation.catalog import (
    CatalogCommand,
    CatalogEntry,
    CatalogRevision,
    CatalogSummary,
    InstantiateRevision,
    RevisionView,
    ScenarioGenerationJob,
    ScenarioGenerationRequest,
)
from wayfarer.simulation.scenario_document import DraftRevision, Provenance, PublicBrief
from wayfarer.simulation.setup import CreateSetup
from wayfarer.simulation.studio import ScenarioGraph, StudioFinding


class ScenarioCatalog:
    def __init__(self, setup: SetupService, authors: frozenset[str]) -> None:
        self.setup = setup
        self.store = CatalogStore(setup.play.store)
        self.documents = ScenarioDocuments(
            ScenarioStudio(setup.play, npc_reviewer=setup.play.engine.reviewer), author_ids=authors
        )

    @staticmethod
    def owner(entry: CatalogEntry, principal: str) -> None:
        if entry.owner_id != principal:
            raise NotFoundError("Scenario not found")

    @staticmethod
    def revision(entry: CatalogEntry, revision: int | None) -> CatalogRevision:
        index = len(entry.revisions) if revision is None else revision
        if not 1 <= index <= len(entry.revisions):
            raise NotFoundError("Scenario revision not found")
        return entry.revisions[index - 1]

    def summary(self, entry: CatalogEntry) -> CatalogSummary:
        revision = self.revision(entry, None)
        report = self.documents.validate(revision.draft.content_json)
        try:
            public = parse_document(revision.draft.content_json).public
            title, summary = public.title, public.summary
        except ValueError:
            title, summary = "Unfinished scenario", "Draft needs structural corrections."
        return CatalogSummary(
            id=entry.id,
            owner_id=entry.owner_id,
            version=entry.version,
            archived=entry.archived,
            revision=revision.draft.edit,
            published=revision.published is not None,
            title=title,
            summary=summary,
            status=report.status,
        )

    async def listing(self, principal: str) -> tuple[CatalogSummary, ...]:
        self.documents.authorize(principal)
        return tuple(self.summary(e) for e in await self.store.listing(principal))

    async def read(self, cid: str, principal: str, revision: int | None = None) -> RevisionView:
        self.documents.authorize(principal)
        entry = await self.store.read(cid)
        self.owner(entry, principal)
        selected = self.revision(entry, revision)
        return RevisionView(
            entry=self.summary(entry),
            revision=selected,
            current_report=self.documents.validate(selected.draft.content_json),
        )

    def source(self, source: str, cid: str, edit: int, *, importing: bool) -> str:
        if len(source.encode("utf-8")) > 2_000_000:
            raise ValidationError("Scenario exceeds the 2 MB limit")
        try:
            document = parse_document(source)
        except ValueError:
            if importing:
                raise ValidationError(
                    "Import requires a structurally valid v1 scenario document"
                ) from None
            return source
        if importing:
            report = self.documents.validate(source)
            if any(f.code in ("document.rules", "document.capabilities") for f in report.findings):
                raise ValidationError("Imported document is incompatible with this engine")
        # Import is always an explicit fork. Never overwrite an identity supplied in JSON.
        document = document.model_copy(
            update={
                "scenario_id": cid,
                "revision_id": str(uuid5(NAMESPACE_URL, f"{cid}:{edit}")),
                "revision": edit,
                "graph": document.graph.model_copy(update={"id": cid}),
            }
        )
        return document.canonical()

    async def execute(
        self, principal: str, command: CatalogCommand, cid: str | None = None
    ) -> CatalogEntry:
        self.documents.authorize(principal)
        creating = command.operation in ("create", "import", "duplicate")
        if creating != (cid is None or command.operation == "duplicate"):
            raise ValidationError("Operation does not match scenario resource")
        source: str | None
        original_cid = cid
        if command.operation == "duplicate":
            if cid is None:
                raise ValidationError("Duplicate requires a source scenario")
            original = await self.store.read(cid)
            self.owner(original, principal)
            # Source revision is explicit and immutable, so retries survive later edits.
            if command.revision is None:
                raise ValidationError("Duplicate requires a source revision")
            source = self.revision(original, command.revision).draft.content_json
        else:
            source = command.content_json
        if creating:
            cid = str(uuid5(NAMESPACE_URL, json.dumps(["scenario", principal, command.id])))
        assert cid is not None
        payload = json.dumps(
            {"resource": original_cid, "command": command.model_dump(mode="json")}, sort_keys=True
        )

        def resolve(previous: CatalogEntry | None) -> CatalogEntry:
            if previous:
                self.owner(previous, principal)
            if command.expected_version != (previous.version if previous else 0):
                raise ConflictError("Scenario changed; reload before saving")
            if creating and previous:
                raise ConflictError("Scenario already exists")
            if not creating and previous is None:
                raise NotFoundError("Scenario not found")
            if previous and previous.archived:
                raise ConflictError("Archived scenarios are read-only; duplicate to edit")
            revisions = list(previous.revisions if previous else ())
            if command.operation in ("create", "import", "duplicate", "save"):
                if source is None:
                    raise ValidationError("Scenario content is required")
                edit = len(revisions) + 1
                content = self.source(source, cid, edit, importing=command.operation == "import")
                draft = self.documents.save_draft(
                    content,
                    draft_id=cid,
                    principal_id=principal,
                    previous=revisions[-1].draft if revisions else None,
                    expected_edit=edit - 1,
                    party=command.party,
                )
                revisions.append(CatalogRevision(draft=draft))
            elif command.operation in ("validate", "publish"):
                assert previous is not None
                revision = self.revision(previous, command.revision)
                if command.operation == "publish":
                    if revision.published is not None:
                        raise ConflictError("Revision is already published")
                    published = self.documents.publish(
                        revision.draft, principal_id=principal, party=command.party
                    )
                    revisions[revision.draft.edit - 1] = revision.model_copy(
                        update={"published": published}
                    )
                # Validation results are saved as a new immutable draft revision.
                else:
                    content = self.source(
                        revision.draft.content_json, cid, len(revisions) + 1, importing=False
                    )
                    revisions.append(
                        CatalogRevision(
                            draft=DraftRevision(
                                id=cid,
                                edit=len(revisions) + 1,
                                content_json=content,
                                source_digest=hashlib.sha256(content.encode()).hexdigest(),
                                report=self.documents.validate(content, party=command.party),
                            )
                        )
                    )
            return CatalogEntry(
                id=cid,
                owner_id=principal,
                version=(previous.version if previous else 0) + 1,
                archived=command.operation == "archive",
                revisions=tuple(revisions),
            )

        return await self.store.commit(cid, principal, command.id, payload, resolve)

    async def instantiate(
        self, cid: str, principal: str, command: InstantiateRevision
    ) -> dict[str, object]:
        self.documents.authorize(principal)
        entry = await self.store.read(cid)
        self.owner(entry, principal)
        revision = self.revision(entry, command.revision)
        if revision.published is None:
            raise ValidationError("Publish a valid revision before creating a game")
        source = revision.published.content_json
        if self.documents.validate(source, party=command.party).status != "playable":
            raise ValidationError("Scenario or party is incompatible")
        document = parse_document(source)
        graph = bind_party(document, command.party)
        # Deterministic campaign identity and creation payload provide crash-safe retries.
        return await self.setup.create(
            CreateSetup(id=command.id, brief=graph.brief, graph=graph),
            principal_id=principal,
            document_json=source,
        )

    async def create_generation_job(
        self, principal: str, request: ScenarioGenerationRequest
    ) -> ScenarioGenerationJob:
        self.documents.authorize(principal)
        if request.source_json is not None:
            digest = hashlib.sha256(request.source_json.encode()).hexdigest()
            if request.source_digest != digest:
                raise ValidationError("Scenario source changed before generation")
            try:
                parse_document(request.source_json)
            except ValueError as exc:
                raise ValidationError("Refinement requires a valid saved scenario source") from exc
        elif request.source_digest is not None or request.section != "all":
            raise ValidationError("Section refinement requires a scenario source")
        return await self.store.create_job(
            ScenarioGenerationJob(
                id=request.id,
                owner_id=principal,
                version=1,
                status="queued",
                request=request,
            )
        )

    async def read_generation_job(self, principal: str, job_id: str) -> ScenarioGenerationJob:
        self.documents.authorize(principal)
        return await self.store.read_job(job_id, principal)

    async def cancel_generation_job(self, principal: str, job_id: str) -> ScenarioGenerationJob:
        for _ in range(3):
            job = await self.read_generation_job(principal, job_id)
            if job.status in ("succeeded", "failed", "cancelled"):
                return job
            try:
                return await self.store.update_job(
                    job.model_copy(update={"status": "cancelled"}), job.version
                )
            except ConflictError:
                continue
        raise ConflictError("Generation job changed; retry cancellation")

    @staticmethod
    def _merge_section(current: ScenarioGraph, generated: ScenarioGraph, section: str) -> ScenarioGraph:
        if section == "all":
            return generated
        fields: dict[str, tuple[str, ...]] = {
            "brief": ("title", "brief"),
            "opening": ("opening_scene_id", "opening_action"),
            "world": ("world", "scenes", "actions", "approaches", "noncombat", "recovery"),
            "objectives": ("objectives", "failure_consequence"),
            "characters": (
                "actors",
                "npc_actor_ids",
                "resources",
                "npcs",
                "combat_attacks",
                "combat_consequences",
                "combat_protection",
            ),
        }
        return current.model_copy(
            update={name: getattr(generated, name) for name in fields[section]}
        )

    async def run_generation_job(
        self, principal: str, job_id: str, llm: Orchestrator
    ) -> ScenarioGenerationJob:
        job = await self.read_generation_job(principal, job_id)
        if job.status not in ("queued", "failed"):
            return job
        job = await self.store.update_job(
            job.model_copy(
                update={
                    "status": "running",
                    "proposal_json": None,
                    "report": None,
                    "error_code": None,
                    "error_message": None,
                }
            ),
            job.version,
        )
        request = job.request
        current_document = (
            parse_document(request.source_json) if request.source_json is not None else None
        )
        current_graph = bind_party(current_document) if current_document is not None else None
        current_context: object = None
        if current_graph is not None:
            dumped = current_graph.model_dump(mode="json")
            selected = (
                dumped
                if request.section == "all"
                else {
                    name: dumped[name]
                    for name in {
                        "brief": ("title", "brief"),
                        "opening": ("opening_scene_id", "opening_action"),
                        "world": ("world", "scenes", "actions", "approaches", "noncombat", "recovery"),
                        "objectives": ("objectives", "failure_consequence"),
                        "characters": ("actors", "npc_actor_ids", "resources", "npcs"),
                    }[request.section]
                }
            )
            current_context = selected
        context: dict[str, object] = {
            "brief": request.brief.model_dump(mode="json"),
            "party_capabilities": request.party_capabilities,
            "catalog_ids": sorted(self.setup.play.engine.reviewer.compiler.definitions),
            "section": request.section,
            "current_scenario": current_context,
        }
        if len(json.dumps(context)) > 23_000:
            context["current_scenario"] = {
                "content_digest": request.source_digest,
                "note": "The accepted scenario is too large for provider context; propose only the requested section.",
            }
        graph: ScenarioGraph | None = None
        report = None
        for _ in range(request.attempts):
            raw = await llm._call(
                ProviderRequest(
                    operation="scenario_draft",
                    session_id=f"scenario-authoring:{principal}:{job.id}",
                    context_json=json.dumps(context),
                    prompt=(
                        "Create a complete runtime-backed scenario proposal using only supplied "
                        "catalog IDs and supported mechanics. Include an actionable opening, "
                        "multiple approaches, explicit success/partial/failure consequences, and "
                        "a supported escape or rescue route for every capture outcome. "
                        + request.instructions
                    )[:4000],
                    output_schema=ScenarioGraph.model_json_schema(),
                )
            )
            graph = ScenarioGraph.model_validate_json(raw)
            if current_graph is not None:
                graph = self._merge_section(current_graph, graph, request.section)
            report = self.documents.studio.validate(graph)
            if report.valid:
                break
            context["validation"] = report.model_dump(mode="json")
        assert graph is not None and report is not None
        revision = (current_document.revision + 1) if current_document else 1
        from wayfarer.orchestration.scenario_documents import adapt_graph

        document = adapt_graph(
            graph,
            studio=self.documents.studio,
            revision_id=f"proposal-{job.id}",
            author=principal,
            public=PublicBrief(
                title=graph.title,
                summary=graph.brief.premise,
                setup=graph.brief,
                opening_prompt=graph.opening_action,
            ),
        ).model_copy(
            update={
                "revision": revision,
                "provenance": Provenance(
                    kind="generated",
                    author=principal,
                    generator="configured-provider",
                    source_digest=current_document.digest if current_document else None,
                ),
                "gm_notes": current_document.gm_notes if current_document else "",
            }
        )
        source = document.canonical()
        document_report = self.documents.validate(source)
        if not report.valid:
            document_report = document_report.model_copy(
                update={
                    "findings": document_report.findings
                    + (
                        StudioFinding(
                            code="generation.repair_exhausted",
                            severity="error",
                            reference=graph.id,
                            message="The bounded repair budget was exhausted; edit or retry the proposal.",
                        ),
                    ),
                    "status": "invalid",
                }
            )
        latest = await self.read_generation_job(principal, job_id)
        if latest.status == "cancelled":
            return latest
        return await self.store.update_job(
            latest.model_copy(
                update={
                    "status": "succeeded"
                    if document_report.status == "playable"
                    else "needs_review",
                    "proposal_json": source,
                    "report": document_report,
                }
            ),
            latest.version,
        )