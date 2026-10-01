"""B375 interposition redirects actual injury or preserves the friend's defense."""

from pathlib import Path

import pytest
from test_gurps_melee import attack, setup
from test_gurps_ranged import scene

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn


@pytest.mark.parametrize("ranged", [False, True])
async def test_sacrificial_success_moves_protector_and_applies_injury_to_protector(
    tmp_path: Path, ranged: bool
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        ranged_fixture=ranged,
        ranged_scene=scene() if ranged else (),
    )
    play.rng = RecordedDice([3, 3, 3])
    if ranged:
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="throw",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="throw-fixture",
                target_id="b",
            ),
            principal_id="a",
        )
    else:
        await attack(cid, play)
    seed = play._load(await play.store.read(cid))
    before = {p.id: p.current for p in seed.resources.pools}
    command = ChooseDefense(
        id="protect",
        actor_id="c",
        expected_revision=seed.revision,
        encounter_id="fight",
        defense="dodge",
        sacrificial_for="b",
    )
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2, 2])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    updated = play._load(await play.store.read(cid))
    assert result.injury is not None and result.injury.injury > 0
    assert next(p.current for p in updated.resources.pools if p.id == "hp:b") == before["hp:b"]
    assert next(p.current for p in updated.resources.pools if p.id == "hp:c") < before["hp:c"]
    assert (
        updated.encounters[0].participants[2].position
        == updated.encounters[0].participants[1].position
    )
    assert play.rng.exhausted()
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="c") == result


@pytest.mark.parametrize("ranged", [False, True])
async def test_failed_interposition_preserves_original_roll_and_friend_defense(
    tmp_path: Path, ranged: bool
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        ranged_fixture=ranged,
        ranged_scene=scene() if ranged else (),
    )
    play.rng = RecordedDice([3, 3, 3])
    if ranged:
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="throw",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="throw-fixture",
                target_id="b",
            ),
            principal_id="a",
        )
    else:
        await attack(cid, play)
    seed = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="protect",
        actor_id="c",
        expected_revision=seed.revision,
        encounter_id="fight",
        defense="dodge",
        sacrificial_for="b",
    )
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    failed = play._load(await play.store.read(cid))
    assert result.code == "combat.sacrificial_failed"
    assert failed.encounters[0].pending_defense is not None
    pending = failed.encounters[0].pending_defense
    assert pending.defender_id == "b" and pending.attack_roll is not None
    assert pending.attack_roll.dice == (3, 3, 3)
    assert failed.encounters[0].participants[2].retreat_used
    assert [p.current for p in failed.resources.pools if p.id.startswith("hp:")] == [
        p.current for p in seed.resources.pools if p.id.startswith("hp:")
    ]
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="c") == result
    # Only the friend's Dodge dice are required: the enemy cannot reroll its attack.
    play.rng = RecordedDice([2, 2, 2])
    friend = ChooseDefense(
        id="friend",
        actor_id="b",
        expected_revision=failed.revision,
        encounter_id="fight",
        defense="dodge",
    )
    resolved = await CombatService(play).execute(cid, friend, principal_id="b")
    assert resolved.injury is not None and resolved.injury.attack.dice == (3, 3, 3)
    assert resolved.injury.injury == 0
    assert play.rng.exhausted()


@pytest.mark.parametrize("ranged", [False, True])
async def test_critical_attack_hits_original_friend_and_cannot_be_intercepted(
    tmp_path: Path, ranged: bool
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        ranged_fixture=ranged,
        ranged_scene=scene() if ranged else (),
    )
    play.rng = RecordedDice([3, 3, 3])
    if ranged:
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="throw",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="throw-fixture",
                target_id="b",
            ),
            principal_id="a",
        )
    else:
        await attack(cid, play)
    seed = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="protect",
        actor_id="c",
        expected_revision=seed.revision,
        encounter_id="fight",
        defense="dodge",
        sacrificial_for="b",
    )
    play.rng = RecordedDice([1, 1, 1, 2, 2, 2, 2, 2, 2])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    updated = play._load(await play.store.read(cid))
    assert result.injury is not None and result.injury.attack.dice == (1, 1, 1)
    assert result.injury.defense is None
    assert next(p.current for p in updated.resources.pools if p.id == "hp:c") == next(
        p.current for p in seed.resources.pools if p.id == "hp:c"
    )
    assert next(p.current for p in updated.resources.pools if p.id == "hp:b") < next(
        p.current for p in seed.resources.pools if p.id == "hp:b"
    )
    assert play.rng.exhausted()


@pytest.mark.parametrize("avoid_both", [False, True])
async def test_sacrificial_dodge_and_drop_combines_ranged_bonus_and_protector_posture(
    tmp_path: Path, avoid_both: bool,
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        ranged_fixture=True,
        ranged_scene=scene(),
    )
    play.rng = RecordedDice([3, 3, 3])
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="throw",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="throw-fixture",
            target_id="b",
        ),
        principal_id="a",
    )
    seed = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="protect",
        actor_id="c",
        expected_revision=seed.revision,
        encounter_id="fight",
        defense="dodge",
        sacrificial_for="b",
        dodge_and_drop=True,
    )
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3] if avoid_both else [3, 3, 3, 3, 3, 3, 2])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    updated = play._load(await play.store.read(cid))
    assert result.injury is not None and result.injury.defense_value is not None
    assert result.injury.defense_value.value == 11  # Speed 5 +3 + drop 3, no shield.
    assert updated.encounters[0].participants[2].posture == "prone"
    assert updated.encounters[0].participants[1].posture == "prone"
    assert next(p.current for p in updated.resources.pools if p.id == "hp:b") == next(
        p.current for p in seed.resources.pools if p.id == "hp:b"
    )
    assert (result.injury.injury == 0) if avoid_both else (result.injury.injury > 0)
    assert play.rng.exhausted()


@pytest.mark.parametrize("succeeds", [False, True])
async def test_single_firearm_projectile_conserves_one_loaded_round_across_interposition(
    tmp_path: Path, succeeds: bool
) -> None:
    from test_firearm_malfunctions import firearm
    from test_gurps_maneuvers import turn

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        ranged_mode=firearm(),
        ranged_scene=scene(),
    )
    for _ in range(2):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="ranged",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
        await turn(cid, play, "c", "do_nothing")
    loaded = play._load(await play.store.read(cid))
    rounds = loaded.resources.ammunition_loads[0].rounds
    play.rng = RecordedDice([3, 3, 3])
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="shoot",
            actor_id="a",
            expected_revision=loaded.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="ranged",
            target_id="b",
            shots=1,
        ),
        principal_id="a",
    )
    seed = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="protect",
        actor_id="c",
        expected_revision=seed.revision,
        encounter_id="fight",
        defense="dodge",
        sacrificial_for="b",
    )
    play.rng = RecordedDice([3, 3, 3, *([2, 2, 2, 2] if succeeds else [4, 4, 4])])
    await CombatService(play).execute(cid, command, principal_id="c")
    updated = play._load(await play.store.read(cid))
    if not succeeds:
        assert updated.resources.ammunition_loads[0].rounds == rounds
        play.rng = RecordedDice([2, 2, 2])
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="friend",
                actor_id="b",
                expected_revision=updated.revision,
                encounter_id="fight",
                defense="dodge",
            ),
            principal_id="b",
        )
        updated = play._load(await play.store.read(cid))
    assert updated.resources.ammunition_loads[0].rounds == rounds - 1
    assert play.rng.exhausted()


async def test_interposition_authority_and_no_retreat_are_checked_before_dice(
    tmp_path: Path,
) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True, third_actor=True)
    play.rng = RecordedDice([3, 3, 3])
    await attack(cid, play)
    seed = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="protect",
        actor_id="c",
        expected_revision=seed.revision,
        encounter_id="fight",
        defense="dodge",
        sacrificial_for="b",
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ValidationError, match="no retreat"):
        await CombatService(play).execute(
            cid, command.model_copy(update={"basic_retreat": True}), principal_id="c"
        )
    assert play._load(await play.store.read(cid)).model_dump_json() == seed.model_dump_json()
    assert play.rng.exhausted()
