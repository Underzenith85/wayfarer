"""Where combatants are: placements, Basic spatial facts, and the three contexts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.vocabulary import Facing
from wayfarer.engine.simulation.hex_geometry import Hex, HexFacing
from wayfarer.models import Id, Record


class Placement(Record):
    actor_id: Id
    position: GridPoint | Hex
    hex_facing: HexFacing | None = None
    facing: Facing = "north"


class SquareActorPlacement(Record):
    actor_id: Id
    position: GridPoint
    facing: Facing = "north"


class HexActorPlacement(Record):
    actor_id: Id
    position: Hex
    facing: HexFacing


class SpatialProvenance(Record):
    """Trusted origin and lifetime for an authoritative mapless assertion."""

    source: Literal["scenario", "gm-adjudication", "engine-derived"]
    source_id: Id
    declared_by: Id
    declared_revision: int = Field(ge=0)
    invalidated_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_lifetime(self) -> SpatialProvenance:
        if (
            self.invalidated_revision is not None
            and self.invalidated_revision < self.declared_revision
        ):
            raise ValueError("Spatial fact cannot be invalidated before it is declared")
        return self


class DistanceSpatialFact(Record):
    kind: Literal["distance"] = "distance"
    subject_id: Id
    object_id: Id
    yards: float = Field(ge=0, le=10000, allow_inf_nan=False)
    provenance: SpatialProvenance


class ReachSpatialFact(Record):
    kind: Literal["reach"] = "reach"
    subject_id: Id
    object_id: Id
    relation: Literal["close", "reachable", "separated"]
    provenance: SpatialProvenance


class VisibilitySpatialFact(Record):
    kind: Literal["visibility"] = "visibility"
    subject_id: Id
    object_id: Id
    visible: bool
    provenance: SpatialProvenance


class CoverSpatialFact(Record):
    kind: Literal["cover"] = "cover"
    subject_id: Id
    object_id: Id
    cover: Literal["none", "partial", "full"]
    provenance: SpatialProvenance


class ObstacleSpatialFact(Record):
    kind: Literal["obstacle"] = "obstacle"
    subject_id: Id
    object_id: Id
    blocked: bool
    provenance: SpatialProvenance


class RetreatSpatialFact(Record):
    kind: Literal["retreat"] = "retreat"
    subject_id: Id
    object_id: Id
    feasible: bool
    provenance: SpatialProvenance


BasicSpatialFact = Annotated[
    DistanceSpatialFact
    | ReachSpatialFact
    | VisibilitySpatialFact
    | CoverSpatialFact
    | ObstacleSpatialFact
    | RetreatSpatialFact,
    Field(discriminator="kind"),
]


class BasicSpatialContext(Record):
    kind: Literal["basic"] = "basic"
    facts: tuple[BasicSpatialFact, ...] = Field(default=(), max_length=10000)

    @model_validator(mode="after")
    def validate_facts(self) -> BasicSpatialContext:
        active = tuple(f for f in self.facts if f.provenance.invalidated_revision is None)
        keys = tuple(
            (
                f.kind,
                min(f.subject_id, f.object_id) if f.kind == "distance" else f.subject_id,
                max(f.subject_id, f.object_id) if f.kind == "distance" else f.object_id,
            )
            for f in active
        )
        if len(set(keys)) != len(keys):
            raise ValueError("Basic spatial facts require one authoritative value per pair")
        histories: dict[tuple[str, str, str], list[BasicSpatialFact]] = {}
        for fact in self.facts:
            key = (
                fact.kind,
                min(fact.subject_id, fact.object_id)
                if fact.kind == "distance"
                else fact.subject_id,
                max(fact.subject_id, fact.object_id) if fact.kind == "distance" else fact.object_id,
            )
            histories.setdefault(key, []).append(fact)
        for history in histories.values():
            ordered = sorted(history, key=lambda f: f.provenance.declared_revision)
            if any(
                left.provenance.invalidated_revision is None
                or left.provenance.invalidated_revision > right.provenance.declared_revision
                for left, right in zip(ordered, ordered[1:], strict=False)
            ):
                raise ValueError("Basic spatial fact lifetimes cannot overlap")
        return self

    def active(self, kind: str, subject_id: str, object_id: str) -> BasicSpatialFact | None:
        return next(
            (
                fact
                for fact in reversed(self.facts)
                if fact.kind == kind
                and fact.provenance.invalidated_revision is None
                and (
                    (fact.subject_id, fact.object_id) == (subject_id, object_id)
                    or kind == "distance"
                    and (fact.subject_id, fact.object_id) == (object_id, subject_id)
                )
            ),
            None,
        )


class SquareSpatialContext(Record):
    kind: Literal["square"] = "square"
    battlefield_id: Id
    placements: tuple[SquareActorPlacement, ...] = Field(min_length=2, max_length=100)


class HexSpatialContext(Record):
    kind: Literal["hex"] = "hex"
    battlefield_id: Id
    placements: tuple[HexActorPlacement, ...] = Field(min_length=1, max_length=100)


SpatialContext = Annotated[
    BasicSpatialContext | SquareSpatialContext | HexSpatialContext,
    Field(discriminator="kind"),
]
