"""B66 on real queued B365/B409 attacks, preserving prior costs and hits."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_opponent_attack_routes import enroll, luck_source
from test_spraying_fire import automatic_weapon as spray_weapon
from test_suppression_fire import automatic_weapon as suppress_weapon
from test_suppression_fire import battlefield

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.combat.encounter import CombatResult, RangedSituation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.suppression import SprayTarget, SuppressionZone
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tasks import TaskService


async def automatic_fixture(
    path: Path, backend: str, *, suppression: bool
) -> tuple[str, PlayService]:
    definition, purchase = luck_source()
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        ranged_mode=suppress_weapon() if suppression else spray_weapon(),
        ranged_scene=(
            RangedSituation(
                attacker_id="a", defender_id="b", distance_yards=2 if suppression else 3
            ),
            RangedSituation(attacker_id="a", defender_id="c", distance_yards=3),
        ),
        battlefield=battlefield() if suppression else None,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=2, r=-2), hex_facing=3),
            Placement(actor_id="c", position=Hex(q=6, r=-2), hex_facing=3),
        )
        if suppression
        else (
            Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            Placement(actor_id="b", position=GridPoint(x=3, y=0), facing="west"),
            Placement(actor_id="c", position=GridPoint(x=2, y=1), facing="west"),
        ),
    )
    for _ in range(2):
        await turn(
            cid,
            original,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="suppress" if suppression else "spray",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, original, "b", "do_nothing")
        await turn(cid, original, "c", "do_nothing")
    return cid, await enroll(path, backend, cid, original)


async def original(play: PlayService, cid: str, identifier: str) -> str:
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    result = await TaskService(play).execute(
        cid,
        BeginOpponentAttack(
            id=identifier,
            actor_id=pending.defender_id,
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=pending.id,
        ),
        principal_id="gm",
    )
    assert result.pending_id
    return result.pending_id


async def decide(
    play: PlayService, cid: str, pending_id: str, identifier: str, *, luck: bool = True
) -> CombatResult:
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    result = await TaskService(play).execute(
        cid,
        ChooseOpponentAttack(
            id=identifier,
            actor_id=pending.defender_id,
            expected_revision=state.revision,
            pending_id=pending_id,
            choice="use-luck" if luck else "accept",
            response=ChooseDefense(
                id=identifier,
                actor_id=pending.defender_id,
                expected_revision=state.revision,
                encounter_id="fight",
                defense="none",
            ),
        ),
        principal_id=pending.defender_id,
    )
    assert result.combat_json
    return CombatResult.model_validate_json(result.combat_json)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_each_spray_target_has_its_own_owner_attack_and_traversal_cost_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await automatic_fixture(tmp_path, backend, suppression=False)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="spray",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            target_id="b",
            mode_id="spray",
            shots=5,
            spray_targets=(SprayTarget(target_id="c", shots=4),),
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((1, 1, 1))
    first = await original(play, cid, "first")
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5))
    result = await decide(play, cid, first, "first-choice")
    assert result.injury and result.injury.attack.total == 15 and result.injury.hits == 0
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and pending.defender_id == "c" and pending.traversal_shots == 1
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    play.rng = RecordedDice((1, 1, 1))
    second = await original(play, cid, "second")
    play.rng = RecordedDice((2, 2, 2, 3, 3, 4, 1))
    result = await decide(play, cid, second, "second-choice")
    assert result.injury and result.injury.attack.effective_target == 12
    assert (
        result.injury.attack.total == 10 and result.injury.hits == 1 and result.injury.injury == 1
    )
    after = play._load(await play.store.read(cid))
    assert not after.resources.ammunition_loads and after.encounters[0].pending_defense is None
    assert next(p.current for p in after.resources.pools if p.id == "hp:c") == 19
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_queued_suppression_attacks_preserve_paid_shots_and_one_owner_cooldown(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await automatic_fixture(tmp_path, backend, suppression=True)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="suppress",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="all_out_attack",
            attack_option="suppression",
            item_id="sword-a",
            mode_id="suppress",
            suppression_zones=(
                SuppressionZone(center=Hex(q=3, r=0), shots=5),
                SuppressionZone(center=Hex(q=5, r=0), shots=5),
            ),
        ),
        principal_id="a",
    )
    await turn(cid, play, "b", "move", hex_path=(Hex(q=2, r=-1), Hex(q=2, r=0)), hex_facing=3)
    before = play._load(await play.store.read(cid))
    assert not before.resources.ammunition_loads
    play.rng = RecordedDice((1, 1, 1))
    first = await original(play, cid, "first")
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5))
    result = await decide(play, cid, first, "first-choice")
    assert result.injury and result.injury.hits == 0
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and pending.suppression_zone_id == "suppression:suppress:1"
    play.rng = RecordedDice((2, 2, 2))
    second = await original(play, cid, "second")
    before_choice = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="cooling down"):
        await decide(play, cid, second, "ineligible")
    assert await play.store.read(cid) == before_choice and play.rng.exhausted()
    play.rng = RecordedDice((3, 3, 3, 1))
    result = await decide(play, cid, second, "second-choice", luck=False)
    assert result.injury and result.injury.attack.total == 6 and result.injury.hits == 1
    after = play._load(await play.store.read(cid))
    assert not after.resources.ammunition_loads and after.encounters[0].pending_defense is None
    assert after.encounters[0].current_actor_id == "c"
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 9
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("option", ["double", "rapid-strike"])
async def test_later_blow_keeps_the_first_selected_attack_and_uses_current_cooldown(
    tmp_path: Path, backend: str, option: str
) -> None:
    from test_opponent_attack_inventory import fixture

    cid, play = await fixture(tmp_path, backend, ranged=False)
    await turn(
        cid,
        play,
        "a",
        "all_out_attack" if option == "double" else "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option=option,
    )
    play.rng = RecordedDice((1, 1, 1))
    first = await original(play, cid, "first")
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5))
    result = await decide(play, cid, first, "first-choice")
    assert result.injury and result.injury.attack.total == 15 and result.injury.injury == 0
    mid = play._load(await play.store.read(cid))
    assert mid.encounters[0].pending_defense and mid.encounters[0].current_actor_id == "a"
    play.rng = RecordedDice((2, 2, 2))
    second = await original(play, cid, "second")
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="cooling down"):
        await decide(play, cid, second, "ineligible")
    play.rng = RecordedDice((2,))
    result = await decide(play, cid, second, "second-choice", luck=False)
    assert result.injury and result.injury.attack.total == 6 and result.injury.injury == 4
    after = play._load(await play.store.read(cid))
    assert (
        after.encounters[0].pending_defense is None and after.encounters[0].current_actor_id == "b"
    )
    assert len(after.encounters[0].wounds) == 2
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 6
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_wait_reaction_and_resumed_attack_each_preserve_their_own_luck_decision(
    tmp_path: Path, backend: str
) -> None:
    from test_opponent_attack_inventory import fixture

    from wayfarer.engine.simulation.combat.commands import ResumeInterruptedTurn

    cid, play = await fixture(tmp_path, backend, ranged=False)
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "attack",
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    result = await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    assert result.code == "combat.wait_triggered"
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice((1, 1, 1))
    first = await original(play, cid, "reaction")
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5))
    result = await decide(play, cid, first, "reaction-choice")
    assert result.injury and result.injury.injury == 0
    state = play._load(await play.store.read(cid))
    interrupt = state.encounters[0].wait_interrupt
    assert interrupt and interrupt.ready and not interrupt.reacting
    await CombatService(play).execute(
        cid,
        ResumeInterruptedTurn(
            id="resume",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
        ),
        principal_id="b",
    )
    play.rng = RecordedDice((1, 1, 1))
    second = await original(play, cid, "resumed")
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5))
    result = await decide(play, cid, second, "resumed-choice")
    assert result.injury and result.injury.injury == 0
    after = play._load(await play.store.read(cid))
    assert (
        after.encounters[0].wait_interrupt is None and after.encounters[0].pending_defense is None
    )
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 10
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_random_unarmed_location_is_drawn_after_selected_attack_once(
    tmp_path: Path, backend: str
) -> None:
    from test_combat_sensory_authority import change
    from test_opponent_attack_host import choose
    from test_opponent_attack_inventory import fixture
    from test_opponent_attack_routes import begin_current
    from test_unarmed_symptom_blindness import blindness, locate, random_command

    from wayfarer.engine.simulation.combat.unarmed.random_strike import pending_id, random_strike
    from wayfarer.orchestration.combat.unarmed_host import RandomUnarmedService

    cid, play = await fixture(tmp_path, backend, ranged=False)
    await change(play, cid, lambda s: blindness(s, "a"))
    await locate(cid, play, "a", exact=True)
    command = await random_command(cid, play)
    await RandomUnarmedService(play).execute(cid, command, principal_id="a")
    play.rng = RecordedDice((1, 1, 1))
    begun = await begin_current(play, cid)
    assert begun.check and begun.check.effective_target == 4 and play.rng.exhausted()
    play.rng = RecordedDice((1, 1, 2, 3, 3, 3, 3, 3, 3, 2, 2, 2))
    _, result = await choose(play, cid, begun.pending_id, principal="b")
    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.unarmed and combat.unarmed.checks[0].total == 9 and not combat.unarmed.won
    after = play._load(await play.store.read(cid))
    resolved = random_strike(after, "fight", pending_id(command.id), "a", "b")
    assert resolved and resolved.location_dice == (3, 3, 3)
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)
