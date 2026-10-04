"""The actual resolved contact rolls back HP, permanent injury and charge together."""

from pathlib import Path

import pytest
from support.runtime import build_play
from support.wither_limb import fixture, revision
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_wither_limb_host import prepare_contact

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.wither_spell_state import casts, contact_results
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_contact_authority_stale_rollback_restart_and_exact_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await prepare_contact(play, cid)
    command = ChooseDefense(
        id="response",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="none",
    )
    saved, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    play.rng = RecordedDice(())
    for principal in ("a", "alice", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await CombatService(play).execute(cid, command, principal_id=principal)
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="b"
        )
    dice = (3, 3, 3, 1, 3, 3, 3, 4, 4, 4, 1, 3, 3, 3)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(dice))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await CombatService(failing).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    state = play._load(saved)
    assert casts(state.resources)["wither"].status == "held" and not contact_results(
        state.resources
    )
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    play.rng = RecordedDice(dice)
    result = await CombatService(play).execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    assert next(p.current for p in play._load(final).resources.pools if p.id == "hp:b") == 8
    restart = build_play(tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(()))
    assert await CombatService(restart).execute(cid, command, principal_id="b") == result
    with pytest.raises(ConflictError):
        await CombatService(restart).execute(
            cid, command.model_copy(update={"defense": "dodge"}), principal_id="b"
        )
    assert await play.store.read(cid) == final
    assert isinstance(restart.rng, RecordedDice) and restart.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_contact_cas_loser_cannot_duplicate_permanent_injury(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Callable

    from wayfarer.contracts import Campaign, CommandReceipt, TurnResult
    from wayfarer.persistence.events import CommandEntropy, CommandOrigin, CommandResolution

    cid, play, _ = await fixture(tmp_path, backend)
    await prepare_contact(play, cid)
    command = ChooseDefense(
        id="loser",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="none",
    )
    dice = (3, 3, 3, 1, 3, 3, 3, 4, 4, 4, 1, 3, 3, 3)
    competitor = build_play(
        tmp_path / "competitor", play.engine, store=play.store, rng=RecordedDice(dice)
    )
    original = play.store.commit_turn
    winners: list[Campaign] = []

    async def race(
        campaign_id: str,
        request_id: str,
        revision_number: int,
        text: str,
        resolve: Callable[[Campaign], CommandReceipt | CommandResolution],
        *,
        actor_id: str = "system",
        entropy: CommandEntropy | None = None,
        recorded_at_us: int | None = None,
        origin: CommandOrigin | None = None,
    ) -> TurnResult:
        if request_id == "loser":
            await CombatService(competitor).execute(
                cid, command.model_copy(update={"id": "winner"}), principal_id="b"
            )
            winners.append(await play.store.read(cid))
        return await original(
            campaign_id,
            request_id,
            revision_number,
            text,
            resolve,
            actor_id=actor_id,
            entropy=entropy,
            recorded_at_us=recorded_at_us,
            origin=origin,
        )

    monkeypatch.setattr(play.store, "commit_turn", race)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert winners and await play.store.read(cid) == winners[0] and play.rng.exhausted()
    state = play._load(winners[0])
    assert casts(state.resources)["wither"].status == "spent"
    assert len(contact_results(state.resources)) == 1
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.current == 8 and hp.injury is not None and len(hp.injury.lasting_injuries) == 1
