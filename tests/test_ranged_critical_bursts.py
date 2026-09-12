"""Independent Campaigns fourth-printing B373, B399 and B556 burst-critical cases.

Numeric expectations are handwritten from the rapid-fire, hit-location and
critical-hit tables; they are not read back from the dispatcher. Test-only weapon
statistics. First-printing/errata certification stays with the source audit.
"""

from pathlib import Path

from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.gurps_equipment import Damage, RangedMode
from wayfarer.engine.simulation.ranged_critical import RangedCritical
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def impaling() -> RangedMode:
    return weapon().model_copy(update={"damage": Damage(basis="fixed", dice=1, damage_type="imp")})


async def test_critical_burst_scores_one_critical_projectile(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=3
    )
    state = play._load(await play.store.read(cid))
    cmd = ChooseDefense(
        id="critical-burst",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
    )
    # Skill 13, three shots add no RoF bonus; a roll of 3 is critical and its
    # margin of 10 fills the declared three shots at Rcl 2. Critical table 5
    # doubles the damage of the one critical projectile only: 2x2, then 1 and 1.
    play.rng = RecordedDice([1, 1, 1, 1, 2, 2, 2, 1, 1])
    result = await CombatService(play).execute(cid, cmd, authenticated_actor_id="b")
    assert play.rng.exhausted()
    assert result.injury is not None
    assert result.injury.attack.effective_target == 13
    assert result.injury.adjudication_required is None
    assert result.injury.defense is None  # A critical hit is not defended.
    assert result.injury.critical_table == (1, 2, 2)
    assert result.injury.hits == 3
    assert result.injury.per_hit_damage == (4, 1, 1)
    assert result.injury.per_hit_injury == (4, 1, 1)
    assert result.injury.hp_after == 4
    saved = play._load(await play.store.read(cid))
    assert next(i.quantity for i in saved.resources.items if i.id == "ammo-a") == 7
    assert saved.resources.ammunition_loads[0].rounds == 3
    assert saved.encounters[0].blocked_reason is None
    record = RangedCritical.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("ranged-critical:"))
    )
    assert record.table_rolls == ((1, 2, 2),)
    assert record.trace.hits == 3
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, cmd, authenticated_actor_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_critical_burst_redirects_only_the_critical_projectile(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=impaling(),
        ranged_scene=scene(),
    )
    await load(cid, play)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        shots=3,
        hit_location="face",
    )
    state = play._load(await play.store.read(cid))
    cmd = ChooseDefense(
        id="critical-face-burst",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    # Skill 13 - 5 for the face is 8; a roll of 3 is critical and its margin of 5
    # fills three shots at Rcl 2. Critical table 6 moves the critical projectile
    # to an eye (x4 impaling); the other two keep the declared face (x2). The
    # trailing ones are the per-hit knockdown and eye-crippling checks, which all
    # succeed on a 3 and so leave posture and consciousness alone.
    play.rng = RecordedDice([1, 1, 1, 2, 2, 2, 1] + [1] * 12)
    result = await CombatService(play).execute(cid, cmd, authenticated_actor_id="b")
    assert play.rng.exhausted()
    assert result.injury is not None
    assert result.injury.attack.effective_target == 8
    assert result.injury.adjudication_required is None
    assert result.injury.critical_table == (2, 2, 2)
    assert result.injury.hits == 3
    assert result.injury.location == "right-eye"
    assert result.injury.per_hit_locations == ("right-eye", "face", "face")
    assert result.injury.per_hit_damage == (1, 1, 1)
    assert result.injury.per_hit_injury == (4, 2, 2)
    assert result.injury.hp_after == 2
    saved = play._load(await play.store.read(cid))
    assert saved.encounters[0].blocked_reason is None
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, cmd, authenticated_actor_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)
