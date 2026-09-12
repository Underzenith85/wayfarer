"""Old pending task records finish through the single procedure dispatcher."""

import json
from typing import Literal

import pytest
from test_recovery_variants import PROFILE, lasting_state, mortal_state

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.health.medical.commands import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
)
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.engine.simulation.resources import ResourceState


@pytest.mark.parametrize("procedure", ["trauma-maintenance", "repair-lasting"])
def test_pending_legacy_task_migrates_and_finishes(
    procedure: Literal["trauma-maintenance", "repair-lasting"],
) -> None:
    context = CareContext(PROFILE, 10, physician_skill=12, surgery_skill=12, technology_level=6)
    initial = mortal_state() if procedure == "trauma-maintenance" else lasting_state()
    state, _ = apply_recovery(
        initial,
        BeginRecovery(
            id="care",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind=procedure,
            injury_id="leg",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    encoded = json.loads(state.model_dump_json())
    task = encoded["recovery_tasks"][0]
    task.pop("procedure")
    task.pop("task_schema", None)
    encoded["game_time"] = task["due"]
    restored = ResourceState.model_validate_json(json.dumps(encoded))
    assert restored.recovery_tasks[0].procedure == procedure
    command = FinishRecovery.model_validate_json(
        json.dumps(
            {
                "kind": "finish-recovery-variant",
                "id": "finish",
                "actor_id": "b",
                "expected_revision": 1,
                "task_id": "care",
            }
        )
    )
    finished, result = apply_recovery(
        restored, command, context, rng=RecordedDice([4, 4, 4]), system=True
    )
    assert result.status == "completed"
    if procedure == "trauma-maintenance":
        assert finished.pools[0].injury is not None
        assert finished.pools[0].injury.mortal_wound_due == 7200
    else:
        assert result.repaired
    assert finished.revision == 2
    assert apply_recovery(finished, command, context, rng=RecordedDice([]), system=True) == (
        finished,
        result,
    )
