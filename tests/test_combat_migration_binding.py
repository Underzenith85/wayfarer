"""Committed map changes rebind without admitting arbitrary runtime policy drift."""

from pathlib import Path

import pytest
from test_tactical import migration, setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("migrated", [False, True])
async def test_map_binding_still_rejects_unmigrated_policy_change(
    tmp_path: Path, migrated: bool
) -> None:
    cid, play = await setup(tmp_path, migrate=migrated)
    campaign = await play.store.read(cid)
    dice = RecordedDice(())
    changed = play.derived(
        ActionEngine(
            play.engine.reviewer,
            play.engine.resources,
            play.engine.rules.model_copy(
                update={"maximum_wait": play.engine.rules.maximum_wait + 1}
            ),
        ),
        rng=dice,
    )
    with pytest.raises(ValidationError, match="configuration changed"):
        await CombatService(changed).execute(cid, migration(), principal_id="gm")
    assert await play.store.read(cid) == campaign and dice.exhausted()
