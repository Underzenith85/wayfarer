"""Shared scoring stays below the armed/barehanded defense dispatcher."""

from pathlib import Path

import pytest
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.errors import ValidationError


@pytest.mark.parametrize(
    "selected,item_id",
    [("none", None), ("dodge", None), ("block", "shield-b"), ("parry", "sword-b")],
)
async def test_standard_scores_and_unarmed_delegation_do_not_draw_dice(
    tmp_path: Path, selected: Defense, item_id: str | None
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    play.rng = RecordedDice([])
    state = play._load(await play.store.read(cid))
    before = state.model_dump_json()
    encounter = state.encounters[0]
    defender = next(p for p in encounter.participants if p.actor_id == "b")
    mode_id = "swing" if selected == "parry" else None
    expected = standard_defense_value(
        play.rules_context, state, defender, selected, item_id, parry_mode_id=mode_id
    )
    assert (
        defense_value(play.rules_context, state, defender, selected, item_id, parry_mode_id=mode_id)
        == expected
    )
    if selected in ("dodge", "parry"):
        value, used = expected
        assert value is not None
        assert unarmed_defense(
            play.rules_context, state, encounter, "b", selected, item_id, mode_id=mode_id
        ) == (
            int(value.value),
            used,
        )
    assert state.model_dump_json() == before
    assert play.rng.exhausted()


async def test_barehanded_dispatch_preserves_validation_order(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    play.rng = RecordedDice([])
    state = play._load(await play.store.read(cid))
    defender = next(p for p in state.encounters[0].participants if p.actor_id == "b")
    pinned = defender.model_copy(update={"pinned": True})
    assert defense_value(play.rules_context, state, pinned, "none") == (None, None)
    with pytest.raises(ValidationError, match="Pinned actors cannot defend"):
        defense_value(play.rules_context, state, pinned, "parry", "left-hand")
    with pytest.raises(ValidationError, match="requires a pending throw"):
        defense_value(play.rules_context, state, defender, "parry", "left-hand")
    assert play.rng.exhausted()
