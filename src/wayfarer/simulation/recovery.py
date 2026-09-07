"""Authored setbacks and recovery choices; no model-authored consequences."""

from typing import Literal

from pydantic import Field

from wayfarer.character.compiler import CharacterDraft
from wayfarer.character.power import CharacterProposal
from wayfarer.simulation.resources import Id, Record

SetbackKind = Literal["retreat", "surrender", "capture", "incapacitation", "death"]
RecoveryKind = Literal[
    "observe",
    "communicate",
    "negotiate",
    "escape",
    "rescue",
    "recover_items",
    "rest",
    "resupply",
    "replace",
    "assist",
    "advance",
]


class SetbackRule(Record):
    id: Id
    kind: SetbackKind
    actor_ids: tuple[Id, ...] = Field(min_length=1)
    required_fact_ids: tuple[Id, ...] = ()
    destination_scene_id: Id | None = None
    captor_id: Id | None = None
    custody_owner_id: Id | None = None
    restraints: tuple[str, ...] = ()
    permitted: tuple[RecoveryKind, ...] = (
        "observe",
        "communicate",
        "negotiate",
        "escape",
        "assist",
    )
    consequence_fact_ids: tuple[Id, ...] = ()
    impossible_objective_ids: tuple[Id, ...] = ()
    # Explicit scenario-authored failure/fallback evidence, never capture => defeat.
    failure_fact_id: Id | None = None


class RecoveryOption(Record):
    id: Id
    kind: RecoveryKind
    scene_id: Id
    actor_ids: tuple[Id, ...] = Field(min_length=1)
    target_actor_ids: tuple[Id, ...] = Field(min_length=1)
    required_fact_ids: tuple[Id, ...] = ()
    ticks: int = Field(default=1, ge=1, le=10000)
    check_rule_id: Id | None = None
    supported: bool = True
    success_fact_ids: tuple[Id, ...] = ()
    failure_fact_ids: tuple[Id, ...] = ()
    cost_definition_id: Id | None = None
    cost: int = Field(default=0, ge=0, le=1000000)
    recovery_hp: int = Field(default=0, ge=0, le=100)
    recovery_fp: int = Field(default=0, ge=0, le=100)
    stock_item_id: Id | None = None
    stock_quantity: int = Field(default=1, ge=1, le=1000000)
    replacement: CharacterProposal | None = None
    advancement: CharacterDraft | None = None
    recipient_actor_ids: tuple[Id, ...] = ()
    communication_fact_ids: tuple[Id, ...] = ()


class RecoveryRules(Record):
    id: Id
    version: int = Field(ge=1)
    setbacks: tuple[SetbackRule, ...] = ()
    options: tuple[RecoveryOption, ...] = ()


class Captivity(Record):
    actor_id: Id
    captor_id: Id
    scene_id: Id
    custody_owner_id: Id
    restraints: tuple[str, ...]
    permitted: tuple[RecoveryKind, ...]
    confiscated_item_ids: tuple[Id, ...]
    captured_at: int = Field(ge=0)
    released_at: int | None = None


class SetbackRecord(Record):
    id: Id
    actor_id: Id
    rule_id: Id
    kind: SetbackKind
    at: int = Field(ge=0)


class RecoveryDecision(Record):
    id: Id
    actor_id: Id
    target_actor_id: Id
    option_id: Id
    due: int = Field(ge=0)
    status: Literal["pending", "committed", "failed", "rejected", "adjudication_required"]
    check_json: str | None = None


class ReplacementRecord(Record):
    actor_id: Id
    previous_proposal: CharacterProposal
    at: int


class RecoveryState(Record):
    version: Literal[1] = 1
    setbacks: tuple[SetbackRecord, ...] = ()
    captivity: tuple[Captivity, ...] = ()
    decisions: tuple[RecoveryDecision, ...] = ()
    dead_actor_ids: tuple[Id, ...] = ()
    replacements: tuple[ReplacementRecord, ...] = ()
    impossible_objective_ids: tuple[Id, ...] = ()
