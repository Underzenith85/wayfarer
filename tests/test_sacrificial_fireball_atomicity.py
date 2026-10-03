"""Current authority, private injury, rollback and exact retry across restart."""

import json
from pathlib import Path

import pytest
from support.runtime import build_play
from test_sacrificial_fireball_fixture import pending_fireball

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, generations
from wayfarer.orchestration.views import campaign_view


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_fireball_interposition_atomicity_and_private_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await pending_fireball(tmp_path, backend)
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={"id": "stale", "expected_revision": command.expected_revision - 1}
            ),
            principal_id="c",
        )
    # An admitted step and successful Dodge cannot commit if injury entropy fails.
    play.rng = RecordedDice([2, 2, 2])
    with pytest.raises(ValidationError, match="dice"):
        await CombatService(play).execute(cid, command, principal_id="c")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([2, 2, 2, 6])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    final = await play.store.read(cid)
    view = campaign_view(
        play._load(final), CampaignMember(principal_id="b", role="player", actor_ids=("b",))
    )
    assert "hp:c" not in json.dumps(view, default=str)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, command, principal_id="c") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_absent_generation_retains_historical_spell_interposition_refusal(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"missile-interposition"})
    cid, play, command = await pending_fireball(tmp_path, backend)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="specialized interposition"):
        await CombatService(play).execute(cid, command, principal_id="c")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("option", ["retreat", "double-defense"])
async def test_fireball_interposition_unavailable_choices_are_pre_randomness(
    tmp_path: Path, backend: str, option: str
) -> None:
    cid, play, command = await pending_fireball(tmp_path, backend)
    command = command.model_copy(
        update={"basic_retreat": True} if option == "retreat" else {"second_defense": "dodge"}
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="no retreat"):
        await CombatService(play).execute(cid, command, principal_id="c")
    assert await play.store.read(cid) == before and play.rng.exhausted()
