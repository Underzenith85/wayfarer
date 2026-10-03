"""A paused casting Step cannot have its canonical immutable mana channel replaced."""

from pathlib import Path

import pytest
from test_great_haste_named_step_wait import paused_named_step

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.great_haste_state import DeclareGreatHasteChannel, channels
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_ritual_step_mana_channel_is_immutable_and_resume_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await paused_named_step(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="ritual-wait-decline",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    channel = next(channel for channel in channels(state.resources) if channel.id == "great-haste")
    assert channel.mana == "normal"
    play.rng = RecordedDice([])
    with pytest.raises(ConflictError, match="channels cannot be replaced"):
        await GreatHasteService(play).execute(
            cid,
            DeclareGreatHasteChannel(
                id="replace-paused-mana",
                actor_id="gm",
                expected_revision=state.revision,
                channel=channel.model_copy(update={"mana": "low"}),
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert play.rng.exhausted()
    restarted = PlayService(play.store, play.engine)
    restarted.rng = RecordedDice([1, 1, 3])
    resume = ResumeInterruptedTurn(
        id="ritual-resume", actor_id="a", expected_revision=state.revision, encounter_id="fight"
    )
    receipt = await CombatService(restarted).execute(cid, resume, principal_id="a")
    final = await play.store.read(cid)
    current = restarted._load(final)
    assert latest(current.resources)["named"].phase == "active"
    assert next(pool.current for pool in current.resources.pools if pool.id == "fp:a") == 6
    assert restarted.rng.exhausted()
    retry = PlayService(play.store, play.engine)
    retry.rng = RecordedDice([])
    assert await CombatService(retry).execute(cid, resume, principal_id="a") == receipt
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    assert retry._load(final).resources.game_time == current.resources.game_time
