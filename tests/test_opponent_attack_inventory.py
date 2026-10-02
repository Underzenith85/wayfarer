"""B66 selects inventory attacks before canonical B374 defenses and B373 hits."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon
from test_opponent_attack_host import begin, choose

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService


async def fixture(path: Path, backend: str, *, ranged: bool) -> tuple[str, PlayService]:
    luck = next(d for d in candidate_package().definitions if d.id == "trait:advantage:luck")
    luck = replace(luck, source_id=source("gurps-basic-set-4e-2004").id)
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        extra_definitions=(luck,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        extra_purchases=(
            Purchase(definition_id=luck.id, trait=TraitOptions(parameters=(("point-cost", 15),))),
        ),
        ranged_mode=weapon() if ranged else None,
        ranged_scene=scene() if ranged else (),
    )
    if ranged:
        await load(cid, original)
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "ranged,total,hits,damage,following",
    [
        (False, 15, 0, 0, ()),
        (False, 9, 1, 4, (2,)),
        (True, 15, 0, 0, ()),
        (True, 10, 3, 3, (1, 1, 1)),
    ],
)
async def test_selected_inventory_attack_changes_real_hit_count_damage_and_paid_ammunition(
    tmp_path: Path,
    backend: str,
    ranged: bool,
    total: int,
    hits: int,
    damage: int,
    following: tuple[int, ...],
) -> None:
    cid, play = await fixture(tmp_path, backend, ranged=ranged)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged" if ranged else "swing",
        shots=6 if ranged else 1,
    )
    declared = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 1))
    _, original = await begin(play, cid, principal="gm")
    after_original = play._load(await play.store.read(cid))
    assert after_original.resources.items == declared.resources.items
    assert after_original.resources.ammunition_loads == declared.resources.ammunition_loads
    assert after_original.resources.pools == declared.resources.pools
    assert original.check and original.check.effective_target == (14 if ranged else 13)
    second = (3, 3, 4) if total == 10 else (total // 3,) * 3
    play.rng = RecordedDice((2, 2, 2) + second + following)
    _, result = await choose(play, cid, original.pending_id, principal="b")
    assert result.check and result.check.total == total and result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.injury == damage
    assert combat.injury.hp_after == 10 - damage
    if ranged:
        assert combat.injury.hits == hits
        assert not play._load(await play.store.read(cid)).resources.ammunition_loads
    assert play.rng.exhausted()
    assert snapshot(play._load(await play.store.read(cid))).pending is None
    assert await play.store.read(cid) == await play.store.replay(cid)
