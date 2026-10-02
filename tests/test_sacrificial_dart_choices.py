"""B375/B377 success, retained friend choice and critical carrier consequences."""

import json
from pathlib import Path

import pytest
from test_sacrificial_dart_fixture import pending_dart

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.orchestration.combat import ChooseDefense, CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice", ["failure", "drop-safe", "drop-hit", "critical"])
async def test_sacrificial_dart_choices(tmp_path: Path, backend: str, choice: str) -> None:
    cid, play, command = await pending_dart(tmp_path, backend)
    initial = play._load(await play.store.read(cid))
    rounds = initial.resources.ammunition_loads[0].rounds
    if choice == "failure":
        play.rng = RecordedDice([3, 3, 3, 4, 4, 4])
        result = await CombatService(play).execute(cid, command, principal_id="c")
        failed_result = result
        failed = play._load(await play.store.read(cid))
        assert result.code == "combat.sacrificial_failed"
        assert failed.resources.ammunition_loads[0].rounds == rounds
        assert not any(":follow-up:" in e.id for e in failed.resources.events)
        pending = failed.encounters[0].pending_defense
        assert pending and pending.defender_id == "b" and pending.attack_roll
        assert pending.attack_roll.dice == (3, 3, 3)
        play.rng = RecordedDice([6, 4, 4, 4])
        result = await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="friend",
                actor_id="b",
                expected_revision=failed.revision,
                encounter_id="fight",
                defense="none",
            ),
            principal_id="b",
        )
    elif choice == "critical":
        play.rng = RecordedDice([1, 1, 1, 3, 3, 3, 6, 4, 4, 4])
        result = await CombatService(play).execute(cid, command, principal_id="c")
        assert result.injury and result.injury.defense is None
    else:
        command = command.model_copy(update={"dodge_and_drop": True})
        play.rng = RecordedDice(
            [
                3,
                3,
                3,
                *([2, 3, 3] if choice == "drop-safe" else [3, 3, 3]),
                *([] if choice == "drop-safe" else [6, 4, 4, 4]),
            ]
        )
        result = await CombatService(play).execute(cid, command, principal_id="c")
    state = play._load(await play.store.read(cid))
    hp = {p.id: p for p in state.resources.pools}
    events = [e for e in state.resources.events if ":follow-up:" in e.id]
    if choice == "drop-safe":
        assert hp["hp:b"].current == 10 and hp["hp:c"].current == 20 and not events
    else:
        victim = "b" if choice in ("failure", "critical") else "c"
        assert len(events) == 1 and events[0].target_id == victim
        payload = json.loads(events[0].kind)
        assert payload["resistance_target"] == (7 if victim == "b" else 5)
        assert payload["duration_seconds"] == (300 if victim == "b" else 420)
        injured = hp["hp:" + victim].injury
        assert injured is not None and injured.unconscious
        other = "c" if victim == "b" else "b"
        assert hp["hp:" + other].current == (20 if other == "c" else 10)
    if choice.startswith("drop"):
        assert all(
            p.posture == "prone"
            for p in state.encounters[0].participants
            if p.actor_id in ("b", "c")
        )
    assert state.resources.ammunition_loads[0].rounds == rounds - 1 and play.rng.exhausted()
    if choice == "failure":
        final = await play.store.read(cid)
        play.rng = RecordedDice([])
        assert await CombatService(play).execute(cid, command, principal_id="c") == failed_result
        assert await play.store.read(cid) == final and play.rng.exhausted()
