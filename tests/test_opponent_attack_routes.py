"""B66 actual unarmed, shield-rush and held-missile consequences."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import world
from test_combat_sensory_authority import change
from test_gurps_melee import setup
from test_issue_714_shield_rush import SHIELD_SKILL, board, rush, shield_profile
from test_opponent_attack_host import choose

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.resources import Item
from wayfarer.engine.world import Fact
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import SetRealPlayClock, TaskResult
from wayfarer.orchestration.tasks import TaskService


def luck_source() -> tuple[RuleDefinition, Purchase]:
    luck = next(d for d in candidate_package().definitions if d.id == "trait:advantage:luck")
    luck = replace(luck, source_id=source("gurps-basic-set-4e-2004").id)
    return luck, Purchase(
        definition_id=luck.id, trait=TraitOptions(parameters=(("point-cost", 15),))
    )


async def enroll(path: Path, backend: str, cid: str, original: PlayService) -> PlayService:
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    return play


async def begin_current(play: PlayService, cid: str) -> TaskResult:
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    pending = encounter.pending_defense or encounter.pending_unarmed
    assert pending
    return await TaskService(play).execute(
        cid,
        BeginOpponentAttack(
            id="original",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=pending.id,
        ),
        principal_id="gm",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("revoke", [False, True])
@pytest.mark.parametrize(
    "action,total,following,damage,grip",
    [
        ("punch", 15, (), 0, False),
        ("punch", 9, (4,), 1, False),
        ("grapple", 15, (), 0, False),
        ("grapple", 9, (), 0, True),
    ],
)
async def test_unarmed_selected_roll_changes_actual_punch_or_grip(
    tmp_path: Path,
    backend: str,
    revoke: bool,
    action: Literal["punch", "grapple"],
    total: int,
    following: tuple[int, ...],
    damage: int,
    grip: bool,
) -> None:
    definition, purchase = luck_source()
    cid, original = await setup(
        tmp_path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    play = await enroll(tmp_path, backend, cid, original)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeUnarmedTurn(
            id="strike",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            action=action,
            target_id="b",
            hands=("left-hand",),
            enter_close_combat=True,
        ),
        principal_id="a",
    )
    declared = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 1))
    prepared = await begin_current(play, cid)
    state = play._load(await play.store.read(cid))
    assert (
        state.encounters == declared.encounters
        and state.resources.pools == declared.resources.pools
    )
    assert prepared.check and prepared.check.effective_target == 10
    if revoke:
        await revoke_attacker(play, cid)
    play.rng = RecordedDice((2, 2, 2) + (total // 3,) * 3 + following)
    _, result = await choose(play, cid, prepared.pending_id, principal="b")
    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.unarmed and combat.unarmed.checks[0].total == total
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10 - damage
    assert bool(state.encounters[0].grips) == grip
    assert state.encounters[0].pending_unarmed is None and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("revoke", [False, True])
@pytest.mark.parametrize(
    "total,following,damage,prone,shield_hp",
    [
        (15, (), 0, False, 20),
        (9, (3, 4, 6, 6, 6), 3, True, 18),
    ],
)
async def test_shield_rush_worst_roll_controls_both_collision_bodies_without_repeating_movement(
    tmp_path: Path,
    backend: str,
    revoke: bool,
    total: int,
    following: tuple[int, ...],
    damage: int,
    prone: bool,
    shield_hp: int,
) -> None:
    definition, purchase = luck_source()
    test_world = world()
    test_world = replace(
        test_world,
        facts=test_world.facts
        + (Fact("seen-a", "a", "visible", "yes"), Fact("seen-b", "b", "visible", "yes")),
        knowledge=test_world.knowledge + (("a", "seen-b"), ("b", "seen-a")),
    )
    cid, original = await setup(
        tmp_path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        runtime_world=test_world,
        battlefield=board(),
        allow_supernatural=True,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=5, r=0), hex_facing=3),
        ),
        extra_definitions=(SHIELD_SKILL, definition),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        extra_purchases=(Purchase(definition_id=SHIELD_SKILL.id, amount=4), purchase),
        extra_equipment=(shield_profile(),),
        extra_items=(
            Item(
                id="rush-shield",
                definition_id="equipment:rush-shield",
                owner_id="a",
                equipped=True,
                ready=True,
                condition=ObjectCondition(hp=20),
            ),
        ),
        extra_attacker_hands=(("rush-shield", "left-hand"),),
    )
    play = await enroll(tmp_path, backend, cid, original)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(cid, rush(revision=state.revision), principal_id="a")
    declared = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 1))
    prepared = await begin_current(play, cid)
    assert prepared.check and prepared.check.effective_target == 12
    assert play._load(await play.store.read(cid)).encounters == declared.encounters
    if revoke:
        await revoke_attacker(play, cid)
    play.rng = RecordedDice((2, 2, 2) + (total // 3,) * 3 + following)
    _, result = await choose(play, cid, prepared.pending_id, principal="b")
    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.injury == damage
    state = play._load(await play.store.read(cid))
    attacker, target = state.encounters[0].participants
    assert attacker.position == target.position == Hex(q=5, r=0)
    assert (target.posture == "prone") == prone
    shield = next(item for item in state.resources.items if item.id == "rush-shield")
    assert shield.condition and shield.condition.hp == shield_hp
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("revoke", [False, True])
@pytest.mark.parametrize("total,following,damage", [(15, (), 0), (5, (4,), 4)])
async def test_held_missile_keeps_casting_energy_and_selected_hit_ends_one_fireball(
    tmp_path: Path,
    backend: str,
    revoke: bool,
    total: int,
    following: tuple[int, ...],
    damage: int,
) -> None:
    from test_spell_bindings import command as spell_command
    from test_spell_bindings import idle, start_fight
    from test_spell_bindings import setup as spell_setup

    from wayfarer.engine.simulation.magic.spells import latest
    from wayfarer.orchestration.spells import SpellService

    definition, purchase = luck_source()
    cid, original = await spell_setup(
        tmp_path / "source",
        combat=True,
        execution_version=2,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    await start_fight(cid, original)
    original.rng = RecordedDice((3, 3, 3))
    start = spell_command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    await SpellService(original).execute(cid, start, principal_id="a")
    await idle(cid, original, "b")
    await SpellService(original).execute(
        cid,
        start.model_copy(update={"id": "release", "kind": "release", "expected_revision": 3}),
        principal_id="a",
    )
    play = await enroll(tmp_path, backend, cid, original)
    declared = play._load(await play.store.read(cid))
    assert next(p.current for p in declared.resources.pools if p.id == "fp:a") == 9
    play.rng = RecordedDice((1, 1, 1))
    prepared = await begin_current(play, cid)
    assert prepared.check and prepared.check.effective_target == 6
    assert latest(play._load(await play.store.read(cid)).resources)["cast"].phase == "active"
    if revoke:
        await revoke_attacker(play, cid)
    final_dice = (1, 2, 2) if total == 5 else (5, 5, 5)
    play.rng = RecordedDice((1, 1, 1) + final_dice + following)
    _, result = await choose(play, cid, prepared.pending_id, principal="b")
    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.attack.total == total and combat.injury.injury == damage
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10 - damage
    assert latest(state.resources)["cast"].phase == "ended"
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


async def revoke_attacker(play: PlayService, cid: str) -> None:
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"approval": None}) if a.actor_id == "a" else a
                    for a in s.actors
                )
            }
        ),
    )
