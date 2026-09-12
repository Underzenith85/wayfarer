"""The entity kernel: the ``Record`` base class and the vocabulary the engine emits.

Entities are frozen, strict, closed records. Every state transition returns a new
record; verbs live in engines and functions, never on the entity itself.
Runtime validation remains at the service boundary.

The typed dictionaries here are engine vocabulary that application payloads
embed: a character draft and its verdict, a demo roll, and the rules pin a
campaign carries. The payloads themselves (the campaign envelope, receipts and
turn results) are :mod:`wayfarer.contracts`, which the engine never imports.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    """Immutable entity contract shared by rules, character, simulation and orchestration."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


Id = Annotated[str, Field(min_length=1, max_length=200)]
Count = Annotated[int, Field(ge=1, le=1000000)]
Tick = Annotated[int, Field(ge=0)]


class Character(TypedDict):
    name: str
    concept: str
    attributes: dict[str, int]
    skills: dict[str, int]
    traits: list[str]


class ValidationResult(TypedDict):
    valid: bool
    errors: list[str]
    spent: int
    remaining: int
    levels: dict[str, int]


class Roll(TypedDict):
    dice: list[int]
    total: int
    target: int
    success: bool
    critical: Literal["success", "failure"] | None


class RulesPackagePin(TypedDict):
    id: str
    version: str
    digest: str


class RulesReference(TypedDict):
    edition: str
    packages: list[RulesPackagePin]
    policy_id: str
    policy_version: int
