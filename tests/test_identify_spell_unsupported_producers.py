"""Actual unsupported private producers refuse incomplete B249 information."""

from pathlib import Path

import pytest
from support.identify_spell import fixture, revision

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.simulation.magic.apportation_state import (
    ApportationChannel,
    CastApportation,
    DeclareApportationChannel,
)
from wayfarer.engine.simulation.magic.great_haste_state import (
    CastGreatHaste,
    DeclareGreatHasteChannel,
    GreatHasteChannel,
)
from wayfarer.engine.simulation.magic.identify_spell_state import (
    CastIdentifySpell,
    IdentifySubject,
    ObserveIdentifySpellSubject,
)
from wayfarer.errors import ConflictError
from wayfarer.orchestration.apportation import ApportationService
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.identify_spell import IdentifySpellService


class NoDice:
    def randbelow(self, upper: int) -> int:
        raise AssertionError("Unexpected RNG draw")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("spell", ["great-haste", "apportation"])
async def test_actual_private_casting_producer_fails_closed(
    tmp_path: Path, spell: str, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if spell == "great-haste":
        import support.identify_spell as support

        from wayfarer.engine.rules.magic.movement import package

        monkeypatch.setattr(support, "movement_package", lambda: package(great_haste=True))
    cid, play, _ = await fixture(
        tmp_path, backend, extra_purchases=(Purchase(definition_id="spell:" + spell),)
    )
    host: GreatHasteService | ApportationService
    declare: DeclareGreatHasteChannel | DeclareApportationChannel
    if spell == "great-haste":
        host = GreatHasteService(play)
        declare = DeclareGreatHasteChannel(
            id="real-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=GreatHasteChannel(
                id="real-private", actor_id="a", target_id="b", location_id="dock"
            ),
        )
    else:
        host = ApportationService(play)
        declare = DeclareApportationChannel(
            id="real-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=ApportationChannel(
                id="real-private",
                actor_id="a",
                target_id="b",
                location_id="dock",
                body_weight_millipounds=150000,
            ),
        )
    await host.execute(cid, declare, principal_id="gm")
    cls = CastGreatHaste if spell == "great-haste" else CastApportation
    await host.execute(
        cid,
        cls(
            id="actual-start",
            actor_id="a",
            expected_revision=await revision(play, cid),
            operation="start",
            channel_id="real-private",
            cast_id="actual-private",
        ),
        principal_id="alice",
    )
    service = IdentifySpellService(play)
    await service.execute(
        cid,
        ObserveIdentifySpellSubject(
            id="observe",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=IdentifySubject(id="subject", caster_id="c", subject_id="a"),
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    play.rng = NoDice()
    with pytest.raises(ConflictError, match="unsupported casting producer"):
        await service.execute(
            cid,
            CastIdentifySpell(
                id="identify",
                actor_id="c",
                expected_revision=await revision(play, cid),
                subject_id="subject",
                cast_id="identification",
            ),
            principal_id="cora",
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == stream
