"""B168/B173/B178 defaults resolve into actual B484 durability transactions."""

from pathlib import Path

import pytest
from support.armoury_defaults import declare, fixture, revision, select, wait
from test_issue_818_armoury_acceptance import begin

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, EndEncounter
from wayfarer.orchestration.play import PlayService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_canonical_firearm_broken_ready_then_repaired_actual_shot(
    tmp_path: Path, backend: str
) -> None:
    from test_gurps_maneuvers import defend
    from test_gurps_maneuvers import turn as original_turn

    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.combat.encounter import RangedSituation
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.orchestration.combat import StartEncounter

    cid, play = await fixture(tmp_path, backend, "firearm", "skill:engineer-small-arms")
    current_encounter = "broken-fight"

    async def turn(
        cid: str, play: PlayService, actor: str, maneuver: str, **options: object
    ) -> None:
        await original_turn(cid, play, actor, maneuver, encounter_id=current_encounter, **options)

    async def fight(identifier: str) -> None:
        nonlocal current_encounter
        current_encounter = identifier
        await CombatService(play).execute(
            cid,
            StartEncounter(
                id=identifier,
                actor_id="gm",
                encounter_id=identifier,
                battlefield_id="dock",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=1, y=2)),
                ),
                ranged_situations=(
                    RangedSituation(
                        attacker_id="b",
                        defender_id="a",
                        distance_yards=2,
                        speed_yards_per_second=0,
                        size_modifier=0,
                    ),
                ),
                expected_revision=await revision(play, cid),
            ),
            principal_id="gm",
        )

    await fight("broken-fight")
    await turn(cid, play, "a", "do_nothing")
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="disabled|Disabled"):
        await turn(cid, play, "b", "ready", item_id="repair-target")
    assert await play.store.read(cid) == saved
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end-broken",
            actor_id="gm",
            encounter_id=current_encounter,
            reason="repair",
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )
    await declare(play, cid)
    await select(play, cid, "skill:engineer-small-arms")
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await wait(play, cid, 1800)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    item = next(
        i for i in play._load(await play.store.read(cid)).resources.items if i.id == "repair-target"
    )
    assert item.condition and item.condition.hp == 6 and not item.condition.disabled
    play.rng = RecordedDice(())
    await fight("repaired-fight")
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "ready", item_id="repair-target")
    for _ in range(20):
        await turn(cid, play, "a", "do_nothing")
        await turn(
            cid,
            play,
            "b",
            "ready",
            item_id="repair-target",
            mode_id="shot",
            reload_ammunition_id="ammo-b",
        )
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 1
    hp = next(p.current for p in state.resources.pools if p.id == "hp:a")
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "attack", item_id="repair-target", target_id="a", mode_id="shot")
    play.rng = RecordedDice((1, 1, 1, 3, 3, 3, 2, 2))
    result = await defend(cid, play, "a", "none", encounter_id=current_encounter)
    assert result.injury and result.injury.shots_fired == 1 and result.injury.hits == 1
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == hp - 4
    assert not state.resources.ammunition_loads
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "tl,source,penalty,skill",
    [(3, "attribute:iq", -1, 3), (2, "skill:armoury-melee-weapons", -3, 3)],
)
async def test_training_tl_penalty_applied_once_actual_body_armor(
    tmp_path: Path, backend: str, tl: int, source: str, penalty: int, skill: int
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", source, tl=tl)
    await declare(play, cid)
    plan = await select(play, cid, source)
    assert plan.training_tl == 4 and plan.equipment_tl == tl and plan.tl_penalty == penalty
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert task.skill == skill and task.technology_level_penalty == penalty
    await wait(play, cid, 1800)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    assert tasks(play._load(await play.store.read(cid)).resources)[0].restored_hp == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_repaired_body_armor_resumes_actual_dr_protection(
    tmp_path: Path, backend: str
) -> None:
    from test_gurps_maneuvers import defend, turn

    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.orchestration.combat import StartEncounter

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    await select(play, cid, "attribute:iq")
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await wait(play, cid, 1800)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    play.rng = RecordedDice(())
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="protected",
            actor_id="gm",
            encounter_id="protected",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=1, y=2)),
            ),
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )
    await turn(cid, play, "a", "do_nothing", encounter_id="protected")
    await turn(cid, play, "b", "ready", item_id="repair-target", encounter_id="protected")
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        encounter_id="protected",
    )
    play.rng = RecordedDice((3, 3, 3, 4))
    result = await defend(cid, play, "b", "none", encounter_id="protected")
    assert result.injury and result.injury.basic_damage == 5 and result.injury.injury == 0
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    item = next(i for i in state.resources.items if i.id == "repair-target")
    assert (
        item.condition and item.condition.hp == 1 and item.equipped and not item.condition.disabled
    )
