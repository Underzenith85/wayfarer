"""Independent fixtures for Campaigns B442-B444 and Characters B20-B21."""

import json

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.disease import (
    YEAR_SECONDS,
    AgingRules,
    ContactExposure,
    DiseaseProfile,
    PermanentAttributeLoss,
    WoundInfectionRisk,
)
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.health.disease import (
    ApprovePermanentChange,
    EnrollAging,
    ExposeDisease,
    RecordInfectionRisk,
    ResolveAging,
    ResolveDisease,
    ResolveInfectionRisk,
    aging_schedules,
    apply_aging,
    apply_disease,
    apply_infection_risk,
    approve_permanent_change,
    disease_episodes,
    disease_projection,
    due_health_effects,
    permanent_attribute_losses,
    permanent_changes,
)
from wayfarer.engine.simulation.resources import Pool, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def state(*, now: int = 0, hp: int = 10) -> ResourceState:
    return ResourceState(
        game_time=now,
        pools=(
            Pool(
                id="hp:a",
                current=hp,
                maximum=10,
                injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
            ),
        ),
    )


def flu(**updates: object) -> DiseaseProfile:
    return DiseaseProfile.model_validate(
        {
            "id": "purple-fever",
            "title": "Purple Fever",
            "vector": "respiratory",
            "resistance_modifier": -2,
            "incubation_seconds": 86_400,
            "cycle_seconds": 43_200,
            "cycles": 6,
            "damage_add": 1,
            "acquired_immunity": True,
            **updates,
        }
    )


def contact(**updates: object) -> ContactExposure:
    return ContactExposure.model_validate(
        {
            "id": "contact-1",
            "actor_id": "a",
            "disease_id": "purple-fever",
            "vector": "respiratory",
            "contact": "close-conversation",
            "occurred_at": 0,
            **updates,
        }
    )


def expose(
    resources: ResourceState, *, relationship: ContactExposure | None = None
) -> ResourceState:
    relationship = relationship or contact()
    updated, _ = apply_disease(
        resources,
        ExposeDisease(
            id="expose",
            actor_id="a",
            expected_revision=resources.revision,
            relationship_id=relationship.id,
        ),
        rng=RecordedDice([]),
        profile=flu(),
        relationship=relationship,
        ht=10,
        system=True,
    )
    return updated


def test_contact_is_authored_and_incubation_is_private() -> None:
    resources = state()
    command = ExposeDisease(
        id="expose", actor_id="a", expected_revision=0, relationship_id="contact-1"
    )
    with pytest.raises(ValidationError, match="authored disease and contact"):
        apply_disease(resources, command, rng=RecordedDice([]), system=True)

    resources = expose(resources, relationship=contact(carrier_id="secret-npc"))
    assert disease_projection(resources, ("a",)) == ()
    director = disease_projection(resources, (), director=True)[0]
    assert director["carrier_id"] == "secret-npc"
    assert director["stage"] == "exposure"
    wire = json.loads(resources.model_dump_json())
    assert "secret-npc" in json.dumps(wire)  # authoritative checkpoint retains the relationship


def test_restart_and_large_jump_settle_every_due_disease_check_once() -> None:
    resources = expose(state())
    episode_id = disease_episodes(resources)[0].id
    # End-of-day exposure check, incubation, one failed cycle and one successful
    # recovery check are all overdue after this disconnected time jump.
    resources = ResourceState.model_validate_json(
        resources.model_copy(update={"game_time": 4 * 86_400}).model_dump_json()
    )
    command = ResolveDisease(
        id="catch-up", actor_id="a", expected_revision=1, episode_id=episode_id
    )
    updated, result = apply_disease(
        resources,
        command,
        rng=RecordedDice([6, 6, 6, 5, 5, 5, 1, 1, 1]),
        system=True,
    )
    assert result.hp_lost == 1
    assert len(result.checks) == 3
    assert not result.active
    episode = disease_episodes(updated)[0]
    assert episode.stage == "recovered" and episode.immune
    assert next(p.current for p in updated.pools if p.id == "hp:a") == 9
    assert updated.illnesses[0].active is False

    replayed, same = apply_disease(
        ResourceState.model_validate_json(updated.model_dump_json()),
        command,
        rng=RecordedDice([]),
        system=True,
    )
    assert replayed == ResourceState.model_validate_json(updated.model_dump_json())
    assert same == result


def test_symptom_effects_activate_at_threshold_and_retire_on_recovery() -> None:
    disease = flu(cycles=2, damage_add=4, symptom_effect_ids=("effect:fever",))
    relationship = contact()
    resources, _ = apply_disease(
        state(now=200_000),
        ExposeDisease(
            id="expose", actor_id="a", expected_revision=0, relationship_id=relationship.id
        ),
        profile=disease,
        relationship=relationship,
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    episode = disease_episodes(resources)[0]
    resources = resources.model_copy(update={"game_time": episode.due + disease.incubation_seconds})
    resources, first = apply_disease(
        resources,
        ResolveDisease(id="symptoms", actor_id="a", expected_revision=1, episode_id=episode.id),
        rng=RecordedDice([6, 6, 6, 6, 6, 6]),
        system=True,
    )
    assert first.active and first.symptomatic
    assert resources.active_effect_ids == ("effect:fever",)
    assert due_health_effects(resources, frozenset({"a"}), first.due or 0) == (
        (first.due, episode.id),
    )
    resources = resources.model_copy(update={"game_time": first.due})
    resources, recovered = apply_disease(
        resources,
        ResolveDisease(id="recover", actor_id="a", expected_revision=2, episode_id=episode.id),
        rng=RecordedDice([1, 1, 1]),
        system=True,
    )
    assert not recovered.active and not resources.active_effect_ids


def test_recorded_protection_changes_only_the_exposure_check() -> None:
    protected = contact(protection_id="respirator", protection_bonus=3, protection_understood=True)
    resources = expose(state(), relationship=protected)
    episode = disease_episodes(resources)[0]
    resources = resources.model_copy(update={"game_time": episode.due})
    updated, result = apply_disease(
        resources,
        ResolveDisease(id="resist", actor_id="a", expected_revision=1, episode_id=episode.id),
        rng=RecordedDice([4, 4, 4]),
        system=True,
    )
    assert not result.infected and not result.active
    assert disease_episodes(updated)[0].checks[0].effective_target == 13
    with pytest.raises(ValueError, match="understood authored precaution"):
        contact(protection_id="mask", protection_bonus=1)


def test_wound_infection_uses_recorded_wound_elapsed_time_and_antibiotics() -> None:
    resources = state(now=100).model_copy(
        update={"events": (ResourceEvent(id="injury:cut-1", at=90, target_id="a", kind="{}"),)}
    )
    risk = WoundInfectionRisk(
        id="risk-1",
        actor_id="a",
        wound_event_id="cut-1",
        opened_at=90,
        due=190,
        contamination_modifier=-2,
        antibiotics=True,
    )
    resources, pending = apply_infection_risk(
        resources,
        RecordInfectionRisk(id="record-risk", actor_id="a", expected_revision=0, risk_id=risk.id),
        risk=risk,
        rng=RecordedDice([]),
        system=True,
    )
    assert pending.due == 190
    early = ResolveInfectionRisk(
        id="settle-risk", actor_id="a", expected_revision=1, risk_id=risk.id
    )
    with pytest.raises(ConflictError, match="not due"):
        apply_infection_risk(
            resources, early, infection=flu(), ht=10, rng=RecordedDice([]), system=True
        )
    resources = ResourceState.model_validate_json(
        resources.model_copy(update={"game_time": 190}).model_dump_json()
    )
    updated, result = apply_infection_risk(
        resources, early, infection=flu(), ht=10, rng=RecordedDice([]), system=True
    )
    assert not result.infected and not result.checks
    assert not disease_episodes(updated)


def test_dirty_wound_failure_creates_the_same_disease_runtime() -> None:
    resources = state(now=100).model_copy(
        update={"events": (ResourceEvent(id="injury:cut", at=90, target_id="a", kind="{}"),)}
    )
    risk = WoundInfectionRisk(
        id="dirty",
        actor_id="a",
        wound_event_id="cut",
        opened_at=90,
        due=100,
        contamination_modifier=-2,
    )
    resources, _ = apply_infection_risk(
        resources,
        RecordInfectionRisk(id="record", actor_id="a", expected_revision=0, risk_id="dirty"),
        risk=risk,
        rng=RecordedDice([]),
        system=True,
    )
    updated, result = apply_infection_risk(
        resources,
        ResolveInfectionRisk(id="resolve", actor_id="a", expected_revision=1, risk_id="dirty"),
        infection=flu(vector="contact", id="wound-infection", title="Wound Infection"),
        ht=10,
        rng=RecordedDice([6, 6, 6]),
        system=True,
    )
    assert result.infected
    assert disease_episodes(updated)[0].stage == "cycles"


def test_aging_is_profile_gated_and_uses_chronological_thresholds() -> None:
    resources = state()
    enroll = EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="age:a")
    with pytest.raises(ValidationError, match="must be enabled"):
        apply_aging(
            resources,
            enroll,
            rules=AgingRules(enabled=False),
            age_seconds=49 * YEAR_SECONDS,
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )
    resources, result = apply_aging(
        resources,
        enroll,
        rules=AgingRules(enabled=True, technology_level=3),
        age_seconds=70 * YEAR_SECONDS,
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.due == 0
    updated, result = apply_aging(
        resources,
        ResolveAging(id="age-roll", actor_id="a", expected_revision=1, schedule_id="age:a"),
        rng=RecordedDice([6, 6, 6] * 4),
        system=True,
    )
    assert len(result.checks) == 4
    assert result.due == YEAR_SECONDS // 2
    assert len(result.permanent_change_ids) == 1
    assert permanent_changes(updated)[0].losses == PermanentAttributeLoss(st=2, dx=2, iq=2, ht=2)
    assert (
        aging_schedules(ResourceState.model_validate_json(updated.model_dump_json()))[0]
        == aging_schedules(updated)[0]
    )


def test_longevity_turns_seventeen_into_one_level_aging_loss() -> None:
    resources, _ = apply_aging(
        state(),
        EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="age:a"),
        rules=AgingRules(enabled=True, longevity=True),
        age_seconds=50 * YEAR_SECONDS,
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    updated, _ = apply_aging(
        resources,
        ResolveAging(id="roll", actor_id="a", expected_revision=1, schedule_id="age:a"),
        rng=RecordedDice([5, 6, 6] * 4),
        system=True,
    )
    assert permanent_changes(updated)[0].losses == PermanentAttributeLoss(st=1, dx=1, iq=1, ht=1)


def test_permanent_health_loss_requires_exact_approved_build_and_survives_healing() -> None:
    resources, _ = apply_aging(
        state(),
        EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="age:a"),
        rules=AgingRules(enabled=True),
        age_seconds=50 * YEAR_SECONDS,
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    resources, result = apply_aging(
        resources,
        ResolveAging(id="roll", actor_id="a", expected_revision=1, schedule_id="age:a"),
        rng=RecordedDice([6, 6, 6] * 4),
        system=True,
    )
    change = permanent_changes(resources)[0]
    wrong = ApprovePermanentChange(
        id="approve",
        actor_id="a",
        expected_revision=2,
        change_id=change.id,
        losses=PermanentAttributeLoss(st=1),
        approved_build_revision="build-2",
    )
    with pytest.raises(ValidationError, match="exact permanent"):
        approve_permanent_change(resources, wrong, system=True)
    approve = wrong.model_copy(update={"losses": change.losses})
    resources, approved = approve_permanent_change(resources, approve, system=True)
    assert approved.permanent_change_ids == result.permanent_change_ids
    assert permanent_attribute_losses(resources, "a") == change.losses

    # Restoring transient HP is an ordinary healing/rebuild-shaped state change;
    # the applied mutation ledger remains authoritative and round-trips unchanged.
    healed = resources.model_copy(
        update={
            "pools": tuple(p.model_copy(update={"current": p.maximum}) for p in resources.pools)
        }
    )
    healed = ResourceState.model_validate_json(healed.model_dump_json())
    assert permanent_attribute_losses(healed, "a") == change.losses


def test_disease_lasting_effect_uses_the_same_mutation_boundary() -> None:
    disease = flu(
        cycles=1,
        lasting_after_damage=1,
        lasting_loss={"ht": 1},
    )
    relationship = contact()
    resources, _ = apply_disease(
        state(now=200_000),
        ExposeDisease(
            id="expose", actor_id="a", expected_revision=0, relationship_id=relationship.id
        ),
        profile=disease,
        relationship=relationship,
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    episode = disease_episodes(resources)[0]
    resources = resources.model_copy(update={"game_time": episode.due + disease.incubation_seconds})
    updated, result = apply_disease(
        resources,
        ResolveDisease(id="resolve", actor_id="a", expected_revision=1, episode_id=episode.id),
        rng=RecordedDice([6, 6, 6, 6, 6, 6]),
        system=True,
    )
    assert result.permanent_change_ids
    assert permanent_changes(updated)[0].losses.ht == 1
