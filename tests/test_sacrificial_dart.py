"""B375/B279 carrier and payload go to the current protecting victim exactly once."""

import json
from pathlib import Path

import pytest
from test_sacrificial_dart_fixture import pending_dart

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dr,damage,injury,drug", [(0, 6, 2, True), (1, 6, 1, True), (1, 4, 0, False)]
)
async def test_current_protector_hp_dr_ht_and_payload(
    tmp_path: Path,
    backend: str,
    dr: int,
    damage: int,
    injury: int,
    drug: bool,
) -> None:
    cid, play, command = await pending_dart(tmp_path, backend, dr=dr)
    before = play._load(await play.store.read(cid))
    rounds = before.resources.ammunition_loads[0].rounds
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2, damage, *([4, 4, 4] if drug else [])])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    state = play._load(await play.store.read(cid))
    assert result.injury is not None and result.injury.injury == injury
    hp = {p.id: p for p in state.resources.pools}
    assert hp["hp:c"].current == 20 - injury and hp["hp:b"].current == 10
    assert hp["hp:c"].injury and hp["hp:c"].injury.unconscious == drug
    assert hp["hp:b"].injury and not hp["hp:b"].injury.unconscious
    events = [e for e in state.resources.events if ":follow-up:" in e.id]
    assert len(events) == 1 and events[0].target_id == "c"
    payload = json.loads(events[0].kind)
    assert payload["applied"] == drug
    if drug:
        assert payload["resistance_target"] == 5 and payload["duration_seconds"] == 420
    else:
        assert payload["reason"] == "carrier-stopped"
    assert state.resources.ammunition_loads[0].rounds == rounds - 1
    assert play.rng.exhausted()
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="c") == result
    assert play.rng.exhausted()
