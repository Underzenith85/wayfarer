"""The new initial carrier uses the canonical square mover as well as mapped hexes."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_great_haste_combat_concentration import prepare_combat
from test_great_haste_initial_step import initial
from test_great_haste_named_step import prepare_named
from test_power_maintenance_lifecycle import change

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.magic.great_haste_step_state import CastingStep
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.world import Fact
from wayfarer.errors import ValidationError
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_initial_square_step_current_budget_and_pose_range(
    tmp_path: Path, backend: str, named: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import test_great_haste_named_step as trained_fixture

    async def square_original(
        path: Path, backend: str, *, blocked: bool
    ) -> tuple[str, PlayService]:
        cid, play = await prepare_combat(path, backend)
        await change(
            cid,
            play,
            "square-named-knowledge",
            lambda state: state.model_copy(
                update={
                    "world": replace(
                        state.world,
                        facts=state.world.facts
                        + (
                            Fact(
                                "named-subject",
                                "b",
                                "name",
                                next(
                                    entity.name
                                    for entity in state.world.entities
                                    if entity.id == "b"
                                ),
                            ),
                        ),
                    ).learn("a", "named-subject")
                }
            ),
        )
        return cid, play

    monkeypatch.setattr(trained_fixture, "prepare_original", square_original)
    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=11)
    before = await play.store.read(cid)
    state = play._load(before)
    selected = initial(state.revision, named=named).model_copy(
        update={"step": CastingStep(destination=GridPoint(x=4, y=2))}
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError):
        await GreatHasteService(play).execute(
            cid,
            selected.model_copy(update={"step": CastingStep(destination=GridPoint(x=5, y=2))}),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
    receipt = await GreatHasteService(play).execute(cid, selected, principal_id="alice")
    current = play._load(await play.store.read(cid))
    assert receipt.outcome == "casting" and receipt.energy_spent == 0
    assert next(
        participant.position
        for participant in current.encounters[0].participants
        if participant.actor_id == "a"
    ) == GridPoint(x=4, y=2)
    assert latest(current.resources)["larger"].skill == 12
    assert play.rng.exhausted()
