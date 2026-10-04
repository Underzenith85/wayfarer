"""Actual current GM speech/gesture observations prevent unsupported casting."""

from pathlib import Path

import pytest
from support.identify_spell import fixture, observe, producer, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.identify_spell_state import CastIdentifySpell
from wayfarer.engine.simulation.magic.ritual_state import DeclareRitualCapability
from wayfarer.errors import ValidationError
from wayfarer.orchestration.identify_spell import IdentifySpellService
from wayfarer.orchestration.spell_rituals import SpellRitualService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("speech", [True, False])
async def test_real_current_ritual_observation_refuses_before_dice(
    tmp_path: Path, backend: str, speech: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await producer(play, cid)
    await observe(play, cid)
    await SpellRitualService(play).execute(
        cid,
        DeclareRitualCapability(
            id="physical-ritual",
            actor_id="c",
            expected_revision=await revision(play, cid),
            speech_available=not speech,
            gesture_available=speech,
            reason="Observed current gag" if speech else "Observed hands and head immobilized",
        ),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await IdentifySpellService(play).execute(
            cid,
            CastIdentifySpell(
                id="identify",
                actor_id="c",
                expected_revision=await revision(play, cid),
                cast_id="identify",
                subject_id="subject",
            ),
            principal_id="cora",
        )
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert play.rng.exhausted()
