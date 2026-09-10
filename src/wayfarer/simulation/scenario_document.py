"""Portable v1 scenario contracts. Prose never defines executable mechanics."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from wayfarer.character.power import CharacterProposal
from wayfarer.models import Id, Record
from wayfarer.rules.catalog import CampaignRules
from wayfarer.rules.transport_types import Transport
from wayfarer.simulation.actions import ActorSetup
from wayfarer.simulation.resources import ResourceState
from wayfarer.simulation.studio import GenerationBrief, ScenarioContent, StudioFinding

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Capability = Literal[
    "actions-v1",
    "scenes-v1",
    "objectives-v1",
    "combat-v1",
    "noncombat-v1",
    "npc-plans-v1",
    "recovery-v1",
    "split-party-v1",
    "adjudication-v1",
]


def canonical_json(value: object) -> str:
    """UTF-8 JSON, sorted object keys, preserved array order, no insignificant space."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class Provenance(Record):
    kind: Literal["authored", "generated", "adapted"]
    author: str = Field(min_length=1, max_length=200)
    source: str | None = Field(default=None, max_length=1000)
    generator: str | None = Field(default=None, max_length=200)
    source_digest: Digest | None = None


class Compatibility(Record):
    rules: CampaignRules
    # Includes compilation, power review, equipment and resource-policy configuration.
    engine_digest: Digest
    capabilities: tuple[Capability, ...] = Field(min_length=3)


class PublicBrief(Record):
    """Explicit author-approved allowlist, never populated from the private graph."""

    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    tags: tuple[str, ...] = Field(default=(), max_length=30)
    setup: GenerationBrief
    opening_prompt: str = Field(min_length=1, max_length=1000)


class PartySlot(Record):
    """An actor ID local to this scenario, never an account or membership ID."""

    actor_id: Id
    role: str = Field(min_length=1, max_length=200)
    required_definitions: tuple[Id, ...] = ()
    aware_of: tuple[Id, ...] = ()
    conditions: tuple[Literal["unconscious", "stunned", "restrained"], ...] = ()


class PartyRequirements(Record):
    slots: tuple[PartySlot, ...] = Field(min_length=1, max_length=30)
    minimum_points: int = Field(default=0, ge=0)
    maximum_points: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> PartyRequirements:
        if self.minimum_points > self.maximum_points:
            raise ValueError("Invalid party point range")
        if len({s.actor_id for s in self.slots}) != len(self.slots):
            raise ValueError("Duplicate party slot")
        return self


class PregeneratedCharacter(Record):
    slot_id: Id
    proposal: CharacterProposal


class InitialResources(ResourceState):
    """Only initial resources; runtime history and elapsed time are forbidden."""

    # Internal runtime state is not an addition to the frozen v1 authoring API.
    transports: SkipJsonSchema[tuple[Transport, ...]] = Field(default=(), exclude=True)

    @model_validator(mode="after")
    def reject_transport_input(self) -> InitialResources:
        if "transports" in self.model_fields_set or self.transports:
            raise ValueError("Transport authoring is not supported by scenario v1")
        return self

    revision: Literal[0] = 0
    game_time: Literal[0] = 0
    fired: tuple[()] = ()
    receipts: tuple[()] = ()
    events: tuple[()] = ()


class PortableGraph(ScenarioContent):
    resources: InitialResources

    @model_validator(mode="after")
    def single_mechanics_source(self) -> PortableGraph:
        # runtime_rules replaces these nested copies. Reject ambiguity in portable content.
        for name in ("scenes", "objectives", "noncombat", "npcs", "recovery", "party", "abilities"):
            nested = getattr(self.actions, name)
            if nested is not None and nested != getattr(self, name):
                raise ValueError(f"Conflicting actions.{name} mechanics")
        if self.actions.combat is None and (
            self.combat_attacks
            or self.combat_consequences
            or self.combat_protection
            or self.combat_equipment
        ):
            raise ValueError("Combat profiles require enabled combat mechanics")
        if self.actions.combat:
            for name, expected in (
                ("attacks", self.combat_attacks),
                ("gurps_equipment", self.combat_equipment),
                ("consequences", self.combat_consequences),
                ("protection", self.combat_protection),
            ):
                nested = getattr(self.actions.combat, name)
                if nested and nested != expected:
                    raise ValueError("Conflicting combat mechanics")
        return self


class ScenarioDocumentBase(Record):
    schema_version: Literal[1, 2]
    scenario_id: Id
    revision_id: Id
    revision: int = Field(ge=1)
    public: PublicBrief
    provenance: Provenance
    compatibility: Compatibility
    party: PartyRequirements
    graph: PortableGraph
    npc_actors: tuple[ActorSetup, ...] = ()
    pregenerated: tuple[PregeneratedCharacter, ...] = ()
    gm_notes: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def identities(self) -> ScenarioDocumentBase:
        if self.scenario_id != self.graph.id:
            raise ValueError("Scenario and graph identity must match")
        slots = {s.actor_id for s in self.party.slots}
        npcs = {a.actor_id for a in self.npc_actors}
        if len(npcs) != len(self.npc_actors) or npcs & slots:
            raise ValueError("Duplicate NPC or overlapping party identity")
        if npcs != set(self.graph.npc_actor_ids) or len(npcs) != len(self.graph.npc_actor_ids):
            raise ValueError("NPC references must match authored NPC builds")
        pregens = {p.slot_id for p in self.pregenerated}
        if len(pregens) != len(self.pregenerated) or not pregens <= slots:
            raise ValueError("Duplicate or unknown pregenerated slot")
        capabilities = self.compatibility.capabilities
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("Duplicate engine capability")
        return self

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))


class ScenarioDocument(ScenarioDocumentBase):
    schema_version: Literal[1]


class PlayerScenarioExport(Record):
    export_kind: Literal["player-brief"] = "player-brief"
    schema_version: Literal[1] = 1
    scenario_id: Id
    revision_id: Id
    brief: PublicBrief
    party_size: int = Field(ge=1, le=30)


class DocumentReport(Record):
    content_digest: Digest
    engine_digest: Digest
    party_digest: Digest | None = None
    status: Literal["invalid", "needs-party", "playable"]
    findings: tuple[StudioFinding, ...]


class DraftRevision(Record):
    """Persistence-ready draft aggregate, including structurally invalid source text."""

    id: Id
    edit: int = Field(ge=1)
    content_json: str = Field(max_length=2000000)
    source_digest: Digest
    report: DocumentReport


class PublishedRevision(Record):
    """Immutable serialized snapshot; derived models cannot mutate the stored content."""

    scenario_id: Id
    revision_id: Id
    revision: int = Field(ge=1)
    content_json: str
    report: DocumentReport

    @model_validator(mode="after")
    def consistent(self) -> PublishedRevision:
        document = parse_document(self.content_json)
        if (
            self.report.status != "playable"
            or self.report.content_digest != document.digest
            or self.report.engine_digest != document.compatibility.engine_digest
            or (self.scenario_id, self.revision_id, self.revision)
            != (document.scenario_id, document.revision_id, document.revision)
            or self.content_json != document.canonical()
        ):
            raise ValueError("Published revision does not match validated canonical content")
        return self


def parse_document(source: str) -> ScenarioDocumentBase:
    """Reject unknown versions and ambiguous JSON before strict domain decoding."""
    if len(source) > 2_000_000:
        raise ValueError("Scenario document exceeds the import size limit")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise ValueError(f"Non-finite JSON number: {value}")

    raw = json.loads(source, object_pairs_hook=pairs, parse_constant=constant)
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] not in (1, 2)
    ):
        raise ValueError("Unsupported scenario schema_version; explicit migration is required")
    if raw["schema_version"] == 2:
        from wayfarer.simulation.social_policy import SocialScenarioDocument

        return SocialScenarioDocument.model_validate_json(source)
    return ScenarioDocument.model_validate_json(source)
