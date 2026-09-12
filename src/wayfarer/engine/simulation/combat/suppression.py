"""Spraying and suppression zones, and the attacks a zone owes on entry."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.models import Id, Record


class SprayTarget(Record):
    """A declared additional target in one B409 spraying-fire sweep."""

    target_id: Id
    shots: int = Field(ge=1)
    hit_location: HitLocation | None = None


class PendingSprayTarget(SprayTarget):
    """Server-derived traversal cost and control penalty for a queued target."""

    recoil_penalty: int = Field(ge=1)
    traversal_shots: int = Field(ge=0)


class SuppressionZone(Record):
    """One declared two-yard B409 suppression zone on an exact hex map."""

    center: Hex
    shots: int = Field(ge=1)


class ActiveSuppressionZone(SuppressionZone):
    """Paid suppression fire that remains live until the firer's next turn."""

    id: Id
    attacker_id: Id
    weapon_id: Id
    mode_id: str
    origin: Hex
    remaining_hits: int = Field(ge=0)
    aim_bonus: int = Field(ge=0)
    skill_cap: Literal[6, 8]
    attacked_actor_ids: tuple[Id, ...] = ()


class PendingSuppressionAttack(Record):
    """A deterministic automatic attack queued when movement enters a zone."""

    zone_id: Id
    attacker_id: Id
    weapon_id: Id
    mode_id: str
    shots: int = Field(ge=1)
    remaining_hits: int = Field(ge=1)
    aim_bonus: int = Field(ge=0)
    skill_cap: Literal[6, 8]
