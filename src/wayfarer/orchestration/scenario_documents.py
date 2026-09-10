"""One validation/binding path for authored, generated and imported documents.

Revision aggregates are storage-independent; the catalog supplies atomic writes and
uniqueness for published (scenario_id, revision_id) identities.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from pydantic import TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.character.power import PowerReviewer
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Campaign
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.rules.catalog import ImplementationStatus
from wayfarer.rules.effects import Effect
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.actions import ActorSetup
from wayfarer.simulation.scenario_document import (
    Capability,
    Compatibility,
    DocumentReport,
    DraftRevision,
    PartyRequirements,
    PartySlot,
    PlayerScenarioExport,
    PortableGraph,
    PregeneratedCharacter,
    Provenance,
    PublicBrief,
    PublishedRevision,
    ScenarioDocument,
    ScenarioDocumentBase,
    digest_json,
)
from wayfarer.simulation.scenario_document import (
    parse_document as parse_document,
)
from wayfarer.simulation.studio import ScenarioGraph, StudioFinding


def required_capabilities(graph: PortableGraph) -> tuple[Capability, ...]:
    result: list[Capability] = ["actions-v1", "scenes-v1", "objectives-v1"]
    for value, capability in (
        (graph.actions.combat, "combat-v1"),
        (graph.noncombat, "noncombat-v1"),
        (graph.npcs, "npc-plans-v1"),
        (graph.recovery, "recovery-v1"),
        (graph.party, "split-party-v1"),
        (graph.actions.adjudication, "adjudication-v1"),
    ):
        if value is not None:
            result.append(TypeAdapter(Capability).validate_python(capability))
    return tuple(result)


def engine_digest(studio: ScenarioStudio) -> str:
    def reviewer_config(reviewer: PowerReviewer) -> dict[str, object]:
        policy = reviewer.compiler.policy
        return {
            "rules": asdict(reviewer.compiler.rules),
            "policy": {
                **asdict(policy),
                "permitted_sources": sorted(policy.permitted_sources),
                "allowed_equipment": sorted(policy.allowed_equipment),
            },
            "power": reviewer.policy.model_dump(mode="json"),
            "effects": TypeAdapter(tuple[tuple[str, Effect], ...]).dump_python(
                reviewer.compiler.effects, mode="json"
            ),
        }

    return digest_json(
        {
            "validator": "scenario-document-v1",
            "player": reviewer_config(studio.play.engine.reviewer),
            "npc": reviewer_config(studio.npc_reviewer) if studio.npc_reviewer else None,
            "resources_rules": asdict(studio.play.engine.resources.rules),
            "equipment": [
                s.model_dump(mode="json")
                for _, s in sorted(studio.play.engine.resources.specs.items())
            ],
        }
    )


def adapt_graph(
    graph: ScenarioGraph,
    *,
    studio: ScenarioStudio,
    revision_id: str,
    public: PublicBrief,
    author: str,
    include_pregenerated: bool = True,
) -> ScenarioDocumentBase:
    """Explicit adapter for current initial graphs; callers retain the source bytes.

    A public brief is mandatory: private prose is never implicitly made public.
    Existing nonzero resource history fails PortableGraph validation.
    """
    raw = graph.model_dump(mode="json", exclude={"actors"})
    raw["actions"] = graph.runtime_rules().model_dump(mode="json")
    from wayfarer.simulation.npcs import NPCSocialRules
    from wayfarer.simulation.social_policy import SocialPortableGraph, SocialScenarioDocument

    social = isinstance(graph.npcs, NPCSocialRules)
    portable = (SocialPortableGraph if social else PortableGraph).model_validate_json(
        json.dumps(raw)
    )
    slots = tuple(
        PartySlot(
            actor_id=a.actor_id, role="Adventurer", aware_of=a.aware_of, conditions=a.conditions
        )
        for a in graph.actors
        if a.actor_id not in graph.npc_actor_ids
    )
    if any(a.available_at for a in graph.actors):
        raise ValidationError("Adapter requires a fresh actor timeline")
    value: dict[str, object] = dict(
        schema_version=2 if social else 1,
        scenario_id=graph.id,
        revision_id=revision_id,
        revision=1,
        public=public,
        provenance=Provenance(
            kind="adapted",
            author=author,
            source="ScenarioGraph",
            source_digest=digest_json(graph.model_dump(mode="json")),
        ),
        compatibility=Compatibility(
            rules=studio.play.engine.resources.rules,
            engine_digest=engine_digest(studio),
            capabilities=required_capabilities(portable),
        ),
        party=PartyRequirements(
            slots=slots, maximum_points=studio.play.engine.reviewer.compiler.policy.point_budget
        ),
        graph=portable,
        npc_actors=tuple(a for a in graph.actors if a.actor_id in graph.npc_actor_ids),
        pregenerated=tuple(
            PregeneratedCharacter(slot_id=a.actor_id, proposal=a.proposal)
            for a in graph.actors
            if a.actor_id not in graph.npc_actor_ids
        )
        if include_pregenerated
        else (),
    )
    encoded = TypeAdapter(dict[str, object]).dump_json(value)
    return (SocialScenarioDocument if social else ScenarioDocument).model_validate_json(encoded)


def bind_party(
    document: ScenarioDocumentBase, party: tuple[PregeneratedCharacter, ...] | None = None
) -> ScenarioGraph:
    """Bind proposals to scenario-local actor slots. Account membership is separate.

    Never perform string substitution on IDs, prose, catalog references or pools.
    A supplied party must fill every slot; pregens are used only when omitted.
    """
    selected = document.pregenerated if party is None else party
    by_slot = {p.slot_id: p.proposal for p in selected}
    if len(by_slot) != len(selected) or set(by_slot) != {s.actor_id for s in document.party.slots}:
        raise ValidationError("Party must bind exactly one legal character to every scenario slot")
    actors = (
        tuple(
            ActorSetup(
                actor_id=s.actor_id,
                proposal=by_slot[s.actor_id],
                aware_of=s.aware_of,
                conditions=s.conditions,
            )
            for s in document.party.slots
        )
        + document.npc_actors
    )
    raw = document.graph.model_dump(mode="json")
    raw["actors"] = [a.model_dump(mode="json") for a in actors]
    from wayfarer.simulation.social_policy import parse_graph

    return parse_graph(json.dumps(raw))


class ScenarioDocuments:
    def __init__(self, studio: ScenarioStudio, *, author_ids: frozenset[str] | None = None) -> None:
        self.studio = studio
        self.author_ids = studio.play.engine.reviewer.gm_ids if author_ids is None else author_ids

    def authorize(self, principal_id: str) -> None:
        if principal_id not in self.author_ids:
            raise AuthorizationError("Scenario author authority required")

    def validate(
        self, source: str, *, party: tuple[PregeneratedCharacter, ...] | None = None
    ) -> DocumentReport:
        configured = engine_digest(self.studio)
        content_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        findings: list[StudioFinding] = []

        def error(code: str, reference: str, message: str) -> None:
            findings.append(
                StudioFinding(code=code, reference=reference, message=message, severity="error")
            )

        try:
            document = parse_document(source)
        except (SchemaError, ValueError) as exc:
            error("document.structure", "document", str(exc))
            return DocumentReport(
                content_digest=content_digest,
                engine_digest=configured,
                status="invalid",
                findings=tuple(findings),
            )
        content_digest = document.digest
        if (
            document.compatibility.engine_digest != configured
            or document.compatibility.rules != self.studio.play.engine.resources.rules
        ):
            error(
                "document.rules",
                "compatibility",
                "Exact rules, catalog and engine configuration must match",
            )
        if not set(required_capabilities(document.graph)) <= set(
            document.compatibility.capabilities
        ):
            error(
                "document.capabilities",
                "compatibility",
                "Used engine capabilities must be declared",
            )

        # Check every collection's definition IDs, including world commitments and approaches.
        def duplicates(value: object, path: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    duplicates(child, f"{path}.{key}")
            elif isinstance(value, list):
                ids = [v["id"] for v in value if isinstance(v, dict) and "id" in v]
                if len(ids) != len(set(ids)):
                    error("document.duplicate", path, "Duplicate stable ID in collection")
                for i, child in enumerate(value):
                    duplicates(child, f"{path}[{i}]")

        duplicates(document.model_dump(mode="json"), "document")
        try:
            document.graph.world.validate()
            document.graph.scenes.validate_world(document.graph.world)
        except ValidationError as exc:
            error("document.references", "graph", str(exc))
        reviewer = self.studio.play.engine.reviewer
        for slot in document.party.slots:
            for definition_id in slot.required_definitions:
                definition = reviewer.compiler.definitions.get(definition_id)
                if definition is None or definition.status is not ImplementationStatus.IMPLEMENTED:
                    error(
                        "party.definition",
                        slot.actor_id,
                        "Party requirement uses an unavailable catalog definition",
                    )
        # Optional pregens must be legal even when a different validation party is supplied.
        for pregen in document.pregenerated:
            review = reviewer.review(pregen.proposal)
            slot = next(s for s in document.party.slots if s.actor_id == pregen.slot_id)
            if (
                review.status != "automatic"
                or not document.party.minimum_points
                <= review.compilation.spent
                <= document.party.maximum_points
                or not set(slot.required_definitions)
                <= {p.definition_id for p in pregen.proposal.draft.purchases}
            ):
                error(
                    "party.pregen",
                    pregen.slot_id,
                    "Pregenerated character is not automatically legal",
                )
        party_digest: str | None = None
        needs_party = party is None and len(document.pregenerated) != len(document.party.slots)
        if not needs_party:
            try:
                graph = bind_party(document, party)
                party_digest = digest_json([a.model_dump(mode="json") for a in graph.actors])
                by_id = {a.actor_id: a for a in graph.actors}
                for slot in document.party.slots:
                    proposal = by_id[slot.actor_id].proposal
                    compilation = reviewer.review(proposal).compilation
                    if (
                        not document.party.minimum_points
                        <= compilation.spent
                        <= document.party.maximum_points
                        or not set(slot.required_definitions)
                        <= {p.definition_id for p in proposal.draft.purchases}
                    ):
                        error(
                            "party.incompatible",
                            slot.actor_id,
                            "Character does not meet slot requirements",
                        )
                findings.extend(self.studio.validate(graph).findings)
            except (ValidationError, ValueError, KeyError, StopIteration) as exc:
                error("document.runtime", "graph", str(exc) or "Unresolved runtime reference")
        return DocumentReport(
            content_digest=content_digest,
            engine_digest=configured,
            party_digest=party_digest,
            findings=tuple(findings),
            status="invalid"
            if any(f.severity == "error" for f in findings)
            else "needs-party"
            if needs_party
            else "playable",
        )

    def save_draft(
        self,
        source: str,
        *,
        draft_id: str,
        principal_id: str,
        previous: DraftRevision | None = None,
        expected_edit: int = 0,
        party: tuple[PregeneratedCharacter, ...] | None = None,
    ) -> DraftRevision:
        self.authorize(principal_id)
        if expected_edit != (previous.edit if previous else 0) or (
            previous and previous.id != draft_id
        ):
            raise ConflictError("Draft edit changed")
        return DraftRevision(
            id=draft_id,
            edit=expected_edit + 1,
            content_json=source,
            source_digest=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            report=self.validate(source, party=party),
        )

    def publish(
        self,
        draft: DraftRevision,
        *,
        principal_id: str,
        party: tuple[PregeneratedCharacter, ...] | None = None,
    ) -> PublishedRevision:
        self.authorize(principal_id)
        # Never trust a saved or client-supplied validation status.
        report = self.validate(draft.content_json, party=party)
        if report.status != "playable":
            raise ValidationError(
                "Only a document validated with a compatible party can be published"
            )
        document = parse_document(draft.content_json)
        return PublishedRevision(
            scenario_id=document.scenario_id,
            revision_id=document.revision_id,
            revision=document.revision,
            content_json=document.canonical(),
            report=report,
        )

    def author_export(self, revision: PublishedRevision, *, principal_id: str) -> str:
        self.authorize(principal_id)
        return revision.content_json

    @staticmethod
    def player_export(revision: PublishedRevision) -> PlayerScenarioExport:
        document = parse_document(revision.content_json)
        return PlayerScenarioExport(
            scenario_id=document.scenario_id,
            revision_id=document.revision_id,
            brief=document.public,
            party_size=len(document.party.slots),
        )

    async def activate(
        self,
        revision: PublishedRevision,
        campaign: Campaign,
        members: tuple[CampaignMember, ...],
        *,
        principal_id: str,
        party: tuple[PregeneratedCharacter, ...] | None = None,
    ) -> PlayService:
        self.authorize(principal_id)
        # Restored/untrusted snapshots are revalidated, including current engine and actual party.
        revision = PublishedRevision.model_validate_json(revision.model_dump_json())
        report = self.validate(revision.content_json, party=party)
        if report.status != "playable":
            raise ValidationError("Document is incompatible with this engine or party")
        document = parse_document(revision.content_json)
        graph = bind_party(document, party)
        seed = campaign.copy()
        seed["scenario_document_json"] = revision.content_json
        return await self.studio.activate(graph, seed, members, principal_id=principal_id)
