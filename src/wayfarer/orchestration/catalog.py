"""Owner-authorized reusable scenarios, using the shared document validator."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from wayfarer.errors import ConflictError, NotFoundError, ValidationError
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
)
from wayfarer.simulation.scenario_document import DraftRevision
from wayfarer.simulation.setup import CreateSetup


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
