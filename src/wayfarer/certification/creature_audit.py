"""Bounded item ledger for the #521 creatures and #522 swarm examples."""

from dataclasses import dataclass

from wayfarer.engine.rules.creatures import representative_creatures, representative_swarms


@dataclass(frozen=True, slots=True)
class CreatureInventoryRow:
    id: str
    reference: str
    owner: int = 521
    implementation: str = "implemented"
    scope: str = "creature-catalog"
    required_profiles: tuple[str, ...] = ("gurps-basic-set-4e-2004",)
    source_review: str = "reviewed"
    blockers: tuple[int, ...] = ()


def inventory() -> tuple[CreatureInventoryRow, ...]:
    """Return every claimed row; absence is intentional, not a bestiary claim."""
    creatures = tuple(
        CreatureInventoryRow(template.id, template.reference)
        for template in representative_creatures()
    )
    swarms = tuple(
        CreatureInventoryRow(
            spec.id,
            spec.reference,
            owner=522,
            scope="creature-combat",
        )
        for spec in representative_swarms()
    )
    return creatures + swarms
