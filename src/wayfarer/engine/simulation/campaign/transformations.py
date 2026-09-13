"""Typed, authored character transformations (Basic Set Characters B294-B296).

This module contains campaign nouns and aggregate validation only.  Compilation,
approval, resource rebasing, and persistence are orchestration responsibilities.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import CharacterDraft
from wayfarer.engine.character.power import Approval, CharacterProposal
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.models import Id, Record

TransformationKind = Literal[
    "body-modification", "mind-transfer", "supernatural-affliction", "death-transformation"
]
AttachmentKind = Literal["inventory", "credentials", "relationships", "knowledge", "control"]
AttachmentOwner = Literal["mind", "body", "neither"]
PointPolicy = Literal["charge", "adjust", "debt"]


class TraitRoute(Record):
    """Which side of a transfer supplies or relinquishes one purchased trait."""

    definition_id: Id
    follows: AttachmentOwner


class AttachmentRoute(Record):
    """Explicit authority decision for state not contained in the character build."""

    kind: AttachmentKind
    follows: AttachmentOwner
    # For inventory this is a resource owner; for control it is a principal.
    # Other domains retain the destination as audit evidence even when their
    # current canonical representation is immutable character history.
    destination_id: Id | None = None

    @model_validator(mode="after")
    def destination_is_explicit(self) -> Self:
        if self.follows == "mind" and self.destination_id is not None:
            raise ValueError("Mind-following authority retains its existing owner")
        if self.follows == "body" and self.destination_id is None:
            raise ValueError("Body-following authority requires an explicit destination")
        return self


class TransformationRule(Record):
    """One campaign-authored and selectable transformation path."""

    id: Id
    actor_id: Id
    kind: TransformationKind
    source_ref: str = Field(pattern=r"^B29[4-6]$")
    target: CharacterDraft
    target_body_id: Id
    target_body: HumanBody | None = None
    trait_routes: tuple[TraitRoute, ...] = Field(min_length=1)
    attachment_routes: tuple[AttachmentRoute, ...] = Field(min_length=5, max_length=5)
    point_policy: PointPolicy = "adjust"
    voluntary: bool = True
    treatment_seconds: int = Field(default=0, ge=0)
    recovery_seconds: int = Field(default=0, ge=0)
    expires_after_seconds: int | None = Field(default=None, ge=1)
    reversible: bool = False
    curable: bool = False
    requires_death: bool = False
    payment_item_id: Id | None = None
    payment_quantity: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        kinds = [route.kind for route in self.attachment_routes]
        if len(set(kinds)) != len(kinds) or set(kinds) != {
            "inventory",
            "credentials",
            "relationships",
            "knowledge",
            "control",
        }:
            raise ValueError("Every transformation authority domain must be declared exactly once")
        ids = [route.definition_id for route in self.trait_routes]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate transformation trait route")
        if (self.payment_item_id is None) != (self.payment_quantity == 0):
            raise ValueError("Transformation payment requires both an item and positive quantity")
        if self.kind == "death-transformation" and not self.requires_death:
            raise ValueError("Death transformations must require an authoritative death state")
        if self.kind == "death-transformation" and self.reversible:
            raise ValueError("Returning to a dead build requires another authored death path")
        if self.requires_death and self.kind != "death-transformation":
            raise ValueError("Only an authored death transformation may cross the death boundary")
        if self.kind != "supernatural-affliction" and self.expires_after_seconds is not None:
            raise ValueError("Only supernatural afflictions have an expiry")
        if self.kind != "supernatural-affliction" and self.curable:
            raise ValueError("Only supernatural afflictions have a cure boundary")
        if (
            self.kind == "supernatural-affliction"
            and not self.voluntary
            and self.point_policy != "adjust"
        ):
            raise ValueError("Involuntary afflictions adjust value without spending earned points")
        return self


class TransformationRules(Record):
    id: Id
    version: int = Field(ge=1)
    transformations: tuple[TransformationRule, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({rule.id for rule in self.transformations}) != len(self.transformations):
            raise ValueError("Duplicate transformation rule ID")
        return self


class TransformationRecord(Record):
    id: Id
    proposal_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_id: Id
    actor_id: Id
    kind: TransformationKind
    status: Literal["proposed", "treatment", "active", "interrupted", "reverted"]
    source_ref: str
    source_proposal: CharacterProposal
    target_proposal: CharacterProposal
    source_approval: Approval | None
    target_approval: Approval | None = None
    source_build_revision: str
    target_build_revision: str
    source_body_id: Id
    source_body: HumanBody | None = None
    target_body_id: Id
    trait_routes: tuple[TraitRoute, ...]
    attachment_routes: tuple[AttachmentRoute, ...]
    proposed_by: Id
    approved_by: Id | None = None
    proposed_at: int = Field(ge=0)
    approved_at: int | None = Field(default=None, ge=0)
    ready_at: int | None = Field(default=None, ge=0)
    recovery_until: int | None = Field(default=None, ge=0)
    expires_at: int | None = Field(default=None, ge=0)
    resolved_at: int | None = Field(default=None, ge=0)
    point_value_delta: int
    points_charged: int = Field(default=0, ge=0)
    source_item_owners: tuple[tuple[Id, Id], ...] = ()
    source_item_credentials: tuple[tuple[Id, tuple[Id, ...]], ...] = ()
    source_control_principal_ids: tuple[Id, ...] = ()
    source_knowledge_fact_ids: tuple[Id, ...] = ()


class TransformationState(Record):
    records: tuple[TransformationRecord, ...] = ()


def current_body_id(state: TransformationState, actor_id: str) -> str:
    records = [
        record
        for record in state.records
        if record.actor_id == actor_id and record.status in ("active", "reverted")
    ]
    if not records:
        return actor_id
    latest = records[-1]
    return latest.source_body_id if latest.status == "reverted" else latest.target_body_id


def validate_transformations(
    rules: TransformationRules | None,
    state: TransformationState,
    actor_ids: frozenset[str],
) -> None:
    if rules is None:
        if state.records:
            raise ValueError("Transformation state requires campaign-authored rules")
        return
    authored = {rule.id: rule for rule in rules.transformations}
    if len({record.id for record in state.records}) != len(state.records) or len(
        {record.proposal_id for record in state.records}
    ) != len(state.records):
        raise ValueError("Duplicate transformation record")
    for record in state.records:
        rule = authored.get(record.rule_id)
        if (
            rule is None
            or record.actor_id not in actor_ids
            or rule.actor_id != record.actor_id
            or rule.kind != record.kind
            or rule.source_ref != record.source_ref
            or rule.target_body_id != record.target_body_id
            or record.target_proposal.draft != rule.target
            or rule.trait_routes != record.trait_routes
            or rule.attachment_routes != record.attachment_routes
        ):
            raise ValueError("Persisted transformation disagrees with authored campaign rules")
        if record.target_approval is not None and (
            record.target_approval.actor_id != record.actor_id
            or record.target_approval.build_revision != record.target_build_revision
        ):
            raise ValueError("Transformation target approval does not match its build")
        if record.status == "proposed" and (
            record.approved_by is not None or record.target_approval is not None
        ):
            raise ValueError("Unapproved transformation contains approval authority")
        if record.status in ("treatment", "active", "reverted") and (
            record.approved_by is None or record.target_approval is None
        ):
            raise ValueError("Transformation crossed approval boundary without approval")
        if record.status == "treatment" and record.ready_at is None:
            raise ValueError("Treatment requires a completion deadline")
        if (
            record.status == "active"
            and rule.expires_after_seconds is not None
            and record.expires_at is None
        ):
            raise ValueError("Temporary affliction requires an expiry deadline")


def visible_transformations(
    state: TransformationState, *, actor_ids: frozenset[str], is_gm: bool
) -> tuple[TransformationRecord, ...]:
    """Do not expose another player's proposed build or authority mapping."""

    return state.records if is_gm else tuple(r for r in state.records if r.actor_id in actor_ids)
