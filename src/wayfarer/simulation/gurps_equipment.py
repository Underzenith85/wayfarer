"""Typed equipment data; combat execution remains with the combat services.

No item instances live here. Inventory weights use thousandths of a pound in
this explicit adapter, never the prototype's unspecified integer units.
"""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from wayfarer.character.statistics import (
    CharacterStatistics,
    Encumbrance,
    encumbered_dodge,
    encumbered_move,
    encumbrance,
)
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind, RulesPackage
from wayfarer.rules.conformance import require_capabilities
from wayfarer.rules.location_types import HumanLocation
from wayfarer.rules.object_types import ObjectProfile
from wayfarer.simulation.resources import EquipmentSpec, Id, Record, ResourceEngine, ResourceState

Nonnegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(ge=1)]
DamageType = Literal["cr", "cut", "imp", "pi-", "pi", "pi+", "pi++", "burn", "cor", "tox", "fat"]
Location = HumanLocation | Literal["arms", "hands", "legs", "feet", "eyes"]


class Provenance(Record):
    source_id: Id
    edition: Literal[
        "August 2004, Rev. 07/12/04",
        "Fourth Edition, first printing (2004)",
        "Fourth Edition, third printing (2008)",
    ]
    pages: tuple[Positive, ...] = Field(min_length=1)
    errata: Id


class Damage(Record):
    basis: Literal["thrust", "swing", "fixed"]
    dice: Positive | None = None
    adds: int = 0
    damage_type: DamageType
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    tight_beam: bool = False

    @model_validator(mode="after")
    def valid_basis(self) -> Self:
        if (self.basis == "fixed") != (self.dice is not None):
            raise ValueError("Only fixed damage supplies a positive d6 count")
        return self


class Parry(Record):
    modifier: int = 0
    unbalanced: bool = False
    fencing: bool = False


class MeleeMode(Record):
    kind: Literal["melee"] = "melee"
    id: Id
    skill_id: Id
    minimum_st: Positive
    hands: Literal[1, 2] = 1
    damage: Damage
    reach: tuple[Nonnegative, ...] = Field(min_length=1)  # 0 is close combat
    parry: Parry | None = None
    ready_after_attack: bool = Field(default=False, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def unique_reach(self) -> Self:
        if tuple(sorted(set(self.reach))) != self.reach:
            raise ValueError("Reach must be unique and ascending")
        return self


class RangedMode(Record):
    kind: Literal["ranged"] = "ranged"
    id: Id
    skill_id: Id
    minimum_st: Positive
    hands: Literal[1, 2] = 1
    damage: Damage
    accuracy: Nonnegative
    range_basis: Literal["yards", "st"]
    half_damage_range: Annotated[Decimal | int, Field(gt=0, allow_inf_nan=False)] | None = None
    maximum_range: Annotated[Decimal | int, Field(gt=0, allow_inf_nan=False)]
    rate_of_fire: Positive = 1
    shots: Positive
    reload_seconds: Nonnegative
    bulk: int = Field(le=0)
    recoil: Positive = 1
    ammunition_id: Id | None = None
    thrown: bool = False
    blockable: bool = False

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.half_damage_range is not None and self.half_damage_range > self.maximum_range:
            raise ValueError("Half-damage range exceeds maximum range")
        if self.thrown:
            if self.ammunition_id is not None or self.shots != 1 or self.rate_of_fire != 1:
                raise ValueError("Thrown mode uses the item itself, once")
        elif self.ammunition_id is None:
            raise ValueError("Projectile weapons require an ammunition reference")
        return self


WeaponMode = Annotated[MeleeMode | RangedMode, Field(discriminator="kind")]


class Armor(Record):
    locations: tuple[Location, ...] = Field(min_length=1)
    dr: Nonnegative
    flexible: bool = False

    @model_validator(mode="after")
    def unique_locations(self) -> Self:
        if len(set(self.locations)) != len(self.locations):
            raise ValueError("Duplicate armor location")
        return self


class Shield(Record):
    skill_id: Id
    defense_bonus: Positive
    can_block: bool = True


class EquipmentProfile(Record):
    definition_id: Id
    provenance: Provenance
    weight_millipounds: Nonnegative
    price: Nonnegative
    technology_level: Nonnegative
    slot: Id | None = None
    ammunition: bool = False
    modes: tuple[WeaponMode, ...] = ()
    armor: Armor | None = None
    shield: Shield | None = None
    unsupported_mechanics: tuple[Id, ...] = Field(default=(), exclude_if=lambda v: not v)
    durability: ObjectProfile | None = Field(default=None, exclude_if=lambda v: v is None)
    container_capacity_millipounds: Nonnegative | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def valid_modes(self) -> Self:
        if len({mode.id for mode in self.modes}) != len(self.modes):
            raise ValueError("Duplicate weapon mode ID")
        if (self.modes or self.armor or self.shield) and self.slot is None:
            raise ValueError("Usable equipment requires an inventory slot")
        if self.ammunition and (self.modes or self.armor or self.shield):
            raise ValueError("Ammunition cannot also be wearable or a weapon")
        return self

    def inventory_spec(self) -> EquipmentSpec:
        if self.unsupported_mechanics:
            raise ValidationError(
                "Equipment has unsupported mechanics: " + ", ".join(self.unsupported_mechanics)
            )
        return EquipmentSpec(
            definition_id=self.definition_id,
            unit_weight=self.weight_millipounds,
            technology_level=self.technology_level,
            stackable=not bool(self.modes or self.armor or self.shield or self.durability),
            durability=self.durability,
            container_capacity=self.container_capacity_millipounds,
            slot=self.slot,
            ammunition=self.ammunition,
        )


class EquipmentCatalog(Record):
    profile_id: Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"]
    entries: tuple[EquipmentProfile, ...]

    @model_validator(mode="after")
    def valid_entries(self) -> Self:
        entries = {entry.definition_id: entry for entry in self.entries}
        if len(entries) != len(self.entries):
            raise ValueError("Duplicate equipment definition")
        for entry in self.entries:
            if entry.durability is not None and entry.durability.profile_id != self.profile_id:
                raise ValueError("Object durability requires the exact Basic Set profile")
            for mode in entry.modes:
                if isinstance(mode, RangedMode) and mode.ammunition_id is not None:
                    ammo = entries.get(mode.ammunition_id)
                    if ammo is None or not ammo.ammunition:
                        raise ValueError("Missing or non-ammunition reference")
        return self

    def bind(self, packages: tuple[RulesPackage, ...]) -> tuple[EquipmentSpec, ...]:
        """Resolve source, equipment and skill references against pinned packages.

        Skills are references, not equip prerequisites: untrained use and defaults
        are the skill resolver's responsibility. ResourceEngine enforces policy.
        """
        definitions = {d.id: d for p in packages for d in p.definitions}
        if len(definitions) != sum(len(p.definitions) for p in packages):
            raise ValidationError("Ambiguous equipment catalog references")
        for entry in self.entries:
            if entry.unsupported_mechanics:
                raise ValidationError(
                    "Equipment has unsupported mechanics: " + ", ".join(entry.unsupported_mechanics)
                )
            definition = definitions.get(entry.definition_id)
            if definition is None or definition.kind is not DefinitionKind.EQUIPMENT:
                raise ValidationError("Missing equipment definition")
            if definition.source_id != entry.provenance.source_id or not any(
                definition in p.definitions
                and entry.provenance.source_id in {s.id for s in p.sources}
                for p in packages
            ):
                raise ValidationError("Equipment source mismatch")
            skills = [mode.skill_id for mode in entry.modes]
            if entry.shield is not None:
                skills.append(entry.shield.skill_id)
            for skill in skills:
                if skill not in definitions or definitions[skill].kind is not DefinitionKind.SKILL:
                    raise ValidationError("Missing weapon/shield skill reference")
        return tuple(entry.inventory_spec() for entry in self.entries)


class Load(Record):
    weight_pounds: Decimal
    level: Encumbrance | None
    move: int | None
    dodge: int | None


def inventory_load(
    catalog: EquipmentCatalog,
    engine: ResourceEngine,
    state: ResourceState,
    actor_id: str,
    statistics: CharacterStatistics,
) -> Load:
    """Recompute from authoritative ownership after every receipt; no cached items."""
    if statistics.profile_id != catalog.profile_id:
        raise ValidationError("Equipment/statistics profile mismatch")
    require_capabilities(catalog.profile_id, ("gurps.character.secondary_characteristics",))
    engine.validate(state)
    if actor_id not in {owner.actor_id for owner in state.owners}:
        raise ValidationError("Unknown inventory owner")
    expected = {entry.definition_id: entry.inventory_spec() for entry in catalog.entries}
    if engine.specs != expected:
        raise ValidationError("Inventory must use the exact profile specs and millipound units")
    pounds = Decimal(engine.carried_weight(state, actor_id)) / 1000
    level = encumbrance(catalog.profile_id, statistics.basic_lift, pounds)
    return Load(
        weight_pounds=pounds,
        level=level,
        move=None
        if level is None
        else encumbered_move(catalog.profile_id, statistics.basic_move, level),
        dodge=None if level is None else encumbered_dodge(statistics.dodge, level),
    )


# Declared sample, not an exhaustive equipment catalog. Numeric facts only.
LITE_SOURCE = Provenance(
    source_id="sjg:gurps-lite-4e-2004",
    edition="August 2004, Rev. 07/12/04",
    pages=(19, 20),
    errata="No separate errata overlay selected",
)
LITE_EQUIPMENT = EquipmentCatalog(
    profile_id="gurps-lite-4e-2004",
    entries=(
        EquipmentProfile(
            definition_id="equipment:broadsword",
            provenance=LITE_SOURCE,
            weight_millipounds=3000,
            price=500,
            technology_level=2,
            slot="hand",
            modes=(
                MeleeMode(
                    id="swing",
                    skill_id="skill:broadsword",
                    minimum_st=10,
                    damage=Damage(basis="swing", adds=1, damage_type="cut"),
                    reach=(1,),
                    parry=Parry(),
                ),
                MeleeMode(
                    id="thrust",
                    skill_id="skill:broadsword",
                    minimum_st=10,
                    damage=Damage(basis="thrust", adds=1, damage_type="cr"),
                    reach=(1,),
                    parry=Parry(),
                ),
            ),
        ),
        EquipmentProfile(
            definition_id="equipment:leather-armor",
            provenance=LITE_SOURCE,
            weight_millipounds=10000,
            price=100,
            technology_level=1,
            slot="body",
            armor=Armor(locations=("torso", "arms", "legs"), dr=2, flexible=True),
        ),
    ),
)
