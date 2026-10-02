"""No authority/stale/RNG failure can commit partial interposition or drug injury."""

import json
from pathlib import Path

import pytest
from test_sacrificial_dart_fixture import pending_dart

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.ranged_equipment import FollowUpSpec
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.views import campaign_view


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_refusal_rollback_private_projection_and_exact_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await pending_dart(tmp_path, backend)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={"id": "stale-protect", "expected_revision": command.expected_revision - 1}
            ),
            principal_id="c",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()
    # Exhaust after the admitted step and carrier injury, before the drug's HT dice.
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2, 6])
    with pytest.raises(ValidationError, match="dice|random", check=lambda e: bool(str(e))):
        await CombatService(play).execute(cid, command, principal_id="c")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2, 6, 4, 4, 4])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    final = await play.store.read(cid)
    state = play._load(final)
    view = campaign_view(state, CampaignMember(principal_id="b", role="player", actor_ids=("b",)))
    encoded = json.dumps(view, default=str)
    assert "hp:c" not in encoded and "resistance_target" not in encoded
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="c") == result
    assert await play.store.read(cid) == final and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_other_linked_payloads_remain_unavailable(tmp_path: Path, backend: str) -> None:
    linked = FollowUpSpec(
        payload_id="electrical",
        kind="affliction",
        resistance_penalty=-4,
        condition="stun",
        requires_penetration=False,
    )
    cid, play, command = await pending_dart(tmp_path, backend, payload=linked)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="specialized interposition"):
        await CombatService(play).execute(cid, command, principal_id="c")
    assert await play.store.read(cid) == before and play.rng.exhausted()
