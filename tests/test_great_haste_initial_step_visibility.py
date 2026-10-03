"""Initial declaration binding never replaces actual sight at the casting pose."""

from pathlib import Path

import pytest
from test_great_haste_initial_step import initial
from test_great_haste_named_step import prepare_named

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.visibility import visible_actors
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.initial_step_binding import preparing
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.errors import ValidationError
from wayfarer.orchestration.great_haste import GreatHasteService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_initial_unseen_to_visible_binds_only_final_plain_cast_pose(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=True, basic_move=21)
    state = play._load(await play.store.read(cid))
    assert "b" not in visible_actors(
        state, state.encounters[0], "a", board=play.rules_context.hex_map(state.encounters[0])
    )
    selected = initial(
        state.revision, named=False, path=(Hex(q=0, r=1), Hex(q=1, r=1), Hex(q=2, r=1))
    )
    play.rng = RecordedDice([])
    receipt = await GreatHasteService(play).execute(cid, selected, principal_id="alice")
    current = play._load(await play.store.read(cid))
    assert receipt.outcome == "casting" and receipt.energy_spent == 0
    assert "b" in visible_actors(
        current, current.encounters[0], "a", board=play.rules_context.hex_map(current.encounters[0])
    )
    assert latest(current.resources)["larger"].skill == 14
    assert play.rng.exhausted() and not preparing()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_initial_final_unseen_requires_named_source_or_rolls_back(
    tmp_path: Path, backend: str, named: bool
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=True, basic_move=11)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    selected = initial(state.revision, named=named, path=(Hex(q=0, r=1),))
    play.rng = RecordedDice([])
    if named:
        receipt = await GreatHasteService(play).execute(cid, selected, principal_id="alice")
        current = play._load(await play.store.read(cid))
        assert receipt.outcome == "casting" and latest(current.resources)["larger"].skill == 8
    else:
        with pytest.raises(ValidationError, match="currently visible"):
            await GreatHasteService(play).execute(cid, selected, principal_id="alice")
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted() and not preparing()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_initial_visible_to_unseen_final_pose_rolls_back_all_movement(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import test_great_haste_named_subject as source_fixture

    original = source_fixture.board
    monkeypatch.setattr(
        source_fixture,
        "board",
        lambda blocked: original(False).model_copy(
            update={
                "cells": tuple(
                    cell.model_copy(update={"opaque_height": 3})
                    if cell.position == Hex(q=1, r=1)
                    else cell
                    for cell in original(False).cells
                )
            }
        ),
    )
    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=11)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    assert "b" in visible_actors(
        state, state.encounters[0], "a", board=play.rules_context.hex_map(state.encounters[0])
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="currently visible"):
        await GreatHasteService(play).execute(
            cid,
            initial(state.revision, named=False, path=(Hex(q=1, r=0), Hex(q=0, r=1))),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted() and not preparing()
