"""Independent B181 expectations for cross-species physiology modifiers."""

from decimal import Decimal
from typing import Final, Literal

import pytest

from wayfarer.engine.character.compiler import DerivedSheet, ValidatedBuild
from wayfarer.engine.rules.catalog import CampaignRules
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.skills.mundane.medicine import PROCEDURES
from wayfarer.engine.rules.skills.mundane.procedures import Performer, Situation, attempt, replay
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.skills.cinematic import (
    CinematicSkillCommand,
    apply_cinematic_skill,
    visible_history,
)
from wayfarer.engine.simulation.skills.physiology import (
    AFFECTED_SKILLS,
    ActorPhysiology,
    PhysiologyBand,
    PhysiologySpecialty,
    PhysiologyWorld,
    SpeciesPhysiology,
    SpeciesRelationship,
    replay_physiology,
    resolve_physiology,
    situation_with_physiology,
)
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ValidationError

PROFILE: Final[Literal["gurps-basic-set-4e-2004"]] = "gurps-basic-set-4e-2004"


def build(*skills: tuple[str, int]) -> ValidatedBuild:
    values = tuple(DerivedValue(identifier, Decimal(level), ()) for identifier, level in skills)
    return ValidatedBuild(
        "approved-build",
        CampaignRules("gurps-4e", (), "policy:test", 1),
        (),
        0,
        0,
        DerivedSheet(values),
        "Healer",
        "",
    )


def world(*actors: str) -> World:
    return World(entities=tuple(Entity(value, EntityKind.ACTOR, value.title()) for value in actors))


def resources(
    assignments: tuple[ActorPhysiology, ...], species: tuple[SpeciesPhysiology, ...]
) -> ResourceState:
    by_species = {value.id: value for value in species}
    return ResourceState(
        pools=tuple(
            Pool(
                id=f"hp:{assignment.actor_id}",
                current=10,
                maximum=10,
                injury=InjuryStatus(
                    profile_id=PROFILE,
                    anatomy=by_species[assignment.species_id].anatomy,
                ),
            )
            for assignment in assignments
        )
    )


def context(
    target_species: SpeciesPhysiology,
    *relationships: SpeciesRelationship,
    specialties: tuple[PhysiologySpecialty, ...] = (),
    biology: tuple[str, ...] = (),
) -> tuple[World, PhysiologyWorld, ResourceState]:
    human = SpeciesPhysiology(id="human", world_id="earth", anatomy="human")
    assignments = (
        ActorPhysiology(actor_id="doctor", species_id="human"),
        ActorPhysiology(actor_id="patient", species_id=target_species.id),
    )
    physiology = PhysiologyWorld(
        species=(human, target_species),
        actors=assignments,
        relationships=relationships,
        specialties=specialties,
        biology_skill_ids=biology,
    )
    return world("doctor", "patient"), physiology, resources(assignments, physiology.species)


@pytest.mark.parametrize("penalty", (-2, -3, -4))
def test_similar_species_uses_the_exact_authored_band(penalty: int) -> None:
    elf = SpeciesPhysiology(id="elf", world_id="earth", anatomy="creature")
    relation = SpeciesRelationship(
        left_species_id="human",
        right_species_id="elf",
        band=PhysiologyBand.SIMILAR_SPECIES,
        penalty=penalty,
    )
    setting, physiology, state = context(elf, relation)
    result = resolve_physiology(
        setting,
        physiology,
        state,
        build(),
        actor_id="doctor",
        target_actor_id="patient",
        rng=RecordedDice([]),
    )
    assert (result.band, result.base_penalty, result.applied_penalty) == (
        PhysiologyBand.SIMILAR_SPECIES,
        penalty,
        penalty,
    )


def test_same_world_is_very_different_and_explicit_alien_is_six_or_worse() -> None:
    animal = SpeciesPhysiology(id="dog", world_id="earth", anatomy="creature", common_animal=True)
    setting, physiology, state = context(animal)
    different = resolve_physiology(
        setting,
        physiology,
        state,
        build(),
        actor_id="doctor",
        target_actor_id="patient",
        rng=RecordedDice([]),
    )
    assert (different.band, different.applied_penalty) == (
        PhysiologyBand.VERY_DIFFERENT_SPECIES,
        -5,
    )

    alien = SpeciesPhysiology(id="crystal", world_id="tau", anatomy="creature")
    relation = SpeciesRelationship(
        left_species_id="human",
        right_species_id="crystal",
        band=PhysiologyBand.UTTERLY_ALIEN,
        penalty=-8,
    )
    setting, physiology, state = context(alien, relation)
    utterly = resolve_physiology(
        setting,
        physiology,
        state,
        build(),
        actor_id="doctor",
        target_actor_id="patient",
        rng=RecordedDice([]),
    )
    assert (utterly.band, utterly.applied_penalty) == (PhysiologyBand.UTTERLY_ALIEN, -8)


def test_same_species_has_no_penalty_or_bypass_roll() -> None:
    human = SpeciesPhysiology(id="human", world_id="earth", anatomy="human")
    assignments = (
        ActorPhysiology(actor_id="doctor", species_id="human"),
        ActorPhysiology(actor_id="patient", species_id="human"),
    )
    physiology = PhysiologyWorld(species=(human,), actors=assignments)
    result = resolve_physiology(
        world("doctor", "patient"),
        physiology,
        resources(assignments, physiology.species),
        build(),
        actor_id="doctor",
        target_actor_id="patient",
        rng=RecordedDice([]),
    )
    assert result.band is PhysiologyBand.SAME_SPECIES
    assert result.applied_penalty == 0 and result.bypass_check is None


def test_machine_and_missing_or_ambiguous_context_reject_before_entropy() -> None:
    machine = SpeciesPhysiology(id="robot", world_id="earth", anatomy="creature", machine=True)
    setting, physiology, state = context(machine)
    with pytest.raises(ValidationError, match="Machine"):
        resolve_physiology(
            setting,
            physiology,
            state,
            build(),
            actor_id="doctor",
            target_actor_id="patient",
            rng=RecordedDice([]),
        )

    alien = SpeciesPhysiology(id="alien", world_id="elsewhere", anatomy="creature")
    setting, physiology, state = context(alien)
    with pytest.raises(ValidationError, match="absent or ambiguous"):
        resolve_physiology(
            setting,
            physiology,
            state,
            build(),
            actor_id="doctor",
            target_actor_id="patient",
            rng=RecordedDice([]),
        )
    with pytest.raises(ValidationError, match="Species context"):
        resolve_physiology(
            setting,
            physiology.model_copy(update={"actors": physiology.actors[:1]}),
            state,
            build(),
            actor_id="doctor",
            target_actor_id="patient",
            rng=RecordedDice([]),
        )


def test_racial_specialty_and_biology_minus_four_bypass_and_replay() -> None:
    dog = SpeciesPhysiology(id="dog", world_id="earth", anatomy="creature", common_animal=True)
    specialty = PhysiologySpecialty(definition_id="skill:physiology-dog", species_id="dog")
    setting, physiology, state = context(
        dog, specialties=(specialty,), biology=("skill:biology-zoology",)
    )
    racial = resolve_physiology(
        setting,
        physiology,
        state,
        build(("skill:physiology-dog", 12)),
        actor_id="doctor",
        target_actor_id="patient",
        bypass_skill_id="skill:physiology-dog",
        rng=RecordedDice([3, 3, 3]),
    )
    assert racial.base_penalty == -5 and racial.applied_penalty == 0
    assert replay_physiology(racial) == racial

    biology = resolve_physiology(
        setting,
        physiology,
        state,
        build(("skill:biology-zoology", 13)),
        actor_id="doctor",
        target_actor_id="patient",
        bypass_skill_id="skill:biology-zoology",
        rng=RecordedDice([3, 3, 3]),
    )
    assert biology.bypass_check is not None and biology.bypass_check.effective_target == 9
    assert biology.applied_penalty == 0

    failed = resolve_physiology(
        setting,
        physiology,
        state,
        build(("skill:physiology-dog", 8)),
        actor_id="doctor",
        target_actor_id="patient",
        bypass_skill_id="skill:physiology-dog",
        rng=RecordedDice([6, 6, 6]),
    )
    assert failed.applied_penalty == -5
    assert replay_physiology(failed) == failed


def test_adjustment_routes_through_existing_medical_check_and_replay() -> None:
    assert AFFECTED_SKILLS == {
        "skill:body-language",
        "skill:diagnosis",
        "skill:first-aid",
        "skill:physician",
        "skill:pressure-points",
        "skill:pressure-secrets",
        "skill:surgery",
    }
    troll = SpeciesPhysiology(id="troll", world_id="earth", anatomy="creature")
    relation = SpeciesRelationship(
        left_species_id="human",
        right_species_id="troll",
        band=PhysiologyBand.SIMILAR_SPECIES,
        penalty=-4,
    )
    setting, physiology, state = context(troll, relation)
    adjustment = resolve_physiology(
        setting,
        physiology,
        state,
        build(),
        actor_id="doctor",
        target_actor_id="patient",
        rng=RecordedDice([]),
    )
    result = attempt(
        PROCEDURES,
        Performer("skill:diagnosis", 12),
        situation_with_physiology(
            "skill:diagnosis",
            Situation(frozenset({"patient-and-symptoms"})),
            adjustment,
        ),
        rng=RecordedDice([3, 3, 3]),
    )
    assert result.check is not None and result.check.effective_target == 8
    assert replay(result) == result


def test_pressure_points_records_context_in_receipt_and_is_restart_safe() -> None:
    troll = SpeciesPhysiology(id="troll", world_id="earth", anatomy="creature")
    relation = SpeciesRelationship(
        left_species_id="human",
        right_species_id="troll",
        band=PhysiologyBand.SIMILAR_SPECIES,
        penalty=-2,
    )
    setting, physiology, state = context(troll, relation)
    actor_build = build(("skill:pressure-points", 12))
    adjustment = resolve_physiology(
        setting,
        physiology,
        state,
        actor_build,
        actor_id="doctor",
        target_actor_id="patient",
        rng=RecordedDice([]),
    )
    command = CinematicSkillCommand(
        id="pressure",
        actor_id="doctor",
        expected_revision=0,
        build_revision=actor_build.revision,
        skill_id="skill:pressure-points",
        target_actor_id="patient",
    )
    changed, outcome = apply_cinematic_skill(
        state,
        setting,
        actor_build,
        command,
        authorized_actor_id="doctor",
        rng=RecordedDice([3, 3, 3]),
        physiology=adjustment,
    )
    assert outcome.margin == 1 and outcome.physiology == adjustment
    restarted = ResourceState.model_validate_json(changed.model_dump_json())
    assert apply_cinematic_skill(
        restarted,
        setting,
        actor_build,
        command,
        authorized_actor_id="doctor",
        rng=RecordedDice([]),
    ) == (restarted, outcome)
    assert visible_history(restarted, viewer_actor_id="patient") == ()
