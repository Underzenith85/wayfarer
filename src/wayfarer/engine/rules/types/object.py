"""Opt-in, persisted object facts (Basic Set B380, B483-485)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Record


class ObjectProfile(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    construction: Literal["unliving", "homogenous", "diffuse"]
    hp: int = Field(gt=0)
    dr: int = Field(ge=0)
    ht: int = Field(gt=0)
    high_pain_threshold: bool = Field(default=False, exclude_if=lambda v: not v)
    sentient: bool = Field(default=False, exclude_if=lambda v: not v)
    size_modifier: int | None = Field(default=None, exclude_if=lambda v: v is None)
    repair_skill_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    repair_tools_definition: str | None = Field(default=None, exclude_if=lambda v: v is None)
    repair_parts_definition: str | None = Field(default=None, exclude_if=lambda v: v is None)
    # Six reviewed B485 outcomes, indexed by the recorded d6. None means unusable.
    residual_definitions: tuple[str | None, ...] = Field(default=(), exclude_if=lambda v: not v)
    # B408: ordinary structural cover adds HP/4; thin slabs use DR alone.
    cover_kind: Literal["structural", "thin"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    fragility: tuple[Literal["brittle", "combustible", "flammable", "explosive"], ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    # B485 outcomes may replace the held piece and leave a second catalog-backed
    # piece on the ground. Catalog entries carry the resulting skills, modes,
    # reach, penalties and exact mass.
    broken_weapon_outcomes: tuple[BrokenWeaponOutcome | None, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    reduced_effectiveness_definitions: tuple[str, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    salvage: SalvageProfile | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def residual_table(self) -> Self:
        if self.residual_definitions and len(self.residual_definitions) != 6:
            raise ValueError("Broken weapon outcomes require exactly six entries")
        if self.broken_weapon_outcomes and len(self.broken_weapon_outcomes) != 6:
            raise ValueError("Broken weapon piece outcomes require exactly six entries")
        if len(set(self.fragility)) != len(self.fragility):
            raise ValueError("Fragility traits must be unique")
        return self


class BrokenWeaponOutcome(Record):
    retained_definition_id: str | None = None
    detached_definition_id: str | None = None


class SalvageProfile(Record):
    skill_id: str
    tools_definition_id: str
    recovered_definition_id: str
    seconds: int = Field(gt=0)
    disabled_quantity: int = Field(default=1, ge=0)
    destroyed_quantity: int = Field(default=1, ge=0)


class GroundPosition(Record):
    encounter_id: str = Field(min_length=1, max_length=200)
    geometry: Literal["grid", "hex"]
    x: int
    y: int


class ObjectCondition(Record):
    hp: int
    disabled: bool = False
    destroyed: bool = False
    last_stress_at: int | None = Field(default=None, ge=0)
    residual_roll: int | None = Field(default=None, ge=1, le=6, exclude_if=lambda v: v is None)
    shock: int = Field(default=0, ge=0, le=4, exclude_if=lambda v: not v)
    shock_until: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)
    burning: bool = Field(default=False, exclude_if=lambda v: not v)
    last_burn_at: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)
    reduced_definition_id: str | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def destruction_disables(self) -> Self:
        if self.destroyed and not self.disabled:
            raise ValueError("Destroyed objects must be disabled")
        return self


class ObjectResult(Record):
    command_id: str = Field(min_length=1, max_length=200)
    item_id: str = Field(min_length=1, max_length=200)
    injury: int = Field(default=0, ge=0)
    effective_dr: int = Field(default=0, ge=0)
    checks: tuple[tuple[int, int, int], ...] = ()
    damage_dice: tuple[int, ...] = ()
    ignited: bool = False
    exploded: bool = False
    explosion_dice: int = Field(default=0, ge=0)
    condition: ObjectCondition


def residual_definition(
    profile: ObjectProfile | None, condition: ObjectCondition | None
) -> str | None:
    if (
        profile is None
        or condition is None
        or not condition.disabled
        or condition.destroyed
        or condition.residual_roll is None
        or not (profile.residual_definitions or profile.broken_weapon_outcomes)
    ):
        return None
    if profile.residual_definitions:
        return profile.residual_definitions[condition.residual_roll - 1]
    outcome = profile.broken_weapon_outcomes[condition.residual_roll - 1]
    return outcome.retained_definition_id if outcome else None
