"""B375 ordinary interception of a B249 single Fireball missile."""

from pathlib import Path

import pytest
from test_sacrificial_fireball_fixture import pending_fireball

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_sacrificial_fireball_hits_current_protector_not_friend(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await pending_fireball(tmp_path, backend)
    play.rng = RecordedDice([2, 2, 2, 6])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    assert result.injury and result.injury.injury == 2
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == 18


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_protector_keeps_missile_and_original_attack_for_friend(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.spells import latest
    from wayfarer.orchestration.combat import ChooseDefense

    cid, play, command = await pending_fireball(tmp_path, backend)
    play.rng = RecordedDice([4, 4, 4])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    assert result.code == "combat.sacrificial_failed"
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and pending.defender_id == "b" and pending.attack_roll
    assert pending.attack_roll.dice == (3, 3, 3)
    assert latest(state.resources)["cast"].phase == "active"
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == 20
    play.rng = RecordedDice([6, 3, 3, 3])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="friend",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert (
        result.injury and result.injury.attack == pending.attack_roll and result.injury.injury == 6
    )
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["cast"].phase == "ended"
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 4
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_critical_fireball_bypasses_protector_and_uses_friend_dr(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.combat import ChooseDefense

    cid, play, command = await pending_fireball(tmp_path, backend, attack_dice=(1, 1, 1))
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="noncritical missile"):
        await CombatService(play).execute(cid, command, principal_id="c")
    assert await play.store.read(cid) == before and play.rng.exhausted()
    play.rng = RecordedDice([4, 4, 4, 6, 3, 3, 3])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="friend-critical",
            actor_id="b",
            expected_revision=command.expected_revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.defense is None and result.injury.injury == 6
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 4
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == 20
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("avoids", [True, False])
async def test_sacrificial_fireball_drop_uses_three_margin_and_both_prone(
    tmp_path: Path, backend: str, avoids: bool
) -> None:
    cid, play, command = await pending_fireball(tmp_path, backend)
    command = command.model_copy(update={"dodge_and_drop": True})
    play.rng = RecordedDice([2, 2, 3] if avoids else [3, 3, 3, 6])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    assert result.injury and result.injury.injury == (0 if avoids else 2)
    state = play._load(await play.store.read(cid))
    assert all(
        p.posture == "prone" for p in state.encounters[0].participants if p.actor_id in {"b", "c"}
    )
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == (
        20 if avoids else 18
    )
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_release_captures_attack_before_any_protector_choice(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await pending_fireball(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and pending.attack_roll and pending.attack_roll.dice == (3, 3, 3)
    assert pending.defender_id == "b" and pending.protected_defender_id is None
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_missed_fireball_refuses_interposition_before_rng_or_mutation(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import ValidationError

    cid, play, command = await pending_fireball(tmp_path, backend, attack_dice=(6, 6, 6))
    before = await play.store.read(cid)
    pending = play._load(before).encounters[0].pending_defense
    assert pending and pending.attack_roll and not pending.attack_roll.outcome.succeeded
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="noncritical missile"):
        await CombatService(play).execute(cid, command, principal_id="c")
    assert await play.store.read(cid) == before and play.rng.exhausted()
