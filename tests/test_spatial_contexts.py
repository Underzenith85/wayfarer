"""Persisted ownership and migration for combat spatial contexts (#323)."""

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    DistanceSpatialFact,
    HexSpatialContext,
    SpatialProvenance,
    SquareSpatialContext,
    VisibilitySpatialFact,
)


def provenance(source_id: str = "opening") -> SpatialProvenance:
    return SpatialProvenance(
        source="scenario",
        source_id=source_id,
        declared_by="system",
        declared_revision=0,
    )


def combatants() -> tuple[Combatant, Combatant]:
    return (
        Combatant(
            actor_id="a",
            initiative=12,
            position=GridPoint(x=0, y=0),
            facing="east",
            reach=1,
            movement_allowance=5,
        ),
        Combatant(
            actor_id="b",
            initiative=10,
            position=GridPoint(x=1, y=0),
            facing="west",
            reach=1,
            movement_allowance=5,
        ),
    )


def test_legacy_square_snapshot_normalizes_to_one_persisted_owner() -> None:
    encounter = Encounter.model_validate(
        {
            "id": "fight",
            "battlefield_id": "yard",
            "participants": combatants(),
            "turn_order": ("a", "b"),
        }
    )
    assert isinstance(encounter.spatial, SquareSpatialContext)
    encoded = encounter.model_dump(mode="json")
    assert encoded["spatial_context"] == {
        "kind": "square",
        "battlefield_id": "yard",
        "placements": [
            {"actor_id": "a", "position": {"x": 0, "y": 0}, "facing": "east"},
            {"actor_id": "b", "position": {"x": 1, "y": 0}, "facing": "west"},
        ],
    }
    assert "battlefield_id" not in encoded and "spatial_kind" not in encoded
    assert all("position" not in row and "facing" not in row for row in encoded["participants"])

    restored = Encounter.model_validate_json(encounter.model_dump_json())
    assert restored == encounter
    assert restored.participants[0].position == GridPoint(x=0, y=0)
    assert restored.participants[0].facing == "east"


def test_square_and_hex_contexts_reject_mixed_coordinates() -> None:
    with pytest.raises(SchemaError):
        SquareSpatialContext.model_validate(
            {
                "kind": "square",
                "battlefield_id": "yard",
                "placements": (
                    {"actor_id": "a", "position": {"q": 0, "r": 0}, "facing": "north"},
                    {"actor_id": "b", "position": {"x": 1, "y": 0}, "facing": "west"},
                ),
            }
        )
    with pytest.raises(SchemaError):
        HexSpatialContext.model_validate(
            {
                "kind": "hex",
                "battlefield_id": "hex-yard",
                "placements": (
                    {"actor_id": "a", "position": {"q": 0, "r": 0}, "facing": 0},
                    {"actor_id": "b", "position": {"x": 1, "y": 0}, "facing": "west"},
                ),
            }
        )


def test_basic_context_is_bounded_authoritative_facts_without_a_dummy_map() -> None:
    context = BasicSpatialContext(
        facts=(
            DistanceSpatialFact(subject_id="a", object_id="b", yards=3, provenance=provenance()),
            VisibilitySpatialFact(
                subject_id="a", object_id="b", visible=True, provenance=provenance("sight")
            ),
        )
    )
    encounter = Encounter(
        id="mapless",
        spatial_context=context,
        participants=tuple(p.model_copy(update={"position": None}) for p in combatants()),
        turn_order=("a", "b"),
    )
    encoded = encounter.model_dump(mode="json")
    assert encoded["spatial_context"]["kind"] == "basic"
    assert "battlefield_id" not in encoded["spatial_context"]
    assert CombatRules(id="mapless-rules", version=1).battlefields == ()

    duplicate = context.facts + (
        DistanceSpatialFact(
            subject_id="a", object_id="b", yards=4, provenance=provenance("conflict")
        ),
    )
    with pytest.raises(SchemaError, match="one authoritative value"):
        BasicSpatialContext(facts=duplicate)


def test_spatial_fact_lifetime_cannot_run_backwards() -> None:
    with pytest.raises(SchemaError, match="before it is declared"):
        SpatialProvenance(
            source="gm-adjudication",
            source_id="ruling-1",
            declared_by="gm",
            declared_revision=4,
            invalidated_revision=3,
        )
