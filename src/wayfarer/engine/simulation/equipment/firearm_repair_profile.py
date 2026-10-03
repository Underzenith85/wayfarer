"""Explicit B483 configured durability for unchanged canonical Small Arms rows."""

from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile, RangedMode
from wayfarer.engine.simulation.equipment.objects import object_hp
from wayfarer.errors import ValidationError

TOOLKIT = "equipment:portable-armoury-tool-kit"


def profile(entry: EquipmentProfile) -> ObjectProfile:
    canonical = next(
        (e for e in BASIC_EQUIPMENT.entries if e.definition_id == entry.definition_id), None
    )
    if canonical is None or entry.model_copy(update={"durability": None}) != canonical:
        raise ValidationError("Small Arms repair requires an unchanged canonical firearm row")
    if not any(
        isinstance(m, RangedMode)
        and m.firearm is not None
        and m.firearm.armoury_skill_id == "skill:armoury-small-arms"
        for m in canonical.modes
    ):
        raise ValidationError("Small Arms repair requires its canonical firearm mode")
    if not isinstance(entry.weight_millipounds, int):
        raise ValidationError("Small Arms repair requires supported integral millipound weight")
    return ObjectProfile(
        construction="unliving",
        hp=object_hp(entry.weight_millipounds, "unliving"),
        dr=4,
        ht=10,
        repair_skill_id="skill:armoury-small-arms",
        repair_tools_definition=TOOLKIT,
        repair_parts_definition="equipment:spare-parts",
    )


def require_profile(entry: EquipmentProfile) -> None:
    if entry.durability != profile(entry):
        raise ValidationError(
            "Small Arms repair profile must retain its source-derived HP, DR and tools"
        )
