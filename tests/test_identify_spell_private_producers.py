"""Actual private Information and Enchant producers cannot yield partial answers."""

from pathlib import Path

import pytest
from support import analyze_magic
from support.identify_spell import fixture, revision
from test_haste_manufacture import begin_project

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.identify_spell_state import (
    CastIdentifySpell,
    IdentifySubject,
    ObserveIdentifySpellSubject,
)
from wayfarer.errors import ConflictError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.identify_spell import IdentifySpellService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("mode", ["analysis", "enchant-active", "enchant-completed"])
async def test_actual_unadmitted_private_producers_refuse_atomic_partial_answer(
    tmp_path: Path, backend: str, mode: str
) -> None:
    if mode == "analysis":
        cid, play, _ = await analyze_magic.fixture(tmp_path, backend)
        await analyze_magic.start(play, cid)
        await analyze_magic.work(play, cid)
        await analyze_magic.complete(play, cid, 1)
        target = "c"
    else:
        cid, play, _ = await fixture(tmp_path, backend)
        settle = await begin_project(play, cid, 1)
        if mode == "enchant-completed":
            play.seeds = lambda: f"{1119:064x}"
            await EnchantmentService(play).execute(cid, settle, principal_id="gm")
        target = "a"
    service = IdentifySpellService(play)
    await service.execute(
        cid,
        ObserveIdentifySpellSubject(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=IdentifySubject(id="subject", caster_id="c", subject_id=target),
        ),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="unsupported"):
        await service.execute(
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
