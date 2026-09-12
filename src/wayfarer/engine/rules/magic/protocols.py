"""Shared Basic Set magic protocols used by spell-family bindings.

The functions here are deliberately spell-agnostic.  A concrete spell still has
to opt into the appropriate class and provide its own effect; these helpers only
implement the common B238-242/B481-482 arithmetic and fail-closed boundaries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

ManaLevel = Literal["none", "low", "normal", "high", "very-high"]
SpellClass = Literal[
    "regular",
    "area",
    "blocking",
    "information",
    "melee",
    "missile",
    "special",
]
MagicTradition = Literal["standard", "clerical", "ritual"]


class RitualRequirements(Record):
    """Physical ritual obligations derived from base spell skill (B237)."""

    speech: Literal["firm", "quiet", "brief", "none"]
    gesture: Literal["full-body", "one-hand", "small", "none"]
    time_multiplier: int = Field(ge=1)


class BlockingCast(Record):
    """Instant defensive cast; concrete spells supply the resulting defense."""

    effective_skill: int = Field(ge=1)
    energy_cost: int = Field(ge=0)


def blocking_cast(
    skill: int,
    listed_cost: int,
    *,
    active_spell_penalty: int = 0,
    already_cast_this_turn: bool = False,
    defends_critical_hit: bool = False,
) -> BlockingCast:
    if type(skill) is not int or skill < 1 or type(listed_cost) is not int or listed_cost < 0:
        raise ValidationError("Blocking spell skill and cost are invalid")
    if already_cast_this_turn:
        raise ValidationError("Only one Blocking spell is allowed per turn")
    if defends_critical_hit:
        raise ValidationError("A Blocking spell cannot defend against a critical hit")
    effective = skill - active_spell_penalty
    if effective < 1:
        raise ValidationError("Effective Blocking spell skill is below one")
    # High skill never reduces the energy cost of a Blocking spell.
    return BlockingCast(effective_skill=effective, energy_cost=listed_cost)


def ritual_requirements(skill: int) -> RitualRequirements:
    if type(skill) is not int or skill < 1:
        raise ValidationError("Spell ritual skill must be a positive integer")
    if skill <= 9:
        return RitualRequirements(speech="firm", gesture="full-body", time_multiplier=2)
    if skill <= 14:
        return RitualRequirements(speech="quiet", gesture="one-hand", time_multiplier=1)
    if skill <= 19:
        return RitualRequirements(speech="brief", gesture="small", time_multiplier=1)
    return RitualRequirements(speech="none", gesture="none", time_multiplier=1)


class CeremonialContribution(Record):
    """A trusted contribution after the adapter verifies build and allegiance."""

    actor_id: Id
    fp: int = Field(default=0, ge=0)
    hp: int = Field(default=0, ge=0)
    role: Literal["leader", "mage", "nonmage", "spectator"]

    @property
    def energy(self) -> int:
        return self.fp + self.hp

    @model_validator(mode="after")
    def bounded(self) -> CeremonialContribution:
        if self.role in ("nonmage", "spectator") and self.energy > (
            3 if self.role == "nonmage" else 1
        ):
            raise ValueError("Ceremonial contribution exceeds the participant limit")
        return self


class CeremonialPlan(Record):
    leader_id: Id
    contributions: tuple[CeremonialContribution, ...] = Field(min_length=2)
    opposing_spectators: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def valid_group(self) -> CeremonialPlan:
        ids = tuple(c.actor_id for c in self.contributions)
        if (
            ids.count(self.leader_id) != 1
            or len(set(ids)) != len(ids)
            or len(set(self.opposing_spectators)) != len(self.opposing_spectators)
            or set(ids) & set(self.opposing_spectators)
            or next(c for c in self.contributions if c.actor_id == self.leader_id).role != "leader"
        ):
            raise ValueError("Ceremonial participants and opposition must be distinct")
        if sum(c.role == "spectator" for c in self.contributions) > 100:
            raise ValueError("At most 100 supporting spectators contribute energy")
        if len(self.opposing_spectators) > 20:
            raise ValueError("Opposition is capped at the -100 energy boundary")
        return self

    @property
    def available_energy(self) -> int:
        return max(0, sum(c.energy for c in self.contributions) - 5 * len(self.opposing_spectators))


def ceremonial_skill_bonus(required_energy: int, available_energy: int) -> int:
    """Return the stepped bonus for energy beyond the spell's required cost."""
    if type(required_energy) is not int or required_energy < 1:
        raise ValidationError("Ceremonial magic requires a positive energy cost")
    if type(available_energy) is not int or available_energy < 0:
        raise ValidationError("Available ceremonial energy cannot be negative")
    extra = max(0, available_energy - required_energy)
    if extra < required_energy:
        return min(3, extra * 5 // required_energy)
    return 4 + (extra // required_energy - 1)


class AreaSelection(Record):
    """Selected cells within a paid circular area; height is four yards by default."""

    center: tuple[int, int]
    cells: tuple[tuple[int, int], ...] = ()
    height_yards: int = Field(default=4, ge=1, le=100)

    @model_validator(mode="after")
    def unique_cells(self) -> AreaSelection:
        if len(set(self.cells)) != len(self.cells):
            raise ValueError("Area spell cells must be unique")
        return self


def square_area(selection: AreaSelection, radius: int) -> frozenset[tuple[int, int]]:
    """Validate an explicitly selected square-grid subset against the paid radius."""
    if type(radius) is not int or radius < 1:
        raise ValidationError("Area radius must be positive")
    cx, cy = selection.center
    full = frozenset(
        (x, y)
        for x in range(cx - radius + 1, cx + radius)
        for y in range(cy - radius + 1, cy + radius)
        if max(abs(x - cx), abs(y - cy)) < radius
    )
    chosen = frozenset(selection.cells) if selection.cells else full
    if not chosen or not chosen <= full or selection.center not in chosen:
        raise ValidationError("Selected area must include its center and stay inside the radius")
    return chosen


class MeleeSpellCharge(Record):
    """A successful Melee spell waiting in the caster's hand or magic staff."""

    cast_id: Id
    actor_id: Id
    carrier_item_id: Id | None = None
    held: bool = True


def ready_melee_spell(
    cast_id: Id,
    actor_id: Id,
    *,
    free_hand: bool,
    carrier_item_id: Id | None = None,
) -> MeleeSpellCharge:
    if not free_hand and carrier_item_id is None:
        raise ValidationError("Melee spell requires a free hand or magic staff")
    return MeleeSpellCharge(cast_id=cast_id, actor_id=actor_id, carrier_item_id=carrier_item_id)


def hex_area(selection: AreaSelection, radius: int) -> frozenset[tuple[int, int]]:
    """Validate an explicitly selected axial-hex subset against the paid radius."""
    if type(radius) is not int or radius < 1:
        raise ValidationError("Area radius must be positive")
    cq, cr = selection.center
    full = frozenset(
        (q, r)
        for q in range(cq - radius + 1, cq + radius)
        for r in range(cr - radius + 1, cr + radius)
        if max(abs(q - cq), abs(r - cr), abs((q - cq) + (r - cr))) < radius
    )
    chosen = frozenset(selection.cells) if selection.cells else full
    if not chosen or not chosen <= full or selection.center not in chosen:
        raise ValidationError("Selected area must include its center and stay inside the radius")
    return chosen


class MagicItemBinding(Record):
    """Campaign binding for one spell carried by one concrete magic item."""

    id: Id
    item_id: Id
    spell_id: Id
    power: int = Field(ge=1, le=100)
    power_reduction: int = Field(default=0, ge=0, le=100)
    requires_magery: bool = False
    always_on: bool = False


class MagicItemInstance(MagicItemBinding):
    """Completed, provenance-bearing enchantment attached to a concrete item."""

    project_id: Id
    recipe_id: Id
    effect_id: Id
    activation: Literal["cast", "always-on"] = "cast"
    runtime_family: str | None = None
    owner_id: Id
    created_at: int = Field(ge=0)
    method: Literal["quick-and-dirty", "slow-and-sure"]
    maximum_charges: int | None = Field(default=None, ge=1)
    charges: int | None = Field(default=None, ge=0)
    maintenance_energy: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def charge_bounds(self) -> MagicItemInstance:
        if (self.maximum_charges is None) != (self.charges is None):
            raise ValueError("Magic-item charges require an explicit maximum")
        if (
            self.charges is not None
            and self.maximum_charges is not None
            and self.charges > self.maximum_charges
        ):
            raise ValueError("Magic-item charges exceed their maximum")
        if self.always_on != (self.activation == "always-on"):
            raise ValueError("Magic-item activation and always-on binding disagree")
        return self


def effective_item_power(power: int, mana: ManaLevel) -> int | None:
    if type(power) is not int or power < 1:
        raise ValidationError("Magic-item Power must be positive")
    if mana == "none":
        return None
    return power - 5 if mana == "low" else power


def item_energy_cost(cost: int, reduction: int, mana: ManaLevel) -> int:
    if type(cost) is not int or cost < 0 or type(reduction) is not int or reduction < 0:
        raise ValidationError("Magic-item energy values must be nonnegative integers")
    adjusted = (
        reduction // 2
        if mana == "low"
        else reduction * 2
        if mana in ("high", "very-high")
        else reduction
    )
    return max(0, cost - adjusted)


class InformationAttempt(Record):
    caster_id: Id
    spell_id: Id
    subject_id: Id
    day: int = Field(ge=0)


def information_attempt_allowed(
    attempts: tuple[InformationAttempt, ...], candidate: InformationAttempt
) -> bool:
    """Information spells permit one attempt per caster/group and subject per day."""
    return not any(
        (a.caster_id, a.spell_id, a.subject_id, a.day)
        == (candidate.caster_id, candidate.spell_id, candidate.subject_id, candidate.day)
        for a in attempts
    )


def validate_tradition(
    tradition: MagicTradition, *, enabled_optional_rules: frozenset[str]
) -> None:
    if tradition != "standard" and "magic." + tradition not in enabled_optional_rules:
        raise ValidationError(f"{tradition.title()} magic is not selected for this campaign")
