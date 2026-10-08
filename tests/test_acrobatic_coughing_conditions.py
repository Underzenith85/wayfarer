"""B428 direct DX-skill coughing penalty survives the B421 reaction exemption."""

from pathlib import Path

import pytest
from test_acrobatic_trait_bonuses import reaction_fixture
from test_combat_sensory_authority import change
from test_symptom_attribute_consumers import penalize

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.hazard import (
    HazardEnvironment,
    HazardProtection,
    HazardSchedule,
    HazardSpec,
)
from wayfarer.engine.rules.types.symptoms import SymptomEffect, SymptomSpec
from wayfarer.engine.rules.types.toxin import ToxinExposure, ToxinProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.acrobatic_conditions import modifiers
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.tactical_transitions import prepare_defense
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations

FEATURE = "acrobatic-coughing-conditions"


def coughing(state: PlayState, carrier: str, actor_id: str = "b", active: bool = True) -> PlayState:
    resources = state.resources
    until = resources.game_time + 100 if active else resources.game_time
    if carrier == "symptoms":
        resources = resources.model_copy(
            update={
                "symptom_effects": resources.symptom_effects
                + (
                    SymptomEffect(
                        id="cough",
                        pool_id=f"hp:{actor_id}",
                        source_id="cough",
                        actor_id=actor_id,
                        spec=SymptomSpec(kind="coughing"),
                        active=active,
                    ),
                )
            }
        )
    elif carrier == "hazard":
        resources = resources.model_copy(
            update={
                "hazards": (
                    HazardSchedule(
                        id="cough",
                        actor_id=actor_id,
                        spec=HazardSpec(
                            id="cough",
                            kind="poison",
                            scene_id="room",
                            affliction="coughing",
                            reference="B439",
                        ),
                        started=0,
                        due=resources.game_time + 100,
                        remaining=1,
                        ht=10,
                        will=10,
                        swimming=10,
                        active=False,
                        affliction_until=until,
                    ),
                )
            }
        )
    elif carrier == "atmosphere":
        resources = resources.model_copy(
            update={
                "hazards": (
                    HazardSchedule(
                        id="cough",
                        actor_id=actor_id,
                        spec=HazardSpec(
                            id="cough",
                            kind="atmosphere",
                            scene_id="room",
                            reference="B429",
                            environment=HazardEnvironment(
                                medium="air",
                                intensity=1,
                                duration_seconds=100,
                                source_class="smoke",
                            ),
                            protection=HazardProtection(),
                        ),
                        started=0,
                        due=resources.game_time + 100,
                        remaining=1,
                        ht=10,
                        will=10,
                        swimming=10,
                        active=active,
                        conditions=("coughing",),
                    ),
                )
            }
        )
    else:
        resources = resources.model_copy(
            update={
                "toxins": (
                    ToxinExposure(
                        id="cough",
                        actor_id=actor_id,
                        profile=ToxinProfile(
                            id="cough", vector="respiratory", condition="coughing", reference="B439"
                        ),
                        identity_digest="a" * 64,
                        started=0,
                        due=resources.game_time + 100,
                        remaining=1,
                        ht=10,
                        active=False,
                        condition_until=until,
                    ),
                )
            }
        )
    return state.model_copy(update={"resources": resources})


@pytest.mark.parametrize("flying", [False, True])
@pytest.mark.parametrize("carrier", ["symptoms", "hazard", "atmosphere", "toxin"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_coughing_changes_committed_reaction_and_preserves_legacy_generation(
    tmp_path: Path,
    flying: bool,
    carrier: str,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        generations,
        "ACTIVE",
        frozenset(
            {"acrobatic-reaction-attributes", FEATURE}
            if enabled
            else {"acrobatic-reaction-attributes"}
        ),
    )
    cid, play = await reaction_fixture(tmp_path, "sqlite", flying, ())
    await change(play, cid, lambda state: coughing(penalize(state, "b"), carrier))
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="coughing-dodge",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
    play.rng = RecordedDice([])
    preview = prepare_defense(play.rules_context, state, state.encounters[0], command)
    assert preview.participants[1].acrobatic_dodge_trace is None and play.rng.exhausted()
    play.rng = RecordedDice([2, 3, 3, 3, 3, 3, 3, 3, 3] + ([1] if enabled else []))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == (5 if enabled else 8)
    assert trace.outcome.succeeded is not enabled
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == (7 if enabled else 11)
    assert result.injury.injury == (3 if enabled else 0)
    assert play.rng.exhausted()
    monkeypatch.setattr(generations, "ACTIVE", frozenset({FEATURE}))
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    assert final == await play.store.replay(cid)


@pytest.mark.parametrize("carrier", ["symptoms", "hazard", "atmosphere", "toxin"])
@pytest.mark.parametrize("actor_id,active", [("b", False), ("a", True)])
async def test_expired_or_other_actor_coughing_does_not_penalize_reaction(
    tmp_path: Path,
    carrier: str,
    actor_id: str,
    active: bool,
) -> None:
    cid, play = await reaction_fixture(tmp_path, "sqlite", False, ())
    state = coughing(play._load(await play.store.read(cid)), carrier, actor_id, active)
    with combat_generation(frozenset({FEATURE})):
        assert modifiers(state.resources, "b") == ()
