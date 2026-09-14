"""Authoritative cross-species physiology modifiers (Characters B181).

Species identity, anatomy, origins, and pair-specific similarity are trusted
campaign facts.  A command may select one approved specialty to try, but it
cannot describe a relationship or supply a numeric modifier.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, RandomSource
from wayfarer.engine.rules.gurps_checks import replay_success, success_roll
from wayfarer.engine.rules.skills.mundane.procedures import Situation
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.character.compiler import ValidatedBuild

PROFILE: Final = "gurps-basic-set-4e-2004"
REFERENCE: Final = "B181"
AFFECTED_SKILLS: Final = frozenset(
    {
        "skill:body-language",
        "skill:diagnosis",
        "skill:first-aid",
        "skill:physician",
        "skill:pressure-points",
        "skill:pressure-secrets",
        "skill:surgery",
    }
)


class PhysiologyBand(StrEnum):
    SAME_SPECIES = "same-species"
    SIMILAR_SPECIES = "similar-species"
    VERY_DIFFERENT_SPECIES = "very-different-species"
    UTTERLY_ALIEN = "utterly-alien"
    MACHINE = "machine"


class SpeciesPhysiology(Record):
    id: Id
    world_id: Id
    anatomy: Literal["human", "creature", "swarm"]
    common_animal: bool = False
    machine: bool = False

    @model_validator(mode="after")
    def coherent_kind(self) -> SpeciesPhysiology:
        if self.machine and self.common_animal:
            raise ValueError("A Machine cannot be a common animal")
        return self


class ActorPhysiology(Record):
    actor_id: Id
    species_id: Id


class SpeciesRelationship(Record):
    left_species_id: Id
    right_species_id: Id
    band: Literal[PhysiologyBand.SIMILAR_SPECIES, PhysiologyBand.UTTERLY_ALIEN]
    penalty: int = Field(le=-2, ge=-20)

    @model_validator(mode="after")
    def exact_band_boundary(self) -> SpeciesRelationship:
        if self.left_species_id == self.right_species_id:
            raise ValueError("A physiology relationship requires two species")
        if self.band is PhysiologyBand.SIMILAR_SPECIES and self.penalty not in (-2, -3, -4):
            raise ValueError("Similar-species physiology penalty must be -2 through -4")
        if self.band is PhysiologyBand.UTTERLY_ALIEN and self.penalty > -6:
            raise ValueError("Utterly alien physiology penalty must be -6 or worse")
        return self


class PhysiologySpecialty(Record):
    definition_id: Id
    species_id: Id


class PhysiologyWorld(Record):
    """Pinned campaign physiology facts, separate from player-authored narration."""

    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    species: tuple[SpeciesPhysiology, ...]
    actors: tuple[ActorPhysiology, ...]
    relationships: tuple[SpeciesRelationship, ...] = ()
    specialties: tuple[PhysiologySpecialty, ...] = ()
    biology_skill_ids: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def references_are_exact(self) -> PhysiologyWorld:
        species_ids = {value.id for value in self.species}
        actor_ids = {value.actor_id for value in self.actors}
        if len(species_ids) != len(self.species) or len(actor_ids) != len(self.actors):
            raise ValueError("Duplicate physiology species or actor assignment")
        if any(value.species_id not in species_ids for value in self.actors):
            raise ValueError("Physiology actor names an unknown species")
        if any(value.species_id not in species_ids for value in self.specialties):
            raise ValueError("Physiology specialty names an unknown species")
        if len({value.definition_id for value in self.specialties}) != len(self.specialties):
            raise ValueError("Duplicate physiology specialty definition")
        if len(set(self.biology_skill_ids)) != len(self.biology_skill_ids):
            raise ValueError("Duplicate Biology specialty definition")
        pairs = [
            frozenset((value.left_species_id, value.right_species_id))
            for value in self.relationships
        ]
        if any(not pair <= species_ids for pair in pairs):
            raise ValueError("Physiology relationship names an unknown species")
        if len(set(pairs)) != len(pairs):
            raise ValueError("Ambiguous physiology relationship")
        return self


class PhysiologyAdjustment(Record):
    actor_id: Id
    target_actor_id: Id
    actor_species_id: Id
    target_species_id: Id
    band: PhysiologyBand
    base_penalty: int = Field(le=0, ge=-20)
    applied_penalty: int = Field(le=0, ge=-20)
    bypass_skill_id: str | None = None
    bypass_check: CheckTrace | None = None
    reference: Literal["B181"] = REFERENCE

    def modifier(self) -> tuple[Modifier, ...]:
        if not self.applied_penalty:
            return ()
        return (
            Modifier(
                self.applied_penalty,
                f"{self.band.value} physiology",
                PROFILE,
                REFERENCE,
                ModifierKind.SITUATIONAL,
            ),
        )


@dataclass(frozen=True, slots=True)
class _Relationship:
    band: PhysiologyBand
    penalty: int


def _level(build: ValidatedBuild, skill_id: str) -> int | None:
    value = next((entry.value for entry in build.sheet.values if entry.target == skill_id), None)
    if value is None or not value.is_finite() or value != value.to_integral_value():
        return None
    level = int(value)
    return level if level > 0 else None


def _relationship(
    physiology: PhysiologyWorld,
    actor: SpeciesPhysiology,
    target: SpeciesPhysiology,
) -> _Relationship:
    if target.machine:
        return _Relationship(PhysiologyBand.MACHINE, 0)
    if actor.id == target.id:
        return _Relationship(PhysiologyBand.SAME_SPECIES, 0)
    pair = frozenset((actor.id, target.id))
    explicit = next(
        (
            value
            for value in physiology.relationships
            if frozenset((value.left_species_id, value.right_species_id)) == pair
        ),
        None,
    )
    if explicit is not None:
        return _Relationship(PhysiologyBand(explicit.band), explicit.penalty)
    if actor.world_id == target.world_id:
        return _Relationship(PhysiologyBand.VERY_DIFFERENT_SPECIES, -5)
    raise ValidationError("Species relationship is absent or ambiguous")


def _require_anatomy(
    resources: ResourceState, assignment: ActorPhysiology, species: SpeciesPhysiology
) -> None:
    hp = next((pool for pool in resources.pools if pool.id == f"hp:{assignment.actor_id}"), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != PROFILE:
        raise ValidationError("Physiology requires pinned character injury state")
    if hp.injury.anatomy != species.anatomy:
        raise ValidationError("Character anatomy and authoritative species state disagree")


def resolve_physiology(
    world: World,
    physiology: PhysiologyWorld,
    resources: ResourceState,
    build: ValidatedBuild,
    *,
    actor_id: str,
    target_actor_id: str,
    rng: RandomSource,
    bypass_skill_id: str | None = None,
) -> PhysiologyAdjustment:
    """Resolve B181 before the affected procedure rolls or mutates state."""
    if build.rules.edition != "gurps-4e":
        raise ValidationError("Physiology requires the exact Basic Set character build")
    world.validate()
    entities = {value.id: value for value in world.entities}
    if any(
        entities.get(identifier) is None or entities[identifier].kind is not EntityKind.ACTOR
        for identifier in (actor_id, target_actor_id)
    ):
        raise ValidationError("Physiology requires two authoritative actor entities")
    assignments = {value.actor_id: value for value in physiology.actors}
    actor_assignment = assignments.get(actor_id)
    target_assignment = assignments.get(target_actor_id)
    if actor_assignment is None or target_assignment is None:
        raise ValidationError("Species context is absent or ambiguous")
    species = {value.id: value for value in physiology.species}
    actor_species = species[actor_assignment.species_id]
    target_species = species[target_assignment.species_id]
    _require_anatomy(resources, actor_assignment, actor_species)
    _require_anatomy(resources, target_assignment, target_species)
    relationship = _relationship(physiology, actor_species, target_species)
    if relationship.band is PhysiologyBand.MACHINE:
        raise ValidationError("Physiology skills cannot affect a Machine")

    bypass_check = None
    applied = relationship.penalty
    if bypass_skill_id is not None and relationship.penalty:
        specialty = next(
            (value for value in physiology.specialties if value.definition_id == bypass_skill_id),
            None,
        )
        if specialty is not None and specialty.species_id == target_species.id:
            target_level = _level(build, bypass_skill_id)
        elif target_species.common_animal and bypass_skill_id in physiology.biology_skill_ids:
            biology = _level(build, bypass_skill_id)
            target_level = None if biology is None else biology - 4
        else:
            raise ValidationError("Selected physiology bypass is not relevant to this species")
        if target_level is None or target_level < 1:
            raise ValidationError("Approved build lacks a usable physiology bypass")
        bypass_check = success_roll(PROFILE, target_level, (), rng=rng)
        if bypass_check.outcome.succeeded:
            applied = 0
    return PhysiologyAdjustment(
        actor_id=actor_id,
        target_actor_id=target_actor_id,
        actor_species_id=actor_species.id,
        target_species_id=target_species.id,
        band=relationship.band,
        base_penalty=relationship.penalty,
        applied_penalty=applied,
        bypass_skill_id=bypass_skill_id,
        bypass_check=bypass_check,
    )


def replay_physiology(adjustment: PhysiologyAdjustment) -> PhysiologyAdjustment:
    """Re-score recorded bypass entropy without consulting mutable world state."""
    if adjustment.bypass_check is None:
        return adjustment
    check = replay_success(adjustment.bypass_check)
    applied = 0 if check.outcome.succeeded else adjustment.base_penalty
    return adjustment.model_copy(update={"applied_penalty": applied, "bypass_check": check})


def situation_with_physiology(
    skill_id: str,
    situation: Situation,
    adjustment: PhysiologyAdjustment,
) -> Situation:
    """Feed the authoritative result into the existing mundane check service."""
    if skill_id not in AFFECTED_SKILLS:
        raise ValidationError(f"Physiology does not apply to {skill_id}")
    return replace(
        situation,
        situational=situation.situational + adjustment.modifier(),
    )
