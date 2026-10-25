"""B421 propagation/exemptions from existing lawful condition producers."""

from typing import Literal

import pytest
from test_hazard_variants import schedule
from test_multiple_symptoms import deliver, twelve_hp
from test_survival import context
from test_survival import seed as survival_seed
from test_symptoms import selection
from test_toxins import state as toxin_state

from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.environment import poison_spec
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.rules.types.survival import SurvivalStatus
from wayfarer.engine.rules.types.toxin import DeliveryEvidence, ToxinProfile
from wayfarer.engine.simulation.health.condition_checks import check_modifiers, definition_modifiers
from wayfarer.engine.simulation.health.condition_generations import condition_generation
from wayfarer.engine.simulation.health.hazards import HazardCommand, apply_hazard
from wayfarer.engine.simulation.health.survival import SettleSurvival, settle_survival
from wayfarer.engine.simulation.health.toxins import (
    DrinkCommand,
    ToxinCommand,
    apply_drinking,
    apply_toxin,
)
from wayfarer.engine.simulation.resources import ResourceState

Family = Literal[
    "symptoms", "tear-gas", "cobra", "toxin-cough", "toxin-drowsy", "tipsy", "drunk", "sleep"
]
FAMILIES: tuple[Family, ...] = (
    "symptoms",
    "tear-gas",
    "cobra",
    "toxin-cough",
    "toxin-drowsy",
    "tipsy",
    "drunk",
    "sleep",
)


def produced(family: Family) -> tuple[ResourceState, str]:
    if family == "symptoms":
        return deliver(
            twelve_hp(), 5, identifier="hit", selections=(selection("coughing", "one-third"),)
        ), "b"
    if family in ("tear-gas", "cobra"):
        spec = poison_spec(
            "tear-gas-respiratory" if family == "tear-gas" else "cobra-venom",
            id="poison",
            scene_id="room",
            exposure_seconds=60,
        )
        state, _ = apply_hazard(
            toxin_state(),
            HazardCommand(
                id="enter", actor_id="a", expected_revision=0, kind="enter", hazard_id=spec.id
            ),
            schedule(spec),
            rng=RecordedDice(()),
            system=True,
        )
        state = state.model_copy(update={"game_time": spec.delay})
        state, hazard_result = apply_hazard(
            state,
            HazardCommand(
                id="resolve", actor_id="a", expected_revision=1, kind="resolve", hazard_id=spec.id
            ),
            state.hazards[0],
            rng=RecordedDice((4, 4, 4) + ((2, 2) if family == "cobra" else ())),
            system=True,
        )
        assert hazard_result.check and not hazard_result.check.outcome.succeeded
        return state, "a"
    if family in ("toxin-cough", "toxin-drowsy"):
        profile = ToxinProfile(
            id="scenario-irritant",
            vector="respiratory",
            condition="coughing" if family == "toxin-cough" else "drowsy",
            condition_seconds=60,
            reference="B438-B440",
        )
        state, _ = apply_toxin(
            toxin_state(),
            ToxinCommand(
                id="enter", actor_id="a", expected_revision=0, kind="expose", exposure_id="dose"
            ),
            profile=profile,
            evidence=DeliveryEvidence(inhaled=True),
            ht=10,
            rng=RecordedDice(()),
            system=True,
        )
        state, toxin_result = apply_toxin(
            state,
            ToxinCommand(
                id="resolve", actor_id="a", expected_revision=1, kind="resolve", exposure_id="dose"
            ),
            rng=RecordedDice((4, 4, 4)),
            system=True,
        )
        assert not toxin_result.resisted
        return state, "a"
    if family in ("tipsy", "drunk"):
        state, intoxication = apply_drinking(
            toxin_state(),
            DrinkCommand(id="drink", actor_id="a", expected_revision=0, kind="drink", drinks=4),
            st=10,
            ht=10,
            rng=RecordedDice((5, 5, 5)),
            system=True,
        )
        if family == "drunk":
            state, intoxication = apply_drinking(
                state,
                DrinkCommand(id="more", actor_id="a", expected_revision=1, kind="drink", drinks=1),
                st=10,
                ht=10,
                rng=RecordedDice((5, 5, 5, 3, 3, 3)),
                system=True,
            )
        assert intoxication.level == family
        return state, "a"
    state = survival_seed(fp=6)
    state = state.model_copy(
        update={
            "game_time": 57600,
            "survival": (
                SurvivalStatus(
                    actor_id="a",
                    started=0,
                    next_meal_due=100000,
                    next_water_due=100000,
                    awake_since=0,
                    next_sleep_due=57600,
                    water_day_started=0,
                ),
            ),
            "pools": tuple(
                p.model_copy(
                    update={"fatigue": FatigueStatus(profile_id="gurps-basic-set-4e-2004", sleep=4)}
                )
                if p.id == "fp:a"
                else p
                for p in state.pools
            ),
        }
    )
    state, _ = settle_survival(
        state,
        SettleSurvival(id="missed-sleep", actor_id="a", expected_revision=0),
        context(),
        rng=RecordedDice(()),
        system=True,
    )
    state = state.model_copy(update={"game_time": state.survival[0].next_drowsiness_due})
    state, survival_result = settle_survival(
        state,
        SettleSurvival(id="drowsy", actor_id="a", expected_revision=1),
        context(active=False),
        rng=RecordedDice((3, 3, 3)),
        system=True,
    )
    assert survival_result.drowsiness_check and survival_result.drowsiness_check.outcome.succeeded
    return state, "a"


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("current", [False, True])
def test_source_produced_attribute_penalties_and_defensive_exemptions(
    family: Family, current: bool
) -> None:
    state, actor = produced(family)
    # Printed B428/B439 values; these do not come from the resolver under test.
    ordinary = {
        "symptoms": (-3, -1, -1, -1),
        "tear-gas": (-3, -1, -1, -1),
        "cobra": (-2, 0, 0, 0),
        "toxin-cough": (-3, -1, -1, -1),
        "toxin-drowsy": (-2, -2, -2, -2),
        "tipsy": (-1, -1, -1, -1),
        "drunk": (-2, -2, -2, -2),
        "sleep": (-2, -2, -2, -2),
    }[family]
    historical = (0, -1, 0, 0) if family == "toxin-drowsy" else ordinary[:2] + (0, 0)
    expected = ordinary if current else historical
    with condition_generation(current):
        assert (
            tuple(
                sum(m.value for m in check_modifiers(state, actor, attribute))
                for attribute in ("dx", "iq", "per", "will")
            )
            == expected
        )
        assert check_modifiers(state, actor, "ht") == check_modifiers(state, actor, "st") == ()
        defense = (0, 0, 0, 0) if current or family == "symptoms" else historical
        assert (
            tuple(
                sum(m.value for m in check_modifiers(state, actor, attribute, defensive=True))
                for attribute in ("dx", "iq", "per", "will")
            )
            == defense
        )
        for attribute, amount in (
            (ControllingAttribute.PER, expected[2]),
            (ControllingAttribute.WILL, expected[3]),
        ):
            definition = RuleDefinition(
                "skill:condition-probe",
                DefinitionKind.SKILL,
                "Condition probe",
                "fixture",
                None,
                ImplementationStatus.IMPLEMENTED,
                skill=SkillSpec(attribute, Difficulty.AVERAGE, "B421"),
            )
            assert (
                sum(
                    m.value
                    for m in definition_modifiers(
                        state, actor, definition.id, {definition.id: definition}
                    )
                )
                == amount
            )


@pytest.mark.parametrize("family", ["tear-gas", "toxin-cough", "toxin-drowsy", "sleep"])
def test_timed_condition_penalty_ends_at_exact_authoritative_expiry(family: Family) -> None:
    state, actor = produced(family)
    due = (
        state.hazards[0].affliction_until
        if family == "tear-gas"
        else state.survival[0].drowsy_until
        if family == "sleep"
        else state.toxins[0].condition_until
    )
    assert due is not None and due > state.game_time
    before = state.model_copy(update={"game_time": due - 1})
    ended = state.model_copy(update={"game_time": due})
    assert check_modifiers(before, actor, "per")
    assert check_modifiers(ended, actor, "per") == ()
