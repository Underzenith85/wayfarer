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
from wayfarer.rules.entangle_types import EntangleSpec
from wayfarer.rules.explosion_types import ExplosionSpec
from wayfarer.rules.firearm_types import FirearmSpec
from wayfarer.rules.launcher_types import LauncherSpec
from wayfarer.rules.location_types import HumanLocation
from wayfarer.rules.mount_types import MountSpec
from wayfarer.rules.object_types import ObjectProfile
from wayfarer.rules.readiness_types import ProjectileReadiness
from wayfarer.rules.spray_types import SprayerSpec
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


class RatedStrength(Record):
    """Explicit B270 weapon ST; never inferred from a skill or ammunition name."""

    kind: Literal["bow", "crossbow"]
    st: Positive


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
    reload_protocol: Literal["magazine", "per-round"] = Field(
        default="magazine", exclude_if=lambda v: v == "magazine"
    )
    bulk: int = Field(le=0)
    recoil: Positive = 1
    ammunition_id: Id | None = None
    thrown: bool = False
    catchable: bool = Field(default=False, exclude_if=lambda v: not v)
    blockable: bool = False
    brace_kind: Literal["one-handed", "bipod"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    scope_bonus: Nonnegative = Field(default=0, exclude_if=lambda value: value == 0)
    fixed_power_scope: bool = Field(default=False, exclude_if=lambda value: not value)
    rated_strength: RatedStrength | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    entangle: EntangleSpec | None = Field(default=None, exclude_if=lambda value: value is None)
    mount: MountSpec | None = Field(default=None, exclude_if=lambda value: value is None)
    sprayer: SprayerSpec | None = Field(default=None, exclude_if=lambda value: value is None)
    launcher: LauncherSpec | None = Field(default=None, exclude_if=lambda value: value is None)
    firearm: FirearmSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    readiness: ProjectileReadiness | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.catchable and (not self.thrown or self.hands != 1):
            raise ValueError("Catching requires a one-handed thrown weapon")
        if self.readiness is not None:
            if self.thrown:
                raise ValueError("Projectile readiness cannot bind a thrown weapon")
            if self.readiness.kind in ("bow", "crossbow") and (
                self.rated_strength is None or self.rated_strength.kind != self.readiness.kind
            ):
                raise ValueError("Bow readiness requires matching rated weapon ST")
            if self.readiness.kind == "firearm" and self.firearm is None:
                raise ValueError("Firearm readiness requires pinned firearm facts")
        if (
            self.firearm is not None
            and self.firearm.action not in ("beam", "grenade", "single-use")
            and (
                self.thrown
                or self.blockable
                or self.rated_strength is not None
                or self.damage.basis != "fixed"
                or self.damage.tight_beam
                or self.damage.damage_type not in ("pi-", "pi", "pi+", "pi++")
            )
        ):
            raise ValueError("Malfunctions require an explicit conventional firearm mode")
        if self.firearm is not None:
            action = self.firearm.action
            if action == "beam" and (
                self.thrown or self.damage.basis != "fixed" or self.damage.damage_type != "burn"
            ):
                raise ValueError("Beam requires a fixed burning projectile mode")
            if action == "grenade" and (not self.thrown or self.catchable):
                raise ValueError("Grenades require an uncaught single thrown instance")
            if action == "single-use" and (
                self.thrown or self.shots != 1 or self.rate_of_fire != 1
            ):
                raise ValueError("Single-use launcher requires one loaded shot")
            if self.readiness is not None and action in ("beam", "grenade", "single-use"):
                raise ValueError("Exotic weapons do not use conventional Fast-Draw readiness")
        if self.half_damage_range is not None and self.half_damage_range > self.maximum_range:
            raise ValueError("Half-damage range exceeds maximum range")
        if self.thrown and self.reload_protocol != "magazine":
            raise ValueError("Thrown weapons cannot have a reload protocol")
        if self.thrown:
            if self.ammunition_id is not None or self.shots != 1 or self.rate_of_fire != 1:
                raise ValueError("Thrown mode uses the item itself, once")
        elif self.ammunition_id is None:
            raise ValueError("Projectile weapons require an ammunition reference")
        if self.brace_kind == "one-handed" and self.hands != 1:
            raise ValueError("One-handed bracing requires a one-handed weapon")
        if self.brace_kind == "bipod" and self.hands != 2:
            raise ValueError("Bipod bracing requires a two-handed weapon")
        if self.fixed_power_scope and not self.scope_bonus:
            raise ValueError("A fixed-power scope requires a scope bonus")
        if self.rated_strength is not None and (
            self.thrown
            or self.shots != 1
            or self.rate_of_fire != 1
            or self.hands != 2
            or self.range_basis != "st"
            or self.damage.basis != "thrust"
            or self.reload_protocol != "magazine"
        ):
            raise ValueError("Rated bows require two hands, ST range and single-shot thrust damage")
        if self.rated_strength is not None and self.reload_seconds != (
            2 if self.rated_strength.kind == "bow" else 4
        ):
            raise ValueError("Rated bows require their ordinary two- or four-second reload timing")
        # A binding is thrown once and holds the target; it is not a rapid-fire
        # projectile, and it cannot also be a rated launcher or a firearm.
        # A mounted weapon is served, not thrown or bound by hand.
        if self.mount is not None and (self.thrown or self.entangle is not None):
            raise ValueError("Mounted weapons are neither thrown nor entangling")
        # A launcher assists a throw of the projectile's own item.
        if self.launcher is not None and (
            not self.thrown or self.entangle is not None or self.mount is not None
        ):
            raise ValueError("A launcher assists a thrown weapon")
        # A stream is held on a target; it is not thrown, bound or rapid-fired.
        if self.sprayer is not None and (
            self.thrown
            or self.entangle is not None
            or self.rate_of_fire != 1
            or self.ammunition_id is None
        ):
            raise ValueError("Liquid projector streams are single held discharges")
        if self.entangle is not None and (
            not self.thrown
            or self.rate_of_fire != 1
            or self.shots != 1
            or self.rated_strength is not None
            or self.firearm is not None
        ):
            raise ValueError("Entangling weapons are single thrown bindings")
        return self


WeaponMode = Annotated[MeleeMode | RangedMode, Field(discriminator="kind")]


def require_skill_procedure(profile_id: str, mode: MeleeMode | RangedMode) -> None:
    """Refuse a weapon that claims a ranged combat skill with no bound procedure.

    Skills outside the #344 audit pass through unchanged. A row that this
    repository accounts for but does not execute fails closed here, naming the
    concrete open issue that owns it, so authoring, scenario, character and LLM
    validators cannot turn an accounted-for skill into a mechanic.
    """
    from wayfarer.rules.mundane_skills.ranged import require_mode

    rated = mode.rated_strength if isinstance(mode, RangedMode) else None
    require_mode(
        profile_id,
        mode.skill_id,
        ranged=isinstance(mode, RangedMode),
        thrown=isinstance(mode, RangedMode) and mode.thrown,
        ammunition=isinstance(mode, RangedMode) and mode.ammunition_id is not None,
        rate_of_fire=mode.rate_of_fire if isinstance(mode, RangedMode) else 1,
        recoil=mode.recoil if isinstance(mode, RangedMode) else 1,
        hands=mode.hands,
        tight_beam=mode.damage.tight_beam,
        rated_kind=rated.kind if rated is not None else None,
        entangling=isinstance(mode, RangedMode) and mode.entangle is not None,
        conventional_firearm=isinstance(mode, RangedMode)
        and mode.firearm is not None
        and mode.firearm.action not in ("beam", "grenade"),
        mounted=isinstance(mode, RangedMode) and mode.mount is not None,
        spraying=isinstance(mode, RangedMode) and mode.sprayer is not None,
        launched=isinstance(mode, RangedMode) and mode.launcher is not None,
    )


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
    critical_breakage: Literal["ordinary", "cheap", "resistant"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    parry_quality: Literal["cheap", "good", "fine", "very-fine"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    warhead: ExplosionSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    power_cell_capacity: Positive | None = Field(default=None, exclude_if=lambda v: v is None)
    container_capacity_millipounds: Nonnegative | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def valid_modes(self) -> Self:
        if self.power_cell_capacity is not None and (not self.ammunition or self.warhead):
            raise ValueError("Power cell requires nonexplosive ammunition metadata")
        if self.warhead is not None and not (
            self.ammunition
            or any(
                isinstance(m, RangedMode) and m.firearm and m.firearm.action == "grenade"
                for m in self.modes
            )
        ):
            raise ValueError("Warheads require ammunition or grenade construction")
        if self.parry_quality is not None and (self.durability is None or not self.modes):
            raise ValueError("Parry quality requires a durable weapon")
        if self.critical_breakage is not None and (self.durability is None or not self.modes):
            raise ValueError("Critical breakage requires a durable weapon")
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
            stackable=not bool(
                self.modes
                or self.armor
                or self.shield
                or self.durability
                or self.power_cell_capacity
            ),
            power_cell_capacity=self.power_cell_capacity,
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
            if (
                entry.warhead is not None or entry.power_cell_capacity is not None
            ) and self.profile_id != "gurps-basic-set-4e-2004":
                raise ValueError("Exotic ammunition requires the exact Basic Set profile")
            if entry.durability and entry.durability.residual_definitions:
                for replacement in entry.durability.residual_definitions:
                    if replacement is not None and (
                        replacement not in entries
                        or not entries[replacement].modes
                        or entries[replacement].durability is not None
                    ):
                        raise ValueError(
                            "Residual modes require a pinned non-durable weapon definition"
                        )
            if entry.parry_quality is not None and self.profile_id != "gurps-basic-set-4e-2004":
                raise ValueError("Parry quality requires the exact Basic Set profile")
            if entry.critical_breakage is not None and self.profile_id != "gurps-basic-set-4e-2004":
                raise ValueError("Critical breakage requires the exact Basic Set profile")
            if entry.durability is not None and entry.durability.profile_id != self.profile_id:
                raise ValueError("Object durability requires the exact Basic Set profile")
            for mode in entry.modes:
                require_skill_procedure(self.profile_id, mode)
                if isinstance(mode, RangedMode):
                    cell = entries.get(mode.ammunition_id or "")
                    if (
                        cell
                        and cell.power_cell_capacity
                        and (mode.firearm is None or mode.firearm.action != "beam")
                    ):
                        raise ValueError("Power cells require explicit beam construction")
                if isinstance(mode, RangedMode) and mode.firearm:
                    action = mode.firearm.action
                    ammo = entries.get(mode.ammunition_id or "")
                    if action == "grenade" and entry.warhead is None:
                        raise ValueError("Grenade requires pinned explosive damage")
                    if action == "beam" and (ammo is None or ammo.power_cell_capacity is None):
                        raise ValueError("Beam requires a pinned power-cell capacity")
                    if action != "beam" and ammo and ammo.power_cell_capacity:
                        raise ValueError("Only beam modes may bind power cells")
                if (
                    isinstance(mode, RangedMode)
                    and mode.catchable
                    and self.profile_id != "gurps-basic-set-4e-2004"
                ):
                    raise ValueError("Catching requires the exact Basic Set profile")
                if isinstance(mode, RangedMode) and mode.readiness is not None:
                    if self.profile_id != "gurps-basic-set-4e-2004":
                        raise ValueError(
                            "Projectile readiness requires the exact Basic Set profile"
                        )
                    aid = mode.readiness.cocking_aid_definition_id
                    if aid is not None and aid not in entries:
                        raise ValueError("Cocking aid requires a pinned catalog entry")
                if isinstance(mode, RangedMode) and mode.entangle is not None:
                    if self.profile_id != "gurps-basic-set-4e-2004":
                        raise ValueError("Entangling weapons require the exact Basic Set profile")
                if isinstance(mode, RangedMode) and mode.mount is not None:
                    if self.profile_id != "gurps-basic-set-4e-2004":
                        raise ValueError("Mounted weapons require the exact Basic Set profile")
                if isinstance(mode, RangedMode) and mode.sprayer is not None:
                    if self.profile_id != "gurps-basic-set-4e-2004":
                        raise ValueError("Liquid projectors require the exact Basic Set profile")
                if isinstance(mode, RangedMode) and mode.launcher is not None:
                    if self.profile_id != "gurps-basic-set-4e-2004":
                        raise ValueError("Launchers require the exact Basic Set profile")
                    launcher = entries.get(mode.launcher.launcher_definition_id)
                    if launcher is None or launcher.ammunition or not launcher.slot:
                        raise ValueError("A launcher must be a pinned holdable catalog entry")
                if isinstance(mode, RangedMode) and mode.firearm is not None:
                    if self.profile_id != "gurps-basic-set-4e-2004":
                        raise ValueError("Firearm malfunctions require the exact Basic Set profile")
                    if mode.firearm.technology_level != entry.technology_level:
                        raise ValueError("Firearm and equipment technology levels must agree")
                if isinstance(mode, RangedMode) and mode.rated_strength is not None:
                    if self.profile_id != "gurps-basic-set-4e-2004":
                        raise ValueError("Rated weapon ST requires the exact Basic Set profile")
                    from wayfarer.character.statistics import damage

                    damage(self.profile_id, mode.rated_strength.st)
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
            for mode in entry.modes:
                if isinstance(mode, RangedMode) and mode.readiness is not None:
                    spec = mode.readiness
                    if spec.fast_draw_skill_id is not None:
                        fast_skill = definitions.get(spec.fast_draw_skill_id)
                        if (
                            fast_skill is None
                            or fast_skill.skill is None
                            or fast_skill.skill.specialty is None
                            or fast_skill.skill.specialty.family != "skill:fast-draw"
                            or fast_skill.skill.specialty.name != spec.fast_draw_specialty
                        ):
                            raise ValidationError("Readiness requires an exact Fast-Draw specialty")
                        skills.append(spec.fast_draw_skill_id)
                if isinstance(mode, RangedMode) and mode.firearm is not None:
                    if mode.firearm.armoury_skill_id is not None:
                        skills.append(mode.firearm.armoury_skill_id)
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
