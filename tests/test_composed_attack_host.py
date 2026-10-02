"""B61/B102/B106/B201/B375: persisted approved-source authority and consequences."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_basic_combat import start_basic
from test_combat_sensory_authority import change
from test_composed_attacks import pick
from test_gurps_melee import setup
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.attack_defense import RUNTIME_HOOKS, package
from wayfarer.engine.rules.traits.modifiers import ModifierSelection
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy
from wayfarer.engine.simulation.resources import Item
from wayfarer.engine.simulation.traits.composed_host import UseComposedAttack
from wayfarer.engine.simulation.traits.composed_sources import BindComposedSource
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.play import PlayService


async def fixture(
    path: Path,
    backend: str = "sqlite",
    *,
    modifiers: tuple[ModifierSelection, ...] = (),
    kind: str = "burn",
    distance: float = 2,
    dr: int = 0,
    skill_points: int = 0,
    levels: int = 2,
    armor: bool = False,
    bind_source: bool = True,
    cyclic_policy: CyclicPolicy | None = None,
    contagion_vector: Literal["blood", "contact", "digestive", "respiratory"] | None = None,
    incubation_seconds: int = 86400,
    legacy_modifiers: tuple[str, ...] = (),
) -> tuple[str, PlayService, str]:
    purchase = Purchase(
        definition_id="advantage:innate-attack",
        amount=levels,
        trait=options(**{"damage-type": kind}).model_copy(
            update={"attack_modifiers": modifiers, "modifiers": legacy_modifiers}
        ),
    )
    innate = RuleDefinition(
        "skill:innate-attack-beam",
        DefinitionKind.SKILL,
        "Innate Attack (Beam)",
        package().definitions[0].source_id,
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.DX, Difficulty.EASY, "B201"),
    )
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        trained=False,
        scene_bound=True,
        start_encounter=False,
        extra_definitions=package().definitions + (innate,),
        trait_runtime_hooks=RUNTIME_HOOKS,
        extra_items=(
            Item(
                id="armor-b", definition_id="equipment:leather-armor", owner_id="b", equipped=True
            ),
        )
        if armor
        else (),
        extra_purchases=(purchase,)
        + ((Purchase(definition_id=innate.id, amount=skill_points),) if skill_points else ())
        + ((Purchase(definition_id="advantage:damage-resistance", amount=dr),) if dr else ()),
    )
    await CombatService(original).execute(
        cid, start_basic(distance, ranged=True), principal_id="gm"
    )
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "members": state.members
                + (
                    CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
                    CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
                )
            }
        ),
    )
    state = play._load(await play.store.read(cid))
    if not bind_source:
        return cid, play, "unbound"
    result = await ComposedAttackService(play).execute(
        cid,
        BindComposedSource(
            id="bind",
            actor_id="a",
            expected_revision=state.revision,
            description="A directed natural burning beam",
            specialty="beam",
            cyclic_policy=cyclic_policy,
            contagion_vector=contagion_vector,
            incubation_seconds=incubation_seconds,
        ),
        principal_id="gm",
    )
    assert result.source_id is not None
    return cid, play, result.source_id


async def declare(
    play: PlayService, cid: str, source: str, *, kind: str = "declare", identifier: str = "attack"
) -> UseComposedAttack:
    state = play._load(await play.store.read(cid))
    command = UseComposedAttack.model_validate(
        {
            "id": identifier,
            "actor_id": "a",
            "expected_revision": state.revision,
            "kind": kind,
            "encounter_id": "fight",
            "source_id": source,
            "target_id": "b",
        }
    )
    await ComposedAttackService(play).execute(cid, command, principal_id="alice")
    return command


async def defense(
    play: PlayService, cid: str, *, selected: str = "none", principal: str = "bob"
) -> tuple[ChooseDefense, CombatResult]:
    state = play._load(await play.store.read(cid))
    command = ChooseDefense.model_validate(
        {
            "id": "defend",
            "actor_id": "b",
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "defense": selected,
        }
    )
    return command, await CombatService(play).execute(cid, command, principal_id=principal)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_persisted_composed_source_uses_real_turn_target_authority_damage_and_restart(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    command = await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and pending.composed_attack_id and pending.attack_roll is None
    assert state.resources.items == before.resources.items
    hp_a = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp_a.injury and hp_a.injury.turn == 1
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert state.encounters[0].participants[0].last_maneuver == "attack"
    with pytest.raises(AuthorizationError):
        await defense(play, cid, principal="alice")
    assert play._load(await play.store.read(cid)) == state
    play.rng = RecordedDice((2, 2, 2, 3, 3, 2, 2, 2))
    defend_command, result = await defense(play, cid)
    assert result.injury and result.injury.injury == 6
    assert result.current_actor_id == "b"
    after = play._load(await play.store.read(cid))
    assert after.encounters[0].pending_defense is None
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 4
    assert play.rng.exhausted()
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await CombatService(restarted).execute(cid, defend_command, principal_id="bob") == result
    assert await ComposedAttackService(restarted).execute(cid, command, principal_id="alice")
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


async def test_purchased_divisor_applies_to_current_natural_dr(tmp_path: Path) -> None:
    cid, play, source = await fixture(
        tmp_path, modifiers=(pick("enhancement:armor-divisor", option="2"),), dr=5
    )
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2, 3, 3))
    _, result = await defense(play, cid)
    assert result.injury and result.injury.injury == 4 and result.injury.resistance == 2
    assert play.rng.exhausted()


async def idle(play: PlayService, cid: str, actor: str = "b") -> None:
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="idle-" + str(state.revision),
            actor_id=actor,
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id=actor,
    )


@pytest.mark.parametrize(
    ("modifiers", "target", "damage"),
    [
        ((), 9, 0),
        ((pick("enhancement:accurate", level=2),), 11, 4),
        ((pick("limitation:inaccurate"),), 8, 0),
    ],
)
async def test_accuracy_uses_an_actual_matching_spent_aim(
    tmp_path: Path, modifiers: tuple[ModifierSelection, ...], target: int, damage: int
) -> None:
    cid, play, source = await fixture(tmp_path, modifiers=modifiers)
    await declare(play, cid, source, kind="aim", identifier="aim")
    aimed = play._load(await play.store.read(cid))
    assert aimed.encounters[0].current_actor_id == "b"
    assert (
        next(
            p for p in aimed.encounters[0].participants if p.actor_id == "a"
        ).maneuver_state.aim_seconds
        == 1
    )
    await idle(play, cid)
    await declare(play, cid, source)
    play.rng = RecordedDice((3, 3, 4) + ((2, 2) if damage else ()))
    _, result = await defense(play, cid)
    assert result.injury and result.injury.attack.effective_target == target
    assert result.injury.injury == damage and play.rng.exhausted()


@pytest.mark.parametrize("distance", [9.9, 10.0, 10.1])
async def test_fractional_range_uses_exact_threshold_without_truncation(
    tmp_path: Path, distance: float
) -> None:
    cid, play, source = await fixture(tmp_path, skill_points=16, distance=distance)
    await declare(play, cid, source)
    play.rng = RecordedDice((3, 3, 3, 2, 3))
    _, result = await defense(play, cid)
    assert result.injury and result.injury.basic_damage == (5 if distance < 10 else 2)
    assert result.injury.injury == (5 if distance < 10 else 2)
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    ("modifiers", "maximum"),
    [
        ((), 100.0),
        ((pick("enhancement:increased-range"),), 200.0),
        ((pick("limitation:reduced-range"),), 50.0),
    ],
)
async def test_approved_max_range_rejects_before_dice_and_spending(
    tmp_path: Path, modifiers: tuple[ModifierSelection, ...], maximum: float
) -> None:
    cid, play, source = await fixture(
        tmp_path, skill_points=16, modifiers=modifiers, distance=maximum + 0.1
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="range"):
        await declare(play, cid, source)
    assert await play.store.read(cid) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


async def test_current_worn_plus_natural_dr_and_changed_equipment(tmp_path: Path) -> None:
    cid, play, source = await fixture(
        tmp_path, modifiers=(pick("enhancement:armor-divisor", option="2"),), dr=4, armor=True
    )
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2, 3, 3))
    _, result = await defense(play, cid)
    assert result.injury and result.injury.resistance == 3 and result.injury.injury == 3
    assert play.rng.exhausted()


@pytest.mark.parametrize(("loaded", "expected"), [(False, 8), (True, 6)])
async def test_dodge_scores_current_encumbrance_not_compiled_base(
    tmp_path: Path, loaded: bool, expected: int
) -> None:
    cid, play, source = await fixture(tmp_path)

    # Remove shield DB and add 50lb in existing canonical leather units: Medium at ST10.
    def equipment(state: PlayState) -> PlayState:
        resources = state.resources.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"equipped": False, "ready": False})
                    if i.id == "shield-b"
                    else i
                    for i in state.resources.items
                )
                + (
                    tuple(
                        Item(
                            id="burden-" + str(i),
                            definition_id="equipment:leather-armor",
                            owner_id="b",
                        )
                        for i in range(5)
                    )
                    if loaded
                    else ()
                )
            }
        )
        return state.model_copy(
            update={
                "resources": resources,
                "actors": tuple(
                    a.model_copy(
                        update={
                            "held_item_hands": tuple(
                                (i, h) for i, h in a.held_item_hands if i != "shield-b"
                            )
                        }
                    )
                    for a in state.actors
                ),
                "encounters": tuple(
                    e.model_copy(
                        update={
                            "participants": tuple(
                                p.model_copy(
                                    update={
                                        "ready_item_ids": tuple(
                                            i for i in p.ready_item_ids if i != "shield-b"
                                        ),
                                        "hand_bindings": tuple(
                                            (i, h) for i, h in p.hand_bindings if i != "shield-b"
                                        ),
                                    }
                                )
                                for p in e.participants
                            )
                        }
                    )
                    for e in state.encounters
                ),
            }
        )

    await change(play, cid, equipment)
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2, 2, 2, 3) + ((2, 2) if loaded else ()))
    _, result = await defense(play, cid, selected="dodge")
    assert (
        result.injury
        and result.injury.defense
        and result.injury.defense.effective_target == expected
    )
    assert result.injury.injury == (4 if loaded else 0) and play.rng.exhausted()


@pytest.mark.parametrize("resist", [False, True])
async def test_target_controlled_malediction_spends_concentrate_bypasses_dr(
    tmp_path: Path, resist: bool
) -> None:
    from wayfarer.engine.simulation.traits.composed_host import ResistComposedAttack

    cid, play, source = await fixture(
        tmp_path,
        modifiers=(pick("enhancement:malediction", option="1"),),
        dr=20,
        distance=0.1,
        kind="tox",
    )
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    attacker = next(p for p in state.encounters[0].participants if p.actor_id == "a")
    assert attacker.last_maneuver == "concentrate" and attacker.maneuver_state.concentrating
    with pytest.raises(ValidationError, match="resistance"):
        await defense(play, cid)
    command = ResistComposedAttack(
        id="resist",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        pending_id=pending.id,
        resist=resist,
    )
    with pytest.raises(AuthorizationError):
        await ComposedAttackService(play).execute(cid, command, principal_id="alice")
    play.rng = RecordedDice((2, 2, 2) + ((4, 4, 4) if resist else ()) + (2, 2))
    result = await ComposedAttackService(play).execute(cid, command, principal_id="bob")
    assert result.attack and result.attack.injury and result.attack.injury.injury == 4
    assert result.attack.checks[0].effective_target == 9  # B9 floor(-0.1) = -1, B106.
    assert result.attack.injury.effective_resistance == 0 and play.rng.exhausted()


async def test_stale_range_keeps_spent_commitment_and_abandonment_works(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.combat.abandon import AbandonPendingAttack
    from wayfarer.orchestration.combat.abandon import AbandonPendingAttackService

    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)

    def move_target(state: PlayState) -> PlayState:
        encounter = state.encounters[0]
        assert isinstance(encounter.spatial, BasicSpatialContext)
        spatial = encounter.spatial.model_copy(
            update={
                "facts": tuple(
                    f.model_copy(update={"yards": 101.0}) if f.kind == "distance" else f
                    for f in encounter.spatial.facts
                )
            }
        )
        return state.model_copy(
            update={"encounters": (encounter.model_copy(update={"spatial_context": spatial}),)}
        )

    await change(play, cid, move_target)
    before = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="range"):
        await defense(play, cid)
    pending = before.encounters[0].pending_defense
    assert pending and isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    command = AbandonPendingAttack(
        id="abandon",
        actor_id="a",
        expected_revision=before.revision,
        encounter_id="fight",
        pending_id=pending.id,
    )
    with pytest.raises(AuthorizationError):
        await AbandonPendingAttackService(play).execute(cid, command, principal_id="bob")
    result = await AbandonPendingAttackService(play).execute(cid, command, principal_id="alice")
    assert result.current_actor_id == "b"
    after = play._load(await play.store.read(cid))
    assert after.encounters[0].pending_defense is None
    hp_a = next(p for p in after.resources.pools if p.id == "hp:a")
    assert hp_a.injury and hp_a.injury.turn == 1
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10
    await idle(play, cid)
    assert play._load(await play.store.read(cid)).resources.game_time == 1


def table_dice(total: int) -> tuple[int, int, int]:
    return next(
        (a, b, total - a - b) for a in range(1, 7) for b in range(1, 7) if 1 <= total - a - b <= 6
    )


@pytest.mark.parametrize(
    ("row", "loss"),
    list(zip(range(3, 19), (8, 3, 5, 5, 2, 2, 2, 2, 2, 2, 2, 2, 5, 5, 3, 8), strict=True)),
)
async def test_every_body_critical_row_applies_real_consequences(
    tmp_path: Path, row: int, loss: int
) -> None:
    cid, play, source = await fixture(tmp_path, dr=1, levels=1)
    await declare(play, cid, source)
    major = loss > 5 or row in (7, 13, 14)
    play.rng = RecordedDice(
        (1, 1, 1)
        + table_dice(row)
        + (() if row in (6, 15) else (3,))
        + ((2, 2, 2) if major else ())
    )
    _, result = await defense(play, cid, selected="dodge")
    assert result.injury and result.injury.injury == loss
    assert result.injury.defense is None and result.injury.critical_table == table_dice(row)
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.current == 10 - loss
    if row == 8:
        assert hp.injury and hp.injury.shock == 4
    if row == 12:
        assert {i.id for i in state.resources.items if i.world_ground_location_id == "dock"} == {
            "sword-b",
            "shield-b",
        }
        assert not next(
            p for p in state.encounters[0].participants if p.actor_id == "b"
        ).hand_bindings
    assert play.rng.exhausted()


async def test_ranged_failure_by_ten_does_not_draw_miss_table(tmp_path: Path) -> None:
    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)
    play.rng = RecordedDice((5, 5, 6))  # skill6/16: ordinary ranged miss (B382).
    _, result = await defense(play, cid)
    assert result.injury and result.injury.attack.outcome.value == "failure"
    assert result.injury.injury == 0 and play.rng.exhausted()


async def test_critical_dodge_failure_causes_prone_without_a_miss_table(tmp_path: Path) -> None:
    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2, 6, 6, 6, 2, 2))
    _, result = await defense(play, cid, selected="dodge")
    state = play._load(await play.store.read(cid))
    assert result.injury and result.injury.injury == 4
    assert next(p for p in state.encounters[0].participants if p.actor_id == "b").posture == "prone"
    assert play.rng.exhausted()


async def test_natural_critical_has_immutable_dice_and_executable_gm_continuation(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.traits.composed_host import ContinueComposedCritical
    from wayfarer.engine.simulation.traits.composed_resolution import (
        RESOLUTION_PREFIX,
        ComposedResolution,
    )
    from wayfarer.engine.simulation.traits.innate_criticals import load_innate_critical

    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)
    play.rng = RecordedDice((6, 6, 6, 3, 3, 3))
    defender_command, response = await defense(play, cid)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and pending.attack_roll and state.encounters[0].blocked_reason
    resolution = next(
        ComposedResolution.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith(RESOLUTION_PREFIX)
    )
    assert resolution.critical_id
    captured = load_innate_critical(state.resources, resolution.critical_id)
    assert captured and captured.table_rolls == ((3, 3, 3),)
    assert play.rng.exhausted()
    # The GM changes the description after the captured roll. Continuation still uses history.
    await ComposedAttackService(play).execute(
        cid,
        BindComposedSource(
            id="rebind",
            actor_id="a",
            expected_revision=state.revision,
            description="Revised natural description",
            specialty="gaze",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    continuation = ContinueComposedCritical(
        id="continue",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        pending_id=pending.id,
        critical_id=resolution.critical_id,
        context_digest=captured.digest,
        policy_id="campaign-natural-power-critical-policy",
        reason="GM explicitly substitutes temporary loss of balance for a natural source that cannot be dropped",
        effect="lose-balance",
    )
    with pytest.raises(ValidationError, match="director"):
        await ComposedAttackService(play).execute(cid, continuation, principal_id="alice")
    result = await ComposedAttackService(play).execute(cid, continuation, principal_id="gm")
    assert result.combat and result.combat.current_actor_id == "b"
    after = play._load(await play.store.read(cid))
    assert (
        after.encounters[0].pending_defense is None and after.encounters[0].blocked_reason is None
    )
    assert (
        next(p for p in after.encounters[0].participants if p.actor_id == "a").defense_penalty == -2
    )
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 10
    restarted = build_play(tmp_path, play.engine, rng=RecordedDice(()))
    assert (
        await ComposedAttackService(restarted).execute(cid, continuation, principal_id="gm")
        == result
    )
    assert (
        await CombatService(restarted).execute(cid, defender_command, principal_id="bob")
        == response
    )
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)
