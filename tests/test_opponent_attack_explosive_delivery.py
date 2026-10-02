"""B66/B414: selected delivery check controls real scatter and one spent grenade."""

from pathlib import Path

import pytest
from test_area_attacks import grenade
from test_gurps_melee import setup
from test_opponent_attack_host import begin, choose
from test_opponent_attack_routes import enroll, luck_source

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.explosion import ExplosionSpec
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
from wayfarer.engine.simulation.combat.encounter import RangedSituation
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "aim_x,squared,selected,following,scatter",
    [
        (1, False, (3, 3, 3), (), 0),
        (5, False, (5, 5, 6), (3,), 1),
        (10, True, (5, 5, 6), (3,), 5),
    ],
)
async def test_worst_explosive_delivery_keeps_source_range_scatter_and_spent_instance(
    tmp_path: Path,
    backend: str,
    aim_x: int,
    squared: bool,
    selected: tuple[int, int, int],
    following: tuple[int, ...],
    scatter: int,
) -> None:
    definition, purchase = luck_source()
    cid, initial = await setup(
        tmp_path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        ranged_mode=grenade(),
        ranged_scene=(RangedSituation(attacker_id="a", defender_id="b", distance_yards=aim_x),),
        warhead=ExplosionSpec(dice=1),
        battlefield=Battlefield(id="dock", location_id="dock", width=20, height=12),
        placements=(
            Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            Placement(actor_id="b", position=GridPoint(x=aim_x, y=0), facing="west"),
        ),
    )
    play = await enroll(tmp_path, backend, cid, initial)
    state = play._load(await play.store.read(cid))
    point = GroundPosition(encounter_id="fight", geometry="grid", x=aim_x, y=0)
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="launch",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
            area_aim_point=point,
            scatter_squared=squared,
        ),
        principal_id="a",
    )
    declared = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 1))
    _, opened = await begin(play, cid)
    prepared = play._load(await play.store.read(cid))
    assert prepared.resources.items == declared.resources.items
    assert not blasts(prepared.resources)
    play.rng = RecordedDice((2, 2, 2) + selected + following)
    command, result = await choose(play, cid, opened.pending_id, principal="b")
    assert result.check and result.check.dice == selected
    after = play._load(await play.store.read(cid))
    blast = blasts(after.resources)[0]
    assert blast.attack_range == aim_x and blast.attack_dice == selected
    assert blast.scatter_distance == scatter and blast.scatter_direction == (3 if scatter else None)
    assert blast.center == point.model_copy(update={"x": aim_x + scatter})
    assert blast.direct_actor_id is None
    assert not any(item.id == "sword-a" for item in after.resources.items)
    spent = [item for item in after.resources.expended_items if item.id == "sword-a"]
    assert len(spent) == 1 and spent[0].ground == blast.center
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10
    assert play.rng.exhausted()
    from wayfarer.orchestration.tasks import TaskService

    assert await TaskService(play).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)
