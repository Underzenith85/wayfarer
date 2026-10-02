"""Private multiple-Symptoms Cyclic payloads preserve frozen authoring contracts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as ModelValidationError
from support.runtime import build_play
from test_composed_attack_host import declare, defense, fixture
from test_composed_attacks import attacker, resolve
from test_composed_cyclic_boundaries import cyclic
from test_multiple_symptoms import EFFECTS
from test_resources import engine
from test_symptoms import selection

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.symptoms import symptom_spec
from wayfarer.engine.rules.types.cyclic import (
    CyclicAttack,
    CyclicExposure,
    ZeroDamageCyclicAttack,
    ZeroDamageCyclicExposure,
)
from wayfarer.engine.rules.types.cyclic_runtime import (
    MultipleSymptomsCyclicAttack,
    MultipleSymptomsCyclicExposure,
    ZeroDamageMultipleSymptomsCyclicAttack,
    ZeroDamageMultipleSymptomsCyclicExposure,
    additional_symptoms,
    create_attack,
    create_exposure,
)
from wayfarer.engine.rules.types.disease import ContactExposure
from wayfarer.engine.simulation.campaign.scenario_document import InitialResources
from wayfarer.engine.simulation.combat.commands import EndEncounter
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.cyclic_host_state import (
    CyclicPolicy,
    ObserveCyclicExposure,
    ObserveCyclicStop,
)
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.resources import Advance, ResourceState
from wayfarer.engine.simulation.traits.harmful_physiology_state import AdvancePhysiology
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService


@pytest.mark.parametrize(
    ("distance", "dr", "initial_roll", "repeat_dice", "hp", "blind"),
    [(10, 0, 1, [4, 3], 3, True), (2, 3, 2, [5, 6], 5, False)],
)
def test_zero_or_dr_absorbed_delivery_retains_multiple_effects_for_real_ticks(
    distance: int, dr: int, initial_roll: int, repeat_dice: list[int], hp: int, blind: bool
) -> None:
    state, result = resolve(
        attacker(cyclic("burn"), *EFFECTS, levels=1, dx=20),
        distance=distance,
        dr=dr,
        dice=[2, 2, 2, initial_roll],
    )
    assert result.injury is None or result.injury.injury == 0
    assert next(p.current for p in state.pools if p.id == "hp:b") == 10
    assert not any(effect.active for effect in state.symptom_effects)
    source = state.cyclic_attacks[0]
    expected_type = (
        ZeroDamageMultipleSymptomsCyclicAttack if distance == 10 else MultipleSymptomsCyclicAttack
    )
    assert isinstance(source, expected_type) and len(additional_symptoms(source)) == 1
    assert source.hp_debt == 0 and source.fp_debt == 0
    encoded = state.model_dump_json()
    restarted = ResourceState.model_validate_json(encoded)
    assert restarted.model_dump_json() == encoded
    rng = RecordedDice(repeat_dice)
    clock = Advance(id="ticks", actor_id="a", expected_revision=restarted.revision, to=20)
    state = engine().apply(restarted, clock, rng=rng, system=True)
    assert rng.exhausted()
    assert next(p.current for p in state.pools if p.id == "hp:b") == hp
    assert len(state.symptom_effects) == 2 and len(state.symptom_debts) == 2
    assert sum(d.remaining for d in state.symptom_debts) == 10 - hp
    assert acute_blindness(state, "b") is blind
    assert sum(m.value for m in check_modifiers(state, "b", "dx")) == -3
    restored = ResourceState.model_validate_json(state.model_dump_json())
    assert (
        restored == state
        and engine().apply(restored, clock, rng=RecordedDice([]), system=True) == restored
    )
    with pytest.raises(ModelValidationError):
        InitialResources.model_validate_json(
            json.dumps({"cyclic_attacks": [source.model_dump(mode="json")]})
        )


@pytest.mark.parametrize("zero", [False, True])
def test_private_factory_preserves_single_effect_attack_and_exposure_bytes(zero: bool) -> None:
    kind = ZeroDamageCyclicAttack if zero else CyclicAttack
    source = kind.model_validate(
        {
            "id": "legacy",
            "attacker_id": "a",
            "actor_id": "b",
            "attack_id": "source",
            "basic_damage": 0 if zero else 2,
            "damage_dice": 1,
            "damage_type": "tox",
            "resistance": 0,
            "ht": 10,
            "interval": 86400,
            "remaining": 2,
            "due": 86400,
            "stop_condition": "treatment",
            "symptom_spec": symptom_spec(selection()),
            "symptom_source_id": "a:source",
        }
    )
    rebuilt = create_attack({**source.model_dump(), "additional_symptoms": ()})
    assert type(rebuilt) is kind and rebuilt.model_dump_json() == source.model_dump_json()
    relationship = ContactExposure(
        id="contact",
        actor_id="a",
        carrier_id="b",
        disease_id="source",
        vector="respiratory",
        contact="close-conversation",
        occurred_at=0,
    )
    exposure_kind = ZeroDamageCyclicExposure if zero else CyclicExposure
    exposure = exposure_kind.model_validate(
        {"id": "exposure", "source": source, "relationship": relationship, "ht": 10, "due": 86400}
    )
    rebuilt_exposure = create_exposure(
        source, {k: v for k, v in exposure.model_dump().items() if k != "source"}
    )
    assert type(rebuilt_exposure) is exposure_kind
    assert rebuilt_exposure.model_dump_json() == exposure.model_dump_json()
    state = ResourceState(cyclic_attacks=(rebuilt,), cyclic_exposures=(rebuilt_exposure,))
    assert (
        ResourceState.model_validate_json(state.model_dump_json()).model_dump_json()
        == state.model_dump_json()
    )
    assert "additional_symptoms" not in json.dumps(InitialResources.model_json_schema())


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("zero", [False, True])
async def test_contagious_multiple_effect_snapshot_and_infection_survive_restart(
    tmp_path: Path, backend: str, zero: bool
) -> None:
    symptoms = tuple(
        s.model_copy(
            update={"parameters": s.parameters.model_copy(update={"damage_kind": "toxic"})}
        )
        for s in EFFECTS
        if s.parameters is not None
    )
    cid, play, source_id = await fixture(
        tmp_path,
        backend,
        modifiers=(cyclic("tox", contagious="mild"),) + symptoms,
        kind="tox",
        levels=1,
        distance=10 if zero else 2,
        skill_points=16,
        cyclic_policy=CyclicPolicy(condition="treatment"),
        contagion_vector="respiratory",
        incubation_seconds=86400,
    )
    attack = await declare(play, cid, source_id)
    play.rng = RecordedDice([2, 2, 2, 1 if zero else 2])
    chosen, result = await defense(play, cid)
    state = play._load(await play.store.read(cid))
    source = state.resources.cyclic_attacks[0]
    assert source.basic_damage == (0 if zero else 2) and len(additional_symptoms(source)) == 1
    assert play.rng.exhausted()
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="disengage",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            reason="The infectious exposure continues after combat",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    contact = ObserveCyclicExposure(
        id="contact",
        actor_id="a",
        expected_revision=state.revision,
        source_occurrence_id=source.id,
        location_id="dock",
        contact="intimate-contact",
        route="shared-air",
        reason="Observed contact with the infectious source",
    )
    await CyclicService(play).execute(cid, contact, principal_id="gm")
    state = play._load(await play.store.read(cid))
    snapshot = state.resources.cyclic_exposures[0]
    expected_type = (
        ZeroDamageMultipleSymptomsCyclicExposure if zero else MultipleSymptomsCyclicExposure
    )
    assert isinstance(snapshot, expected_type) and additional_symptoms(
        snapshot.source
    ) == additional_symptoms(source)
    with pytest.raises(ModelValidationError):
        InitialResources.model_validate_json(
            json.dumps({"cyclic_exposures": [snapshot.model_dump(mode="json")]})
        )
    stop = ObserveCyclicStop(
        id="cure-carrier",
        actor_id="b",
        expected_revision=state.revision,
        occurrence_id=source.id,
        location_id="dock",
        reason="Observed the approved treatment completed",
    )
    await CyclicService(play).execute(cid, stop, principal_id="gm")
    play = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice([]))
    state = play._load(await play.store.read(cid))
    assert additional_symptoms(state.resources.cyclic_exposures[0].source) == additional_symptoms(
        source
    )
    # B443: failed exposure HT then4 injury; no second check before first damage.
    play.rng = RecordedDice([5, 5, 5, 4])
    onset = AdvancePhysiology(id="onset", actor_id="a", expected_revision=state.revision, to=86400)
    await HarmfulPhysiologyService(play).execute(cid, onset, principal_id="gm")
    state = play._load(await play.store.read(cid))
    secondary = next(a for a in state.resources.cyclic_attacks if a.actor_id == "a")
    assert len(additional_symptoms(secondary)) == 1 and secondary.hp_debt == 4
    assert sum(m.value for m in check_modifiers(state.resources, "a", "dx")) == -3
    assert not acute_blindness(state.resources, "a") and play.rng.exhausted()
    # A failed daily recovery HT and another4 damage activates the second effect.
    play.rng = RecordedDice([5, 5, 5, 4])
    repeat = AdvancePhysiology(
        id="repeat", actor_id="a", expected_revision=state.revision, to=172800
    )
    await HarmfulPhysiologyService(play).execute(cid, repeat, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 2
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (10 if zero else 8)
    assert acute_blindness(state.resources, "a") and not acute_blindness(state.resources, "b")
    assert sum(d.remaining for d in state.resources.symptom_debts if d.pool_id == "hp:a") == 8
    assert len([e for e in state.resources.symptom_effects if e.actor_id == "a"]) == 2
    assert play.rng.exhausted()
    final = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice([]))
    await ComposedAttackService(restarted).execute(cid, attack, principal_id="alice")
    assert await CombatService(restarted).execute(cid, chosen, principal_id="bob") == result
    await CyclicService(restarted).execute(cid, contact, principal_id="gm")
    await CyclicService(restarted).execute(cid, stop, principal_id="gm")
    await HarmfulPhysiologyService(restarted).execute(cid, onset, principal_id="gm")
    await HarmfulPhysiologyService(restarted).execute(cid, repeat, principal_id="gm")
    assert await restarted.store.read(cid) == final == await restarted.store.replay(cid)
