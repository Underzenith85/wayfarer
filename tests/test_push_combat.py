"""B216/B372/B378 actual Push damage-for-knockback and B201 resistance.

The fixture purchases real skills/traits; expectations do not call production
formulas. Persistence tests exercise the internal reducer, not an API claim.
"""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_cinematic_skills import compiler
from test_gurps_melee import setup
from test_mastery_combat import DEFINITIONS, purchase
from test_statistics import gurps_draft, profile_package

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.cinematic import package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState, WaitTrigger
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter
from wayfarer.engine.simulation.combat.unarmed.injury import drop_held
from wayfarer.engine.simulation.equipment.catalog import Damage, MeleeMode, Parry
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.engine.simulation.resources import Pool
from wayfarer.engine.simulation.skills.push import (
    PushCommand,
    PushDefense,
    PushResult,
    declare_push,
    defend_push,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService

SKILLS = tuple(
    replace(d, source_id=profile_package("gurps-basic-set-4e-2004").sources[0].id)
    for d in package().definitions
    if d.id in {"skill:push", "skill:immovable-stance"}
)


async def game(
    tmp_path: Path,
    *,
    stance: bool = False,
    shield: bool = False,
    learned: bool = True,
    mastery: str | None = "trained-by-a-master",
    points: int = 16,
    melee_modes: tuple[MeleeMode, ...] | None = None,
    board: Battlefield | HexBattlefield | None = None,
    placements: tuple[Placement, ...] | None = None,
    extra_definitions: tuple[RuleDefinition, ...] = (),
    extra_purchases: tuple[Purchase, ...] = (),
) -> tuple[str, PlayService, PlayState, PushCommand]:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        unarmed_fixture=True,
        free_defender_hand=not shield,
        battlefield=board or Battlefield(id="dock", location_id="dock", width=16, height=10),
        extra_definitions=DEFINITIONS + SKILLS + extra_definitions,
        placements=placements,
        extra_purchases=(
            *((purchase(mastery),) if mastery else ()),
            *((Purchase(definition_id="skill:push", amount=points),) if learned else ()),
            *((Purchase(definition_id="skill:immovable-stance", amount=4),) if stance else ()),
        )
        + extra_purchases,
        trait_runtime_hooks=SUPPORTED_HOOKS,
        melee_modes=melee_modes,
    )
    state = play._load(await play.store.read(cid))
    state, encounter = drop_held(state, state.encounters[0], "a")
    state = state.model_copy(update={"encounters": (encounter,)})
    return cid, play, state, declaration(play, state)


def declaration(play: PlayService, state: PlayState, *, command_id: str = "push") -> PushCommand:
    return PushCommand(
        id=command_id,
        actor_id="a",
        expected_revision=state.resources.revision,
        build_revision=play.rules_context.approved_build(state, "a").revision,
        encounter_id="fight",
        target_id="b",
        hands=("left-hand", "right-hand"),
        enter_close_combat=True,
    )


def response(
    play: PlayService,
    state: PlayState,
    *,
    defense: Literal["none", "dodge", "parry", "block"] = "none",
    stance: bool = False,
    item_id: str | None = None,
    mode_id: str | None = None,
    attack_id: str = "push",
    command_id: str = "push-defense",
) -> PushDefense:
    return PushDefense(
        id=command_id,
        actor_id="b",
        expected_revision=state.resources.revision,
        build_revision=play.rules_context.approved_build(state, "b").revision,
        encounter_id="fight",
        attack_command_id=attack_id,
        defense=defense,
        immovable_stance=stance,
        item_id=item_id,
        mode_id=mode_id,
    )


def pool(state: PlayState, key: str) -> Pool:
    return next(p for p in state.resources.pools if p.id == key)


def replace_pool(state: PlayState, value: Pool) -> PlayState:
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(value if p.id == value.id else p for p in state.resources.pools)
                }
            )
        }
    )


def declare(
    play: PlayService, state: PlayState, command: PushCommand
) -> tuple[PlayState, PushResult]:
    play.rng = RecordedDice([3, 3, 3])
    return declare_push(play.rules_context, state, command, authorized_actor_id="a")


@pytest.mark.parametrize("two_hands,basic,yards", [(True, 18, 2), (False, 14, 1)])
@pytest.mark.parametrize("defended", [True, False])
async def test_push_moves_actual_defender_without_injury_and_retry_reuses_outcome(
    tmp_path: Path, two_hands: bool, basic: int, yards: int, defended: bool
) -> None:
    _, play, state, command = await game(tmp_path)
    if not two_hands:
        command = command.model_copy(update={"hands": ("left-hand",)})
    declared, waiting = declare(play, state, command)
    assert waiting.stage == "defense-required" and waiting.attack is not None
    assert waiting.attack.effective_target == 13
    assert declared.resources.revision == state.resources.revision + 1
    assert declared.encounters[0].blocked_reason is not None
    play.rng = RecordedDice([])
    assert declare_push(play.rules_context, declared, command, authorized_actor_id="a") == (
        declared,
        waiting,
    )
    with pytest.raises(AuthorizationError):
        declare_push(play.rules_context, declared, command, authorized_actor_id="b")
    with pytest.raises(ConflictError):
        declare_push(
            play.rules_context,
            declared,
            command.model_copy(update={"hands": ("right-hand",)}),
            authorized_actor_id="a",
        )
    defense = response(play, declared, defense="dodge" if defended else "none")
    play.rng = RecordedDice([2, 2, 2] if defended else [5, 5, 3, 3, 3])
    changed, result = defend_push(play.rules_context, declared, defense, authorized_actor_id="b")
    assert play.rng.exhausted()
    assert result.stage == "resolved"
    target = fighter(changed.encounters[0], "b")
    assert target.position == GridPoint(x=1 if defended else 1 + yards, y=0)
    assert result.basic_damage == (0 if defended else basic)
    assert pool(changed, "hp:b").current == 10
    assert pool(changed, "fp:a").current == pool(changed, "fp:b").current == 10
    assert changed.resources.revision == declared.resources.revision + 1
    assert changed.encounters[0].current_actor_id == "b"
    assert changed.encounters[0].blocked_reason is None
    assert (
        changed.encounters[0].close_pairs == (("a", "b"),)
        if defended
        else not changed.encounters[0].close_pairs
    )
    play.rng = RecordedDice([])
    assert defend_push(play.rules_context, changed, defense, authorized_actor_id="b") == (
        changed,
        result,
    )
    with pytest.raises(AuthorizationError):
        defend_push(play.rules_context, changed, defense, authorized_actor_id="a")
    with pytest.raises(ConflictError):
        defend_push(
            play.rules_context,
            changed,
            defense.model_copy(update={"defense": "parry"}),
            authorized_actor_id="b",
        )


@pytest.mark.parametrize("dice,prone", [([5, 5, 5], False), ([6, 6, 6, 2, 3, 3], True)])
async def test_misses_cost_no_fp_and_critical_fall_changes_both_postures(
    tmp_path: Path, dice: list[int], prone: bool
) -> None:
    _, play, state, command = await game(tmp_path)
    play.rng = RecordedDice(dice)
    changed, result = declare_push(play.rules_context, state, command, authorized_actor_id="a")
    assert play.rng.exhausted() and result.stage == "missed"
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=1, y=0)
    assert (fighter(changed.encounters[0], "a").posture == "prone") == prone
    hp = pool(changed, "hp:a")
    assert hp.injury is not None and hp.injury.prone == prone
    assert pool(changed, "hp:a").current == pool(changed, "hp:b").current == 10
    assert pool(changed, "fp:a").current == 10
    assert changed.encounters[0].current_actor_id == "b"
    play.rng = RecordedDice([])
    assert declare_push(play.rules_context, changed, command, authorized_actor_id="a") == (
        changed,
        result,
    )


@pytest.mark.parametrize("defense", ["dodge", "parry"])
async def test_all_ordinary_legal_defenses_stop_actual_push(
    tmp_path: Path, defense: Literal["dodge", "parry"]
) -> None:
    _, play, state, command = await game(tmp_path)
    declared, _ = declare(play, state, command)
    selected = response(
        play, declared, defense=defense, item_id="left-hand" if defense == "parry" else None
    )
    play.rng = RecordedDice([2, 2, 2])
    changed, result = defend_push(play.rules_context, declared, selected, authorized_actor_id="b")
    assert play.rng.exhausted() and result.defense is not None and result.defense.outcome.succeeded
    assert result.basic_damage == 0 and result.displacement is None
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=1, y=0)


async def test_armed_parry_uses_weapon_skill_and_injures_pushing_arm(tmp_path: Path) -> None:
    mode = MeleeMode(
        id="close",
        skill_id="skill:broadsword",
        reach=(0,),
        damage=Damage(basis="swing", adds=1, damage_type="cut"),
        parry=Parry(),
    )
    _, play, state, command = await game(tmp_path, melee_modes=(mode,))
    declared, _ = declare(play, state, command)
    selected = response(play, declared, defense="parry", item_id="sword-b", mode_id="close")
    play.rng = RecordedDice([2, 2, 2, 3, 3, 3, 2])
    changed, result = defend_push(play.rules_context, declared, selected, authorized_actor_id="b")
    assert play.rng.exhausted()
    assert result.basic_damage == 0
    assert len(result.effect_checks) == 1 and result.effect_checks[0].outcome.succeeded
    assert pool(changed, "hp:a").current == 6  # sw10=1d, +1 sword, cut x1.5, floor
    assert pool(changed, "hp:b").current == 10
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=1, y=0)


async def test_close_combat_block_rejects_before_dice_or_shield_changes(
    tmp_path: Path,
) -> None:
    _, play, state, command = await game(tmp_path, shield=True)
    declared, _ = declare(play, state, command)
    before = declared.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Blocking is unavailable in close combat"):
        defend_push(
            play.rules_context,
            declared,
            response(play, declared, defense="block"),
            authorized_actor_id="b",
        )
    assert declared.model_dump_json() == before and play.rng.exhausted()


@pytest.mark.parametrize("critical", [False, True])
async def test_critical_dodge_success_fells_attacker_failure_fells_defender(
    tmp_path: Path, critical: bool
) -> None:
    _, play, state, command = await game(tmp_path)
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([1, 1, 1, 2, 3, 3] if critical else [6, 6, 6, 5, 5, 3, 3, 3])
    changed, result = defend_push(
        play.rules_context,
        declared,
        response(play, declared, defense="dodge"),
        authorized_actor_id="b",
    )
    assert play.rng.exhausted()
    actor = "a" if critical else "b"
    assert fighter(changed.encounters[0], actor).posture == "prone"
    hp = pool(changed, "hp:" + actor)
    assert hp.injury is not None and hp.injury.prone
    assert result.basic_damage == (0 if critical else 18)


@pytest.mark.parametrize(
    "table,dice,basic", [([6, 6, 6], [5, 5], 54), ([2, 2, 2], [], 22), ([5, 6, 6], [5, 5], 18)]
)
async def test_critical_hit_modifies_only_knockback_and_skips_defense(
    tmp_path: Path, table: list[int], dice: list[int], basic: int
) -> None:
    _, play, state, command = await game(tmp_path)
    play.rng = RecordedDice([1, 1, 1] + table)
    declared, _ = declare_push(play.rules_context, state, command, authorized_actor_id="a")
    play.rng = RecordedDice(dice + [1, 1, 1])
    changed, result = defend_push(
        play.rules_context,
        declared,
        response(play, declared, defense="dodge"),
        authorized_actor_id="b",
    )
    assert play.rng.exhausted() and result.defense is None
    assert result.basic_damage == basic
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=1 + basic // 8, y=0)
    assert pool(changed, "hp:b").current == 10


@pytest.mark.parametrize(
    "dice,prevented,fell,checks",
    [
        ([2, 2, 2], True, False, 1),
        ([3, 3, 3, 3, 3, 3], False, False, 2),
        ([3, 3, 3, 4, 4, 4], False, True, 2),
        ([6, 6, 6], False, True, 1),
    ],
)
async def test_stance_success_failure_and_critical_failure_change_actual_state(
    tmp_path: Path, dice: list[int], prevented: bool, fell: bool, checks: int
) -> None:
    _, play, state, command = await game(tmp_path, stance=True)
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([5, 5] + dice)
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared, stance=True), authorized_actor_id="b"
    )
    assert play.rng.exhausted() and result.displacement is not None
    outcome = result.displacement
    assert outcome.prevented == prevented and outcome.fell == fell and len(outcome.checks) == checks
    assert outcome.potential_yards == 2
    assert outcome.checks[0].effective_target == 8  # approved Stance10 - 2 potential yards
    if checks == 2:
        assert outcome.checks[1].effective_target == 9  # DX/Judo10 - (2-1)
    actor = fighter(changed.encounters[0], "b")
    assert actor.position == GridPoint(x=1 if prevented else 3, y=0)
    assert (actor.posture == "prone") == fell
    hp = pool(changed, "hp:b")
    assert hp.injury is not None and hp.injury.prone == fell and hp.current == 10
    assert pool(changed, "fp:b").current == 10


@pytest.mark.parametrize("skill", ["push", "immovable-stance"])
@pytest.mark.parametrize("master", [None, "weapon-master"])
def test_cinematic_purchases_reject_missing_specific_mastery(
    skill: str, master: str | None
) -> None:
    purchases = (() if master is None else (purchase(master),)) + (
        Purchase(definition_id="skill:" + skill, amount=4),
    )
    result = compiler(include_masters=True).compile(gurps_draft(*purchases))
    assert result.build is None
    accepted = compiler(include_masters=True).compile(
        gurps_draft(
            purchase("trained-by-a-master"), Purchase(definition_id="skill:" + skill, amount=4)
        )
    )
    assert accepted.build is not None


@pytest.mark.parametrize("master", [None, "weapon-master", "trained-by-a-master"])
async def test_runtime_cannot_invent_an_unpurchased_push_skill(
    tmp_path: Path, master: str | None
) -> None:
    _, play, state, command = await game(tmp_path, learned=False, mastery=master)
    before = state.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Trained By A Master|does not know Push"):
        declare_push(play.rules_context, state, command, authorized_actor_id="a")
    assert state.model_dump_json() == before


@pytest.mark.parametrize(
    "updates",
    [
        {"expected_revision": 0},
        {"build_revision": "stale"},
        {"encounter_id": "missing"},
        {"target_id": "missing"},
        {"target_id": "a"},
        {"hands": ("left-hand", "left-hand")},
        {"enter_close_combat": False},
    ],
)
async def test_invalid_declaration_rejects_before_randomness(
    tmp_path: Path, updates: dict[str, object]
) -> None:
    _, play, state, command = await game(tmp_path)
    before = state.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises((ValidationError, ConflictError)):
        declare_push(
            play.rules_context, state, command.model_copy(update=updates), authorized_actor_id="a"
        )
    assert state.model_dump_json() == before


@pytest.mark.parametrize(
    "condition",
    [
        "unconscious",
        "stunned",
        "collapsed",
        "conditions",
        "lost-balance",
        "out-of-turn",
        "occupied-hands",
        "wait",
        "blocked",
    ],
)
async def test_incapacity_and_unsupported_turns_reject_atomically(
    tmp_path: Path, condition: str
) -> None:
    _, play, state, command = await game(tmp_path)
    encounter = state.encounters[0]
    actor = fighter(encounter, "a")
    if condition in ("unconscious", "stunned"):
        hp = pool(state, "hp:a")
        assert hp.injury is not None
        state = replace_pool(
            state, hp.model_copy(update={"injury": hp.injury.model_copy(update={condition: True})})
        )
    elif condition == "collapsed":
        fp = pool(state, "fp:a")
        assert fp.fatigue is not None
        state = replace_pool(
            state,
            fp.model_copy(update={"fatigue": fp.fatigue.model_copy(update={"collapsed": True})}),
        )
    elif condition == "conditions":
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"conditions": ("restrained",)}) if a.actor_id == "a" else a
                    for a in state.actors
                )
            }
        )
    elif condition == "lost-balance":
        encounter = CombatEngine._replace(
            encounter, actor.model_copy(update={"unarmed_balance_lost": True})
        )
    elif condition == "out-of-turn":
        encounter = encounter.model_copy(update={"turn_index": 1})
    elif condition == "occupied-hands":
        encounter = CombatEngine._replace(
            encounter, actor.model_copy(update={"hand_bindings": (("sword-a", "left-hand"),)})
        )
    elif condition == "wait":
        actor = fighter(encounter, "b")
        encounter = CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "maneuver_state": ManeuverState(
                        wait=WaitTrigger(
                            action="attack",
                            reaction="attack",
                            reaction_target_id="a",
                            item_id="sword-b",
                        )
                    )
                }
            ),
        )
    else:
        encounter = encounter.model_copy(update={"blocked_reason": "other pending adjudication"})
    state = state.model_copy(update={"encounters": (encounter,)})
    before = state.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError):
        declare_push(play.rules_context, state, command, authorized_actor_id="a")
    assert state.model_dump_json() == before


@pytest.mark.parametrize(
    "updates",
    [
        {"expected_revision": 0},
        {"build_revision": "stale"},
        {"encounter_id": "missing"},
        {"attack_command_id": "missing"},
        {"immovable_stance": True},
        {"defense": "dodge", "item_id": "sword-b"},
        {"defense": "none", "mode_id": "swing"},
        {"defense": "block", "mode_id": "swing"},
        {"defense": "block"},
        {"defense": "parry", "item_id": "sword-b", "mode_id": "swing"},
    ],
)
async def test_invalid_defense_rejects_before_damage_or_any_random_draw(
    tmp_path: Path, updates: dict[str, object]
) -> None:
    _, play, state, command = await game(tmp_path)
    declared, _ = declare(play, state, command)
    selected = response(play, declared).model_copy(update=updates)
    before = declared.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises((ValidationError, ConflictError, AuthorizationError)):
        defend_push(play.rules_context, declared, selected, authorized_actor_id="b")
    assert declared.model_dump_json() == before


@pytest.mark.parametrize(
    "alteration", ["clock", "round", "position", "attacker-approval", "defender-approval"]
)
async def test_pending_push_is_bound_to_exact_encounter_clock_and_both_approvals(
    tmp_path: Path, alteration: str
) -> None:
    _, play, state, command = await game(tmp_path)
    declared, _ = declare(play, state, command)
    selected = response(play, declared)
    encounter = declared.encounters[0]
    if alteration == "clock":
        declared = declared.model_copy(
            update={
                "resources": declared.resources.model_copy(
                    update={"game_time": declared.resources.game_time + 1}
                )
            }
        )
    elif alteration == "round":
        declared = declared.model_copy(
            update={"encounters": (encounter.model_copy(update={"round": encounter.round + 1}),)}
        )
    elif alteration == "position":
        declared = declared.model_copy(
            update={
                "encounters": (
                    CombatEngine._replace(
                        encounter,
                        fighter(encounter, "b").model_copy(
                            update={"position": GridPoint(x=2, y=0)}
                        ),
                    ),
                )
            }
        )
    else:
        actor_id = "a" if alteration == "attacker-approval" else "b"
        declared = declared.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"approval": None}) if a.actor_id == actor_id else a
                    for a in declared.actors
                )
            }
        )
    before = declared.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises((ValidationError, ConflictError)):
        defend_push(play.rules_context, declared, selected, authorized_actor_id="b")
    assert declared.model_dump_json() == before


@pytest.mark.parametrize("actor_id", ["a", "b"])
async def test_failed_zero_fp_exertion_persists_collapse_without_spending_or_rerolling(
    tmp_path: Path, actor_id: str
) -> None:
    _, play, state, command = await game(tmp_path, stance=True)
    state = replace_pool(state, pool(state, "fp:" + actor_id).model_copy(update={"current": 0}))
    if actor_id == "a":
        play.rng = RecordedDice([4, 4, 4])
        changed, result = declare_push(play.rules_context, state, command, authorized_actor_id="a")
        assert result.stage == "exertion-denied" and result.attack is None
        assert fighter(changed.encounters[0], "a").position == GridPoint(x=0, y=0)
    else:
        declared, _ = declare(play, state, command)
        selected = response(play, declared, stance=True, defense="dodge")
        play.rng = RecordedDice([4, 4, 4, 5, 5])
        changed, result = defend_push(
            play.rules_context, declared, selected, authorized_actor_id="b"
        )
        assert result.defense is None and result.displacement is not None
        assert not result.displacement.checks and result.displacement.potential_yards == 2
        assert fighter(changed.encounters[0], "b").position == GridPoint(x=3, y=0)
    assert play.rng.exhausted()
    fp = pool(changed, "fp:" + actor_id)
    assert fp.current == 0 and fp.fatigue is not None and fp.fatigue.collapsed
    assert pool(changed, "hp:" + actor_id).current == 10
    play.rng = RecordedDice([])
    if actor_id == "a":
        assert declare_push(play.rules_context, changed, command, authorized_actor_id="a") == (
            changed,
            result,
        )
    else:
        assert defend_push(play.rules_context, changed, selected, authorized_actor_id="b") == (
            changed,
            result,
        )


@pytest.mark.parametrize("fp,yards", [(4, 2), (3, 6)])
async def test_defenders_real_fatigue_threshold_changes_resistance(
    tmp_path: Path, fp: int, yards: int
) -> None:
    _, play, state, command = await game(tmp_path)
    state = replace_pool(state, pool(state, "fp:b").model_copy(update={"current": fp}))
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([5, 5, 1, 1, 1])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert result.displacement is not None and result.displacement.potential_yards == yards
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=1 + yards, y=0)
    assert pool(changed, "fp:b").current == fp


async def test_collision_moves_only_clear_yards_and_pauses_without_invented_injury(
    tmp_path: Path,
) -> None:
    _, play, state, command = await game(
        tmp_path,
        board=Battlefield(
            id="dock", location_id="dock", width=4, height=3, blocked=(GridPoint(x=3, y=0),)
        ),
    )
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([5, 5, 3, 3, 3])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert result.displacement is not None and result.displacement.moved_yards == 1
    assert result.displacement.adjudication_required == "collision-or-map-edge"
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=2, y=0)
    assert changed.encounters[0].blocked_reason is not None
    assert changed.encounters[0].current_actor_id == "a"
    assert pool(changed, "hp:b").current == 10


async def test_push_round_trips_pending_and_final_checkpoints_in_actual_store(
    tmp_path: Path,
) -> None:
    cid, play, state, command = await game(tmp_path, stance=True)
    command = command.model_copy(update={"id": "a" * 200})
    play.rng = RecordedDice([3, 3, 3])
    declared, waiting = declare_push(play.rules_context, state, command, authorized_actor_id="a")

    def save_pending(campaign: Campaign) -> CommandReceipt:
        campaign["play_json"] = declared.model_copy(update={"revision": 2}).model_dump_json()
        campaign["revision"] += 1
        return CommandReceipt(action="combat", outcome="Push awaiting defense")

    await play.store.commit_turn(cid, "save-push", 1, "fixture", save_pending)
    loaded = play._load(await play.store.read(cid))
    play.rng = RecordedDice([])
    assert declare_push(play.rules_context, loaded, command, authorized_actor_id="a") == (
        loaded,
        waiting,
    )
    selected = response(play, loaded, stance=True, attack_id=command.id, command_id="d" * 200)
    play.rng = RecordedDice([5, 5, 3, 3, 3, 4, 4, 4])
    changed, result = defend_push(play.rules_context, loaded, selected, authorized_actor_id="b")
    assert result.displacement is not None and result.displacement.fell

    def save_final(campaign: Campaign) -> CommandReceipt:
        campaign["play_json"] = changed.model_copy(update={"revision": 3}).model_dump_json()
        campaign["revision"] += 1
        return CommandReceipt(action="combat", outcome="Push resolved")

    await play.store.commit_turn(cid, "save-push-defense", 2, "fixture", save_final)
    reloaded = play._load(await play.store.read(cid))
    assert reloaded.resources == changed.resources and reloaded.encounters == changed.encounters
    play.rng = RecordedDice([])
    assert defend_push(play.rules_context, reloaded, selected, authorized_actor_id="b") == (
        reloaded,
        result,
    )
    assert fighter(reloaded.encounters[0], "b").position == GridPoint(x=3, y=0)
    assert fighter(reloaded.encounters[0], "b").posture == "prone"
    play.rng = RecordedDice([5, 5, 3, 3, 3, 4, 4, 4])
    replayed, same = defend_push(play.rules_context, loaded, selected, authorized_actor_id="b")
    assert replayed == changed and same == result


@pytest.mark.parametrize("fp", [10, 3])
async def test_lower_push_skill_uses_st_and_fatigue_does_not_halve_damage(
    tmp_path: Path, fp: int
) -> None:
    _, play, state, command = await game(tmp_path, points=1)
    state = replace_pool(state, pool(state, "fp:a").model_copy(update={"current": fp}))
    play.rng = RecordedDice([2, 2, 2])
    declared, pending = declare_push(play.rules_context, state, command, authorized_actor_id="a")
    assert pending.attack is not None and pending.attack.effective_target == 8
    assert pending.pending is not None and pending.pending.strength == 10
    play.rng = RecordedDice([5, 3, 3, 3])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert play.rng.exhausted() and result.basic_damage == 10  # swing10=1d; double rolled5
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=2, y=0)
    assert pool(changed, "fp:a").current == fp and pool(changed, "hp:b").current == 10


async def test_one_hand_zero_damage_does_not_roll_balance(tmp_path: Path) -> None:
    _, play, state, command = await game(tmp_path, points=1)
    play.rng = RecordedDice([2, 2, 2])
    declared, _ = declare_push(
        play.rules_context,
        state,
        command.model_copy(update={"hands": ("left-hand",)}),
        authorized_actor_id="a",
    )
    play.rng = RecordedDice([1])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert play.rng.exhausted() and result.basic_damage == 0
    assert result.displacement is not None and result.displacement.potential_yards == 0
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=1, y=0)


@pytest.mark.parametrize("force", [0, 8])
async def test_stance_exertion_occurs_only_after_force_requires_a_reaction(
    tmp_path: Path, force: int
) -> None:
    _, play, state, command = await game(tmp_path, points=1, stance=True)
    state = replace_pool(state, pool(state, "fp:b").model_copy(update={"current": 0}))
    play.rng = RecordedDice([2, 2, 2])
    declared, _ = declare_push(
        play.rules_context,
        state,
        command.model_copy(update={"hands": ("left-hand",)}),
        authorized_actor_id="a",
    )
    play.rng = RecordedDice([1] if force == 0 else [5, 4, 4, 4])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared, stance=True), authorized_actor_id="b"
    )
    fp = pool(changed, "fp:b")
    assert fp.fatigue is not None and fp.fatigue.collapsed == (force > 0)
    assert fp.current == 0 and result.basic_damage == force and play.rng.exhausted()
    assert result.displacement is not None and not result.displacement.checks
    assert result.displacement.potential_yards == (1 if force else 0)


async def test_stance_reuses_the_actual_defense_exertion_check(tmp_path: Path) -> None:
    _, play, state, command = await game(tmp_path, stance=True)
    state = replace_pool(state, pool(state, "fp:b").model_copy(update={"current": 0}))
    declared, _ = declare(play, state, command)
    # One successful Will, failed Dodge, two force dice, then the Stance check.
    play.rng = RecordedDice([2, 2, 2, 3, 3, 3, 5, 5, 1, 1, 1])
    _, result = defend_push(
        play.rules_context,
        declared,
        response(play, declared, defense="dodge", stance=True),
        authorized_actor_id="b",
    )
    assert result.defense is not None and not result.defense.outcome.succeeded
    assert result.displacement is not None and result.displacement.prevented
    assert play.rng.exhausted()


async def test_unconscious_target_uses_hp_and_cannot_select_defense_or_stance(
    tmp_path: Path,
) -> None:
    _, play, state, command = await game(tmp_path, stance=True)
    hp = pool(state, "hp:b")
    assert hp.injury is not None
    state = replace_pool(
        state,
        hp.model_copy(
            update={
                "maximum": 14,
                "current": 14,
                "injury": hp.injury.model_copy(update={"unconscious": True}),
            }
        ),
    )
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([])
    for selected in (
        response(play, declared, stance=True),
        response(play, declared, defense="dodge"),
    ):
        with pytest.raises(ValidationError):
            defend_push(play.rules_context, declared, selected, authorized_actor_id="b")
    play.rng = RecordedDice([5, 5])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert play.rng.exhausted() and result.displacement is not None
    assert result.displacement.potential_yards == 1 and not result.displacement.checks
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=2, y=0)
    assert pool(changed, "hp:b").current == 14


@pytest.mark.parametrize("prior", ["all_out_attack", "evaluate", "feint"])
async def test_ordinary_push_replaces_previous_maneuver_but_keeps_applicable_setup(
    tmp_path: Path, prior: Literal["all_out_attack", "evaluate", "feint"]
) -> None:
    _, play, state, command = await game(tmp_path)
    encounter = state.encounters[0]
    actor = fighter(encounter, "a").model_copy(
        update={
            "last_maneuver": prior,
            "maneuver_state": ManeuverState(
                attack_bonus=4,
                defense_forbidden=True,
                evaluate_target_id="b",
                evaluate_bonus=2,
                feint_target_id="b",
                feint_penalty=3,
            ),
        }
    )
    encounter = CombatEngine._replace(encounter, actor)
    state = state.model_copy(update={"encounters": (encounter,)})
    declared, result = declare(play, state, command)
    assert result.attack is not None and result.attack.effective_target == (
        15 if prior == "evaluate" else 13
    )
    assert not fighter(declared.encounters[0], "a").maneuver_state.defense_forbidden
    play.rng = RecordedDice([2, 2, 2] + ([5, 5, 3, 3, 3] if prior == "feint" else []))
    changed, result = defend_push(
        play.rules_context,
        declared,
        response(play, declared, defense="dodge"),
        authorized_actor_id="b",
    )
    assert play.rng.exhausted() and result.defense is not None
    assert result.defense.effective_target == (5 if prior == "feint" else 8)
    assert result.basic_damage == (18 if prior == "feint" else 0)
    assert fighter(changed.encounters[0], "a").maneuver_state == ManeuverState()


@pytest.mark.parametrize("side", ["a", "b"])
async def test_authority_is_checked_before_exposing_pending_results(
    tmp_path: Path, side: str
) -> None:
    _, play, state, command = await game(tmp_path)
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([])
    selected = response(play, declared)
    if side == "a":
        selected = selected.model_copy(
            update={
                "actor_id": "a",
                "build_revision": play.rules_context.approved_build(declared, "a").revision,
            }
        )
    with pytest.raises(AuthorizationError):
        defend_push(play.rules_context, declared, selected, authorized_actor_id="a")


async def test_new_id_cannot_resolve_already_finished_push_again(tmp_path: Path) -> None:
    _, play, state, command = await game(tmp_path)
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([5, 5, 3, 3, 3])
    changed, _ = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    play.rng = RecordedDice([])
    with pytest.raises(ConflictError, match="no longer awaiting"):
        defend_push(
            play.rules_context,
            changed,
            response(play, changed, command_id="second-defense"),
            authorized_actor_id="b",
        )


async def test_target_approval_preflight_happens_before_attack_roll(tmp_path: Path) -> None:
    _, play, state, command = await game(tmp_path)
    state = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"approval": None}) if a.actor_id == "b" else a
                for a in state.actors
            )
        }
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="approval"):
        declare_push(play.rules_context, state, command, authorized_actor_id="a")


async def test_same_square_push_uses_recorded_facing_and_clears_close_relation(
    tmp_path: Path,
) -> None:
    _, play, state, command = await game(tmp_path)
    encounter = state.encounters[0]
    encounter = CombatEngine._replace(
        encounter,
        fighter(encounter, "a").model_copy(
            update={"position": GridPoint(x=2, y=2), "facing": "east"}
        ),
    )
    encounter = CombatEngine._replace(
        encounter, fighter(encounter, "b").model_copy(update={"position": GridPoint(x=2, y=2)})
    )
    encounter = encounter.model_copy(update={"close_pairs": (("a", "b"),)})
    state = state.model_copy(update={"encounters": (encounter,)})
    command = command.model_copy(update={"enter_close_combat": False})
    declared, result = declare(play, state, command)
    assert result.pending is not None and result.pending.attack_direction == (1, 0)
    play.rng = RecordedDice([5, 5, 3, 3, 3])
    changed, _ = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=4, y=2)
    assert not changed.encounters[0].close_pairs


async def test_combined_power_blow_push_is_explicitly_unsupported_before_randomness(
    tmp_path: Path,
) -> None:
    from test_power_blow_combat import POWER

    from wayfarer.engine.simulation.skills.power_blow import PowerBlowCommand, activate_power_blow

    _, play, state, command = await game(
        tmp_path,
        extra_definitions=POWER,
        extra_purchases=(Purchase(definition_id="skill:power-blow", amount=48),),
    )
    power = PowerBlowCommand(
        id="power",
        actor_id="a",
        expected_revision=state.resources.revision,
        build_revision=command.build_revision,
        encounter_id="fight",
        attack_command_id="push",
    )
    play.rng = RecordedDice([2, 2, 2])
    prepared, _ = activate_power_blow(play.rules_context, state, power, authorized_actor_id="a")
    before = prepared.model_dump_json()
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Combining Power Blow"):
        declare_push(
            play.rules_context,
            prepared,
            command.model_copy(update={"expected_revision": prepared.resources.revision}),
            authorized_actor_id="a",
        )
    assert prepared.model_dump_json() == before


async def test_successful_zero_fp_exertion_allows_one_push_and_no_additional_fp_cost(
    tmp_path: Path,
) -> None:
    _, play, state, command = await game(tmp_path)
    state = replace_pool(state, pool(state, "fp:a").model_copy(update={"current": 0}))
    play.rng = RecordedDice([2, 2, 2, 3, 3, 3])
    declared, result = declare_push(play.rules_context, state, command, authorized_actor_id="a")
    assert play.rng.exhausted() and result.stage == "defense-required"
    fp = pool(declared, "fp:a")
    assert fp.current == 0 and fp.fatigue is not None and not fp.fatigue.collapsed
    play.rng = RecordedDice([5, 5, 3, 3, 3])
    changed, _ = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert fighter(changed.encounters[0], "b").position == GridPoint(x=3, y=0)
    assert pool(changed, "fp:a").current == 0


async def test_critical_hit_disarms_actual_target_without_wounding(tmp_path: Path) -> None:
    _, play, state, command = await game(tmp_path, shield=True)
    play.rng = RecordedDice([1, 1, 1, 4, 4, 4])
    declared, _ = declare_push(play.rules_context, state, command, authorized_actor_id="a")
    play.rng = RecordedDice([5, 5, 3, 3, 3])
    changed, result = defend_push(
        play.rules_context, declared, response(play, declared), authorized_actor_id="b"
    )
    assert play.rng.exhausted() and result.basic_damage == 18
    assert not fighter(changed.encounters[0], "b").hand_bindings
    assert all(
        not i.ready and not i.equipped
        for i in changed.resources.items
        if i.id in ("sword-b", "shield-b")
    )
    assert pool(changed, "hp:b").current == 10


async def test_unrepresented_critical_miss_pauses_without_inventing_consequences(
    tmp_path: Path,
) -> None:
    _, play, state, command = await game(tmp_path)
    play.rng = RecordedDice([6, 6, 6, 1, 1, 1])
    changed, result = declare_push(play.rules_context, state, command, authorized_actor_id="a")
    assert play.rng.exhausted() and result.stage == "missed"
    assert changed.encounters[0].blocked_reason == "Push critical miss requires adjudication"
    assert changed.encounters[0].current_actor_id == "a"
    assert result.critical_table == (1, 1, 1)
    assert pool(changed, "hp:a").current == 10


async def test_judo_parry_keeps_existing_followup_opportunity(tmp_path: Path) -> None:
    _, play, state, command = await game(tmp_path)
    state, encounter = drop_held(state, state.encounters[0], "b")
    state = state.model_copy(update={"encounters": (encounter,)})
    declared, _ = declare(play, state, command)
    play.rng = RecordedDice([2, 2, 2])
    changed, _ = defend_push(
        play.rules_context,
        declared,
        response(play, declared, defense="parry", item_id="left-hand"),
        authorized_actor_id="b",
    )
    assert fighter(changed.encounters[0], "b").unarmed_lock_opportunity == ("a", "skill:judo", 1)
    assert pool(changed, "hp:a").current == pool(changed, "hp:b").current == 10
