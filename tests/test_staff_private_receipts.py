"""Private Staff execution evidence must originate from authorized commands."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_actions import campaign
from test_enchanting_projects import setup

from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ValidationError


@pytest.mark.parametrize(
    "prefix",
    (
        "staff-casting-intent:",
        "staff-casting-touch:",
        "staff-casting-invalid:",
        "casting-targeting:",
        "item-magic-loss:",
        "enchantment-rest:",
        "power-cast-origin:",
    ),
)
def test_genesis_cannot_seed_private_staff_execution(tmp_path: Path, prefix: str) -> None:
    engine, _, state = setup(tmp_path)
    resources = state.resources.model_copy(
        update={"events": (ResourceEvent(id=prefix + "forged", at=0, target_id="a", kind="{}"),)}
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        build_play(tmp_path, engine).initial_state(campaign(engine), state.world, resources, ())
