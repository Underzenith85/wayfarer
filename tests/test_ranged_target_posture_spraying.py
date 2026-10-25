"""B551 applies to each real B409 spray recipient under the initiating policy."""

from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_drowsy_toxins import exposure
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_spraying_fire import automatic_weapon

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.tables.combat import Posture
from wayfarer.engine.simulation.combat.abandon import AbandonPendingAttack
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import RangedSituation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.suppression import SprayTarget
from wayfarer.engine.simulation.health.toxins import ToxinCommand
from wayfarer.errors import ValidationError
from wayfarer.orchestration import ranged_target_posture_generations as generations
from wayfarer.orchestration.combat.abandon import AbandonPendingAttackService
from wayfarer.orchestration.drowsy_toxins import DrowsyToxinService
from wayfarer.orchestration.play import PlayService


async def ready(
    path: Path, backend: str, posture: Posture, *, low: bool = False
) -> tuple[str, PlayService]:
    cid, play = await setup(
        path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        trained=not low,
        extra_purchases=(Purchase(definition_id="skill:broadsword", amount=1),) if low else (),
        ranged_mode=automatic_weapon(),
        battlefield=Battlefield(id="spray-range", location_id="dock", width=10, height=3)
        if low
        else None,
        ranged_scene=(
            RangedSituation(attacker_id="a", defender_id="b", distance_yards=3),
            RangedSituation(attacker_id="a", defender_id="c", distance_yards=7 if low else 3),
        ),
        placements=(
            Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            Placement(actor_id="b", position=GridPoint(x=3, y=0), facing="west"),
            Placement(
                actor_id="c",
                position=GridPoint(x=7, y=0) if low else GridPoint(x=2, y=1),
                facing="west",
            ),
        ),
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    for _ in range(2):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="spray",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
        await turn(cid, play, "c", "do_nothing")
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "c", "change_posture", posture=posture)
    return cid, play


async def spray(cid: str, play: PlayService, *, low: bool = False) -> None:
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="spray",
        shots=3 if low else 5,
        spray_targets=(SprayTarget(target_id="c", shots=3 if low else 4),),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_second_recipient_gets_own_posture_once_under_launch_policy(
    tmp_path: Path, backend: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await ready(tmp_path, backend, "sitting")
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    await spray(cid, play)
    monkeypatch.setattr(generations, "ACTIVE", not enabled)
    play.rng = RecordedDice([3, 3, 4, 1, 1])
    first = await defend(cid, play, "b", "none")
    assert first.injury and first.injury.attack.effective_target == 13
    assert first.injury.hits == 2 and play.rng.exhausted()
    play.rng = RecordedDice([3, 4, 4] + ([] if enabled else [1]))
    second = await defend(cid, play, "c", "none")
    assert second.injury and second.injury.attack.effective_target == (10 if enabled else 12)
    assert second.injury.hits == (0 if enabled else 1)
    assert second.injury.injury == (0 if enabled else 1)
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unresolved_secondary_recipient_refuses_before_any_sweep_entropy_or_cost(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await ready(tmp_path, backend, "prone")
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="exposure requires"):
        await spray(cid, play)
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("after_first", [False, True])
async def test_fresh_drowsiness_checks_all_remaining_targets_and_keeps_earlier_rolls_durable(
    tmp_path: Path, backend: str, after_first: bool
) -> None:
    cid, play = await ready(tmp_path, backend, "sitting", low=True)
    await spray(cid, play, low=True)
    if after_first:
        # Paid Broadsword9, three shots, -1 range: first target8. One hit.
        play.rng = RecordedDice([2, 2, 3, 1])
        first = await defend(cid, play, "b", "none")
        assert first.injury and first.injury.attack.effective_target == 8
        assert first.injury.attack.total == 7 and first.injury.injury == 1
    service = DrowsyToxinService(play, lambda *_: exposure())
    play.rng = RecordedDice([4, 4, 4])
    for kind in ("expose", "resolve"):
        state = play._load(await play.store.read(cid))
        await service.execute(
            cid,
            ToxinCommand(
                id="dose-" + kind,
                actor_id="a",
                expected_revision=state.revision,
                kind=kind,
                exposure_id="dose",
            ),
            principal_id="gm",
        )
    assert play.rng.exhausted()
    # Second target was9 -3 range -2 sitting=4. Actual drowsiness adds DX-2,
    # making2. Even before the first target's otherwise lawful6, refuse all
    # remaining attacks before any new original/options dice.
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await defend(cid, play, "c" if after_first else "b", "none")
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    state = play._load(before)
    pending = state.encounters[0].pending_defense
    assert pending
    await AbandonPendingAttackService(play).execute(
        cid,
        AbandonPendingAttack(
            id="abandon",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
        ),
        principal_id="a",
    )
    final = play._load(await play.store.read(cid))
    assert final.encounters[0].pending_defense is None
    assert len(final.encounters[0].wounds) == (1 if after_first else 0)
    assert next(pool.current for pool in final.resources.pools if pool.id == "hp:b") == (
        9 if after_first else 10
    )
    assert next(pool.current for pool in final.resources.pools if pool.id == "hp:c") == 20
    assert final.resources.ammunition_loads[0].rounds == (7 if after_first else 10)
    if after_first:
        assert final.encounters[0].wounds[0].attack.dice == (2, 2, 3)
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)
