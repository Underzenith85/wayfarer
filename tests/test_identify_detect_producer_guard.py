"""Real private Regular casts cannot silently disappear from Identify answers."""

from pathlib import Path

import pytest
from support.detect_magic import complete, fixture, start, work
from support.identify_spell import revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.detect_magic_state import casts, findings
from wayfarer.engine.simulation.magic.identify_spell_state import (
    CastIdentifySpell,
    IdentifySubject,
    ObserveIdentifySpellSubject,
)
from wayfarer.errors import ConflictError
from wayfarer.orchestration.identify_spell import IdentifySpellService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("phase", ["casting", "completed"])
async def test_actual_detect_producer_refuses_partial_identify_before_rng(
    tmp_path: Path, backend: str, phase: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, "permanent")
    await start(play, cid)
    if phase == "completed":
        await work(play, cid)
        detected = await complete(play, cid, 1)
        assert detected.finding is not None and detected.finding.magical
    current = play._load(await play.store.read(cid))
    assert casts(current.resources)["detection"].status == (
        "rolled" if phase == "completed" else "casting"
    )
    if phase == "completed":
        assert any(
            f.actor_id == "c" and f.target_id == "cloak" for f in findings(current.resources)
        )
    service = IdentifySpellService(play)
    await service.execute(
        cid,
        ObserveIdentifySpellSubject(
            id="identify-physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=IdentifySubject(id="identify-subject", caster_id="c", subject_id="c"),
        ),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="unsupported.*producer"):
        await service.execute(
            cid,
            CastIdentifySpell(
                id="identify-private-detect",
                actor_id="c",
                expected_revision=await revision(play, cid),
                cast_id="identify-private-detect",
                subject_id="identify-subject",
            ),
            principal_id="cora",
        )
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == stream
    assert play.rng.exhausted()
