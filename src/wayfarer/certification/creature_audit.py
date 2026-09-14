"""Bounded item ledger for the #521 creatures and #522 swarm examples."""

from dataclasses import dataclass
from typing import Final, Literal

from wayfarer.engine.rules.creatures import representative_creatures, representative_swarms


@dataclass(frozen=True, slots=True)
class CreatureInventoryRow:
    id: str
    reference: str
    owner: int = 521
    implementation: Literal["implemented", "partial"] = "implemented"
    scope: str = "creature-catalog"
    required_profiles: tuple[str, ...] = ("gurps-basic-set-4e-2004",)
    source_review: str = "reviewed"
    blockers: tuple[int, ...] = ()
    gaps: tuple[str, ...] = ()
    evidence: tuple[str, ...] = (
        "tests/fixtures/gurps/residual-creatures.json",
        "tests/test_creatures.py",
        "tests/test_creature_combat.py",
    )


CREATURE_GAPS: Final[dict[str, tuple[str, ...]]] = {}
SWARM_GAPS: Final[dict[str, tuple[str, ...]]] = {}


def _row(
    identifier: str,
    reference: str,
    *,
    owner: int,
    scope: Literal["creature-catalog", "creature-combat"],
    gaps: tuple[str, ...],
) -> CreatureInventoryRow:
    return CreatureInventoryRow(
        identifier,
        reference,
        owner=owner,
        implementation="partial" if gaps else "implemented",
        scope=scope,
        blockers=(owner,) if gaps else (),
        gaps=gaps,
    )


def inventory() -> tuple[CreatureInventoryRow, ...]:
    """Return each claimed row with source-reviewed implementation gaps."""
    creatures = tuple(
        _row(
            template.id,
            template.reference,
            owner=521,
            scope="creature-catalog",
            gaps=CREATURE_GAPS.get(template.id, ()),
        )
        for template in representative_creatures()
    )
    swarms = tuple(
        _row(
            spec.id,
            spec.reference,
            owner=522,
            scope="creature-combat",
            gaps=SWARM_GAPS.get(spec.id, ()),
        )
        for spec in representative_swarms()
    )
    return creatures + swarms
