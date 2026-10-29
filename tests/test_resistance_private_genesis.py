"""Native system producer origins cannot be forged by authored genesis."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_actions import campaign
from test_prepared_task_checks import gurps_state

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ValidationError


@pytest.mark.parametrize("prefix", ["mental-spirit-use:", "creature-combat:"])
def test_authored_genesis_cannot_seed_a_current_native_system_execution(
    tmp_path: Path, prefix: str
) -> None:
    engine, state = gurps_state()
    play = build_play(tmp_path, engine, rng=RecordedDice(()))
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(engine),
            state.world,
            state.resources.model_copy(
                update={
                    "revision": 0,
                    "events": (
                        ResourceEvent(id=prefix + "forged", kind="{}", at=0, target_id="a"),
                    ),
                    "receipts": (),
                }
            ),
            tuple(
                ActorSetup.model_validate(a.model_dump(exclude={"approval"})) for a in state.actors
            ),
        )
