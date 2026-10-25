"""B551 selected-source posture consequences through actual turn producers."""

from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene, weapon
from test_unarmed import action, defend
from test_unarmed import setup as unarmed_setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.tables.combat import Posture
from wayfarer.engine.simulation.equipment.catalog import Damage, MeleeMode, Parry
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.tactical_view.preview import preview

POSTURES: tuple[tuple[Posture, int], ...] = (
    ("standing", 0),
    ("crouching", -2),
    ("kneeling", -2),
    ("crawling", -4),
    ("sitting", -2),
    ("prone", -4),
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture,modifier", POSTURES)
async def test_actual_weapon_attack_posture_and_reach(
    tmp_path: Path, backend: str, posture: Posture, modifier: int
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    if posture == "crouching":
        await turn(cid, play, "a", "do_nothing", crouch="before")
    elif posture != "standing":
        await turn(cid, play, "a", "change_posture", posture=posture)
    else:
        await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    preview_state = play._load(before)
    preview_command = TakeCombatTurn(
        id="source-attack-preview",
        actor_id="a",
        expected_revision=preview_state.revision,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        mode_id="thrust",
        target_id="b",
    )
    if posture == "crawling":
        with pytest.raises(ValidationError, match="[Cc]rawl|[Rr]each C"):
            preview(play, preview_state, preview_state.encounters[0], preview_command)
        with pytest.raises(ValidationError, match="[Cc]rawl|[Rr]each C"):
            await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="thrust", target_id="b")
        assert await play.store.read(cid) == before
        assert play.rng.exhausted()
        return
    preview(play, preview_state, preview_state.encounters[0], preview_command)
    assert play.rng.exhausted() and await play.store.read(cid) == before
    await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="thrust", target_id="b")
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="source-posture-defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    prepared, history = await play.store.read(cid), await play.store.history(cid)
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={"id": "stale-posture", "expected_revision": command.expected_revision - 1}
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == prepared and await play.store.history(cid) == history
    assert play.rng.exhausted()
    play.rng = RecordedDice([3, 3, 3, 3])
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury
    assert result.injury.attack.effective_target == 13 + modifier
    assert result.injury.attack.outcome.succeeded
    assert result.injury.injury == 2
    assert play.rng.exhausted()
    final = await play.store.read(cid)
    resumed = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    assert await CombatService(resumed).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("close", [False, True])
async def test_crawling_dual_reach_weapon_requires_actual_close_distance(
    tmp_path: Path, backend: str, close: bool
) -> None:
    mode = MeleeMode(
        id="close-thrust",
        skill_id="skill:broadsword",
        minimum_st=10,
        damage=Damage(basis="thrust", adds=1, damage_type="cr"),
        reach=(0, 1),
        parry=Parry(),
    )
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True, melee_modes=(mode,))
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    if close:
        await action(cid, play, "a", "punch", hands=("left-hand",), enter=True)
        play.rng = RecordedDice([3, 3, 3, 2, 2, 2])
        await defend(cid, play, defense="dodge")
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "change_posture", posture="crawling")
    await turn(cid, play, "b", "do_nothing")
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    if not close:
        with pytest.raises(ValidationError, match="Crawling permits only Reach C"):
            await turn(cid, play, "a", "attack", item_id="sword-a", mode_id=mode.id, target_id="b")
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        assert play.rng.exhausted()
        return
    await turn(cid, play, "a", "attack", item_id="sword-a", mode_id=mode.id, target_id="b")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3, 3])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="crawl-close-defense",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.attack.effective_target == 9
    assert result.injury.injury == 2
    assert play.rng.exhausted()


@pytest.mark.parametrize("posture,modifier", POSTURES)
async def test_ranged_attack_does_not_receive_melee_posture_penalty(
    tmp_path: Path, posture: Posture, modifier: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_fixture=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    if posture == "crouching":
        await turn(cid, play, "a", "do_nothing", crouch="before")
    elif posture != "standing":
        await turn(cid, play, "a", "change_posture", posture=posture)
    else:
        await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="ranged", target_id="b")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3, 2])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="ranged-posture-defense",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.attack.effective_target == 13
    assert result.injury.injury == 2
    assert play.rng.exhausted()


@pytest.mark.parametrize("posture,modifier", POSTURES)
async def test_feint_contest_retains_skill_in_every_posture(
    tmp_path: Path, posture: Posture, modifier: int
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    if posture == "crouching":
        await turn(cid, play, "a", "do_nothing", crouch="before")
    elif posture != "standing":
        await turn(cid, play, "a", "change_posture", posture=posture)
    else:
        await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    play.rng = RecordedDice([3, 3, 3, 3, 3, 4])
    await turn(cid, play, "a", "feint", item_id="sword-a", mode_id="thrust", target_id="b")
    state = play._load(await play.store.read(cid))
    rolls = state.encounters[0].participants[0].maneuver_state.feint_rolls
    assert rolls[0].effective_target == 13
    assert rolls[1].effective_target == 13
    assert state.encounters[0].participants[0].maneuver_state.feint_penalty == 1
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture,modifier", POSTURES)
async def test_actual_close_punch_uses_posture_once_including_crawling(
    tmp_path: Path, backend: str, posture: Posture, modifier: int
) -> None:
    cid, play = await unarmed_setup(tmp_path)
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    # An actual punch entry establishes shared close combat while standing.
    # Lowering posture later does not fabricate movement or a relationship.
    await action(cid, play, "a", "punch", hands=("left-hand",), enter=True)
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2])
    await defend(cid, play, defense="dodge")
    await turn(cid, play, "b", "do_nothing")
    if posture == "crouching":
        await turn(cid, play, "a", "do_nothing", crouch="before")
    elif posture != "standing":
        await turn(cid, play, "a", "change_posture", posture=posture)
    else:
        await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    await action(cid, play, "a", "punch", hands=("left-hand",))
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="close-posture-defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    hit = 10 + modifier >= 7
    play.rng = RecordedDice([2, 2, 3] + ([5] if hit else []))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.unarmed
    assert result.unarmed.checks[0].effective_target == 10 + modifier
    assert result.unarmed.checks[0].outcome.succeeded == hit
    assert result.unarmed.injury == (2 if hit else 0)
    assert play.rng.exhausted()
    updated = play._load(await play.store.read(cid))
    assert next(p.current for p in updated.resources.pools if p.id == "hp:b") == (8 if hit else 10)
    assert updated.encounters[0].participants[0].posture == posture


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture,modifier", POSTURES)
async def test_b370_takedown_explicitly_uses_usual_melee_posture_penalty(
    tmp_path: Path, backend: str, posture: Posture, modifier: int
) -> None:
    cid, play = await unarmed_setup(tmp_path)
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await action(cid, play, "a", "punch", hands=("left-hand",), enter=True)
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2])
    await defend(cid, play, defense="dodge")
    await turn(cid, play, "b", "do_nothing")
    if posture == "crouching":
        await turn(cid, play, "a", "do_nothing", crouch="before")
    elif posture != "standing":
        await turn(cid, play, "a", "change_posture", posture=posture)
    else:
        await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    await action(
        cid,
        play,
        "a",
        "grapple",
        hands=("left-hand", "right-hand"),
        skill="skill:wrestling",
    )
    play.rng = RecordedDice([2, 2, 2])
    await defend(cid, play)
    state = play._load(await play.store.read(cid))
    grip = state.encounters[0].grips[0]
    await turn(cid, play, "b", "do_nothing")
    play.rng = RecordedDice([3, 3, 3, 3, 3, 4])
    await action(cid, play, "a", "takedown", grip=grip.id)
    state = play._load(await play.store.read(cid))
    record = state.encounters[0].unarmed_history[-1]
    assert record.action == "takedown"
    assert record.checks[0].effective_target == 11 + modifier
    assert record.checks[1].effective_target == 11
    assert record.won == (modifier == 0)
    assert state.encounters[0].participants[1 if record.won else 0].posture == "prone"
    assert play.rng.exhausted()
