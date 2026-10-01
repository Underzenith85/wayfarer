"""Independent selected-printing oracles: Characters third B61-62/B201,
Campaigns fourth B381-382/B556-557. GM substitutions are tested as adjudication,
never as an automatic selected-printing interpretation of natural weapons.
"""

from decimal import Decimal
from typing import Literal

import pytest

from wayfarer.engine.character.traits.attack_defense import (
    AttackDefenseTraits,
    PurchasedAttackDefenseTrait,
)
from wayfarer.engine.rules.checks import CheckTrace, Outcome, RecordedDice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.traits.modifiers import AttackProfile
from wayfarer.engine.rules.traits.physical import PhysicalTraits
from wayfarer.engine.rules.types.cyclic import CyclicAttack
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.rules.types.symptoms import SymptomSpec
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.critical import TableRoll
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter, PendingDefense
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.health.cyclic import save as save_cyclic
from wayfarer.engine.simulation.health.cyclic import settle as settle_cyclic
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import (
    DisableLocation,
    Wound,
    apply_injury,
    apply_location_effect,
)
from wayfarer.engine.simulation.resources import Item, Pool, ResourceState
from wayfarer.engine.simulation.traits.innate_criticals import (
    PROFILE,
    InnateAdjudication,
    InnateCriticalContext,
    InnateCriticalSource,
    apply_fatigue_major_wound,
    body_hit_effects,
    classify_ranged_check,
    continue_innate_miss,
    critical_dodge_failure,
    drop_all_held,
    load_innate_critical,
    require_innate_action,
    resolve_innate_miss,
)
from wayfarer.errors import ConflictError, ValidationError


def table(total: int) -> TableRoll:
    return next(
        (a, b, c)
        for a in range(1, 7)
        for b in range(1, 7)
        for c in range(1, 7)
        if a + b + c == total
    )


def initial(*, hp: int = 10, fp: int = 10, items: tuple[Item, ...] = ()) -> ResourceState:
    return ResourceState(
        items=items,
        pools=tuple(
            Pool(
                id="hp:" + actor,
                current=hp,
                maximum=hp,
                injury=InjuryStatus(profile_id=PROFILE, anatomy="human"),
            )
            for actor in ("a", "b")
        )
        + tuple(
            Pool(
                id="fp:" + actor, current=fp, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)
            )
            for actor in ("a", "b")
        ),
    )


def fight(*, basic: bool = False, held_by: str | None = None) -> Encounter:
    return Encounter(
        id="fight",
        legacy_battlefield_id=None if basic else "room",
        spatial_context=BasicSpatialContext() if basic else None,
        participants=tuple(
            Combatant(
                actor_id=actor,
                initiative=5,
                reach=1,
                movement_allowance=5,
                position=None if basic else GridPoint(x=x, y=1),
                ready_item_ids=("torch", "weapon") if actor == held_by else (),
                hand_bindings=(("torch", "left-hand"), ("weapon", "right-hand"))
                if actor == held_by
                else (),
            )
            for actor, x in (("a", 1), ("b", 2))
        ),
        turn_order=("a", "b"),
        pending_defense=PendingDefense(
            id="pending",
            attacker_id="a",
            defender_id="b",
            weapon_id="source:a",
            allowed=("dodge", "none"),
            opened_round=1,
            opened_turn=0,
        ),
    )


def objects(actor: str) -> tuple[Item, ...]:
    return (
        Item(id="torch", definition_id="torch", owner_id=actor, equipped=True, ready=True),
        Item(
            id="weapon",
            definition_id="weapon",
            owner_id=actor,
            equipped=True,
            ready=True,
            condition=ObjectCondition(hp=10),
        ),
        Item(id="armor", definition_id="armor", owner_id=actor, equipped=True),
    )


def context(
    specialty: Literal["beam", "breath", "gaze", "projectile"] = "beam",
    *,
    source: InnateCriticalSource | None = None,
    held: bool = False,
) -> InnateCriticalContext:
    return InnateCriticalContext(
        id="critical",
        campaign_id="campaign",
        encounter_id="fight",
        pending_id="pending",
        attacker_id="a",
        target_id="b",
        location_id="room",
        source=source
        or InnateCriticalSource(
            source_id="source:a",
            build_revision="approved:a",
            description_digest="a" * 64,
            specialty=specialty,
            damage_dice=1,
            damage_type="burn",
        ),
        target_build_revision="approved:b",
        created_at=0,
        attacker_ht=10,
        attacker_dx=10,
        limb_dr=(("left-arm", 1), ("right-arm", 1), ("left-leg", 1), ("right-leg", 1)),
        held_item_ids=("torch", "weapon") if held else (),
        hand_bindings=(("torch", "left-hand"), ("weapon", "right-hand")) if held else (),
    )


def miss() -> CheckTrace:
    return success_roll(PROFILE, 10, rng=RecordedDice([6, 6, 6]))


@pytest.mark.parametrize(
    "skill,total,expected",
    [
        (6, 16, Outcome.FAILURE),
        (15, 17, Outcome.CRITICAL_FAILURE),
        (16, 17, Outcome.FAILURE),
        (16, 18, Outcome.CRITICAL_FAILURE),
        (14, 5, Outcome.SUCCESS),
        (15, 5, Outcome.CRITICAL_SUCCESS),
        (16, 6, Outcome.CRITICAL_SUCCESS),
    ],
)
def test_b382_ranged_threshold_uses_captured_roll_without_entropy(
    skill: int, total: int, expected: Outcome
) -> None:
    check = success_roll(PROFILE, skill, rng=RecordedDice(table(total)))
    actual = classify_ranged_check(check)
    assert actual.outcome is expected
    assert (
        actual.dice == check.dice and actual.total == check.total and actual.margin == check.margin
    )


@pytest.mark.parametrize(
    "row,expected",
    list(zip(range(3, 19), (8, 3, 5, 5, 2, 2, 2, 2, 2, 2, 2, 2, 5, 5, 3, 8), strict=True)),
)
def test_b556_every_body_row_causes_actual_expected_injury(row: int, expected: int) -> None:
    state = initial(hp=30)
    effects = body_hit_effects(table(row))
    # The host uses no ordinary damage draw on maximum rows6/15.
    basic = (6 if effects.maximum_damage else 3) * effects.basic_multiplier
    updated, result = apply_injury(
        state,
        Wound(
            id="critical-hit",
            actor_id="b",
            expected_revision=0,
            basic_damage=basic,
            resistance=1,
            damage_type="burn",
        ),
        ht=10,
        rng=RecordedDice([3, 4, 4] if row in (7, 13, 14) else []),
        system=True,
        force_major_wound=effects.force_major_wound,
        double_shock=effects.double_shock,
        halve_dr=effects.halve_dr,
    )
    hp = next(p for p in updated.pools if p.id == "hp:b")
    assert result.injury == expected and hp.current == 30 - expected
    assert hp.injury and hp.injury.stunned is (row in (7, 13, 14))
    assert hp.injury.prone is (row in (7, 13, 14))
    assert tuple(c.reason for c in result.checks) == (
        ("major-wound",) if row in (7, 13, 14) else ()
    )


def test_b556_post_divisor_half_dr_preserves_intermediate_rounding() -> None:
    state, result = apply_injury(
        initial(hp=30),
        Wound(
            id="half-dr",
            actor_id="b",
            expected_revision=0,
            basic_damage=6,
            resistance=7,
            armor_divisor=Decimal(2),
            damage_type="burn",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
        halve_dr=body_hit_effects(table(4)).halve_dr,
    )
    assert result.effective_resistance == 1 and result.injury == 5
    assert next(p.current for p in state.pools if p.id == "hp:b") == 25


def test_b556_double_shock_uses_hp_scaling_and_cap() -> None:
    for hp, basic, shock in ((10, 2, 4), (10, 5, 8), (30, 2, 0), (30, 6, 4)):
        updated, _ = apply_injury(
            initial(hp=hp),
            Wound(
                id="shock",
                actor_id="b",
                expected_revision=0,
                basic_damage=basic,
                resistance=0,
                damage_type="burn",
            ),
            ht=10,
            rng=RecordedDice([]),
            system=True,
            double_shock=True,
        )
        actual = next(p for p in updated.pools if p.id == "hp:b")
        assert actual.injury and actual.injury.shock == shock


@pytest.mark.parametrize("basic", [0, 3])
@pytest.mark.parametrize("mapless", [False, True])
def test_b556_drop_happens_without_penetration_and_preserves_armor(
    basic: int, mapless: bool
) -> None:
    state = initial(items=objects("b"))
    encounter = fight(basic=mapless, held_by="b")
    state, wound = apply_injury(
        state,
        Wound(
            id="zero",
            actor_id="b",
            expected_revision=0,
            basic_damage=basic,
            resistance=20,
            damage_type="burn",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert wound.penetration == wound.injury == 0
    state, encounter, dropped = drop_all_held(state, encounter, "b", location_id="room")
    assert dropped == ("torch", "weapon")
    for item in state.items[:2]:
        assert not item.ready and not item.equipped and item.container_id is None
        if mapless:
            assert item.world_ground_location_id == "room" and item.ground is None
        else:
            assert item.ground and (item.ground.x, item.ground.y) == (2, 1)
    assert state.items[2].equipped and state.items[2].ground is None
    assert encounter.participants[1].hand_bindings == ()
    assert encounter.participants[1].ready_item_ids == ()
    assert next(p.current for p in state.pools if p.id == "hp:b") == 10
    assert drop_all_held(state, encounter, "b", location_id="room") == (state, encounter, ())


def test_b556_ranged_self_hit_rerolls_once_then_halves_basic_before_dr() -> None:
    state, encounter, result = resolve_innate_miss(
        initial(),
        fight(),
        context(),
        miss(),
        rng=RecordedDice([*table(5), *table(6), 1, 1, 5]),
        system=True,
    )
    assert result.status == "resolved" and result.effect == "self-hit"
    assert result.location == "right-arm" and result.location_dice == (1, 1)
    assert result.damage_dice == (5,) and result.injury == 1
    assert next(p.current for p in state.pools if p.id == "hp:a") == 9
    captured = load_innate_critical(state, "critical")
    assert captured and captured.table_rolls == (table(5), table(6))
    assert state.items == ()
    assert resolve_innate_miss(
        state, encounter, context(), miss(), rng=RecordedDice([]), system=True
    ) == (state, encounter, result)


@pytest.mark.parametrize("specialty", ["beam", "breath", "gaze", "projectile"])
@pytest.mark.parametrize("row", [7, 13, 16])
def test_b556_balance_forbids_free_actions_but_first_next_turn_is_allowed(
    specialty: Literal["beam", "breath", "gaze", "projectile"], row: int
) -> None:
    state, encounter, result = resolve_innate_miss(
        initial(), fight(), context(specialty), miss(), rng=RecordedDice(table(row)), system=True
    )
    assert result.effect == "lose-balance" and result.basis == "selected-printing"
    assert encounter.participants[0].posture == "standing"
    assert 8 + encounter.participants[0].defense_penalty == 6
    with pytest.raises(ValidationError, match="even free actions"):
        require_innate_action(state, encounter, "a")
    encounter = CombatEngine._advance(encounter.model_copy(update={"pending_defense": None}))
    with pytest.raises(ValidationError, match="even free actions"):
        require_innate_action(state, encounter, "a")
    encounter = CombatEngine._advance(encounter)
    # HP injury.turn has not started the next maneuver yet; no extra Do Nothing.
    require_innate_action(state, encounter, "a")
    assert encounter.current_actor_id == "a" and encounter.round == 2
    assert 8 + encounter.participants[0].defense_penalty == 8


def test_b556_self_hit_reroll_to_balance_has_no_damage_or_third_roll() -> None:
    state, encounter, result = resolve_innate_miss(
        initial(),
        fight(),
        context("gaze"),
        miss(),
        rng=RecordedDice([*table(5), *table(7)]),
        system=True,
    )
    assert result.effect == "lose-balance" and result.damage_dice == ()
    assert next(p.current for p in state.pools if p.id == "hp:a") == 10
    assert encounter.participants[0].posture == "standing"


@pytest.mark.parametrize("row", [3, 4, 8, 9, 10, 11, 12, 14, 15, 17, 18])
def test_natural_incompatible_rows_resume_as_explicit_campaign_policy(row: int) -> None:
    original = initial()
    state, encounter, pending = resolve_innate_miss(
        original, fight(), context("breath"), miss(), rng=RecordedDice(table(row)), system=True
    )
    assert pending.status == "awaiting-adjudication"
    assert state.items == () and state.pools == original.pools
    # Real restart through closed persisted records, with no new attack/table dice.
    state = ResourceState.model_validate_json(state.model_dump_json())
    encounter = Encounter.model_validate_json(encounter.model_dump_json())
    policy = InnateAdjudication(
        principal_id="gm",
        policy_id="campaign-natural-power-critical",
        reason="GM selects a 30-second interruption for this otherwise undefined natural-source result",
        effect="disable-source",
        duration_seconds=30,
    )
    state, encounter, resolved = continue_innate_miss(
        state,
        encounter,
        critical_id="critical",
        command_id="continue",
        context_digest=pending.context_digest,
        adjudication=policy,
        rng=RecordedDice([]),
        system=True,
    )
    assert resolved.status == "resolved" and resolved.basis == "gm-adjudication"
    assert resolved.adjudication == policy and resolved.disabled_until == 30
    assert encounter.blocked_reason is None and encounter.pending_defense is not None
    assert state.pools == original.pools and state.items == ()
    with pytest.raises(ValidationError, match="still disabled"):
        require_innate_action(state, encounter, "a", "source:a")
    require_innate_action(state, encounter, "a", "different-source")
    require_innate_action(state.model_copy(update={"game_time": 30}), encounter, "a", "source:a")
    assert continue_innate_miss(
        state,
        encounter,
        critical_id="critical",
        command_id="continue",
        context_digest=pending.context_digest,
        adjudication=policy,
        rng=RecordedDice([]),
        system=True,
    ) == (state, encounter, resolved)
    with pytest.raises(ConflictError, match="one committed consequence"):
        continue_innate_miss(
            state,
            encounter,
            critical_id="critical",
            command_id="changed",
            context_digest=pending.context_digest,
            adjudication=policy,
            rng=RecordedDice([]),
            system=True,
        )


def test_continuation_rejects_changed_identity_authority_and_chronology_before_entropy() -> None:
    state, encounter, pending = resolve_innate_miss(
        initial().model_copy(update={"game_time": 1}),
        fight(),
        context().model_copy(update={"created_at": 1}),
        miss(),
        rng=RecordedDice(table(3)),
        system=True,
    )
    policy = InnateAdjudication(
        principal_id="gm",
        policy_id="natural-break-policy",
        reason="Campaign ruling applies a balance loss",
        effect="lose-balance",
    )
    with pytest.raises(ValidationError, match="authority"):
        continue_innate_miss(
            state,
            encounter,
            critical_id="critical",
            command_id="c",
            context_digest=pending.context_digest,
            adjudication=policy,
            rng=RecordedDice([]),
        )
    with pytest.raises(ConflictError, match="identity"):
        continue_innate_miss(
            state,
            encounter,
            critical_id="critical",
            command_id="c",
            context_digest="f" * 64,
            adjudication=policy,
            rng=RecordedDice([]),
            system=True,
        )
    with pytest.raises(ConflictError, match="chronology"):
        continue_innate_miss(
            state.model_copy(update={"game_time": 0}),
            encounter,
            critical_id="critical",
            command_id="c",
            context_digest=pending.context_digest,
            adjudication=policy,
            rng=RecordedDice([]),
            system=True,
        )
    with pytest.raises(ConflictError, match="cannot change"):
        resolve_innate_miss(
            state,
            encounter,
            context().model_copy(update={"target_id": "a"}),
            miss(),
            rng=RecordedDice([]),
            system=True,
        )
    state, encounter, result = continue_innate_miss(
        state,
        encounter,
        critical_id="critical",
        command_id="c",
        context_digest=pending.context_digest,
        adjudication=policy,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.effect == "lose-balance" and result.basis == "gm-adjudication"
    with pytest.raises(ValidationError, match="even free"):
        require_innate_action(state, encounter, "a")


@pytest.mark.parametrize("damage_type", ["burn", "fat"])
@pytest.mark.parametrize("effect", ["lose-balance", "disable-source"])
def test_gm_continuation_preserves_intervening_cyclic_damage_body_and_item_changes(
    damage_type: Literal["burn", "fat"], effect: Literal["lose-balance", "disable-source"]
) -> None:
    state, encounter, pending = resolve_innate_miss(
        initial(items=objects("a")),
        fight(held_by="a"),
        context(held=True),
        miss(),
        rng=RecordedDice(table(3)),
        system=True,
    )
    captured = load_innate_critical(state, "critical")
    assert captured is not None
    due = CyclicAttack(
        id="other-attack",
        attacker_id="b",
        actor_id="a",
        attack_id="other-source",
        basic_damage=2,
        damage_dice=1,
        damage_type=damage_type,
        resistance=0,
        ht=10,
        interval=10,
        remaining=1,
        due=10,
        stop_condition="wash",
    )
    state = save_cyclic(state, due).model_copy(update={"game_time": 10})
    state = settle_cyclic(state, due, RecordedDice([2]))
    state, _ = apply_location_effect(
        state,
        DisableLocation(
            id="intervening-arm-injury",
            actor_id="a",
            expected_revision=state.revision,
            location="left-arm",
            duration_seconds=60,
        ),
        system=True,
    )
    state, encounter, dropped = drop_all_held(state, encounter, "a", location_id="room")
    assert dropped == ("torch", "weapon")
    state = ResourceState.model_validate_json(state.model_dump_json())
    encounter = Encounter.model_validate_json(encounter.model_dump_json())
    pool_id = ("fp:" if damage_type == "fat" else "hp:") + "a"
    assert next(p.current for p in state.pools if p.id == pool_id) == 8
    policy = InnateAdjudication(
        principal_id="gm",
        policy_id="current-state-policy",
        reason="Resolve the committed natural critical after its compulsory damage deadline",
        effect=effect,
        duration_seconds=30 if effect == "disable-source" else None,
    )
    continued, resumed, result = continue_innate_miss(
        state,
        encounter,
        critical_id="critical",
        command_id="resume-after-damage",
        context_digest=pending.context_digest,
        adjudication=policy,
        rng=RecordedDice([]),
        system=True,
    )
    # The sole resource mutation is a new consequence event. No wound, pool,
    # body status, object, due occurrence or receipt is replayed or restored.
    assert continued == state.model_copy(update={"events": continued.events})
    assert continued.events[:-1] == state.events and continued.events[-1].at == 10
    assert load_innate_critical(continued, "critical") == captured
    assert result.context_digest == captured.digest and result.basis == "gm-adjudication"
    assert resumed.pending_defense == encounter.pending_defense
    assert resumed.blocked_reason is None
    if effect == "disable-source":
        assert result.disabled_until == 40
        with pytest.raises(ValidationError, match="still disabled"):
            require_innate_action(continued, resumed, "a", "source:a")
        require_innate_action(
            continued.model_copy(update={"game_time": 40}), resumed, "a", "source:a"
        )
    else:
        assert resumed.participants[0].defense_penalty == -2
        with pytest.raises(ValidationError, match="even free"):
            require_innate_action(continued, resumed, "a")
        # If combat ends immediately, the new balance loss lasts its actual
        # current second, rather than expiring at the older attack's time.
        completed = resumed.model_copy(update={"pending_defense": None, "status": "completed"})
        with pytest.raises(ValidationError, match="even free"):
            require_innate_action(continued, completed, "a")
        require_innate_action(continued.model_copy(update={"game_time": 11}), completed, "a")
    later = continued.model_copy(update={"game_time": 11})
    assert continue_innate_miss(
        later,
        resumed,
        critical_id="critical",
        command_id="resume-after-damage",
        context_digest=pending.context_digest,
        adjudication=policy,
        rng=RecordedDice([]),
        system=True,
    ) == (later, resumed, result)


def test_b557_actual_emitter_arm_strains_without_hp_loss_or_compulsory_drop() -> None:
    source = context().source.model_copy(update={"emitter_arm": "right-arm"})
    state, encounter, result = resolve_innate_miss(
        initial(items=objects("a")),
        fight(held_by="a"),
        context(source=source, held=True),
        miss(),
        rng=RecordedDice(table(15)),
        system=True,
    )
    hp = next(p for p in state.pools if p.id == "hp:a")
    assert hp.current == 10 and hp.injury and hp.injury.lasting_injuries[0].recovery_at == 1800
    assert result.effect == "strain-arm" and result.location == "right-arm"
    assert state.items[0].ready and state.items[1].ready
    with pytest.raises(ValidationError, match="emitter arm is disabled"):
        require_innate_action(state, encounter, "a", "source:a")
    require_innate_action(state.model_copy(update={"game_time": 1800}), encounter, "a", "source:a")


@pytest.mark.parametrize(
    "row,effect",
    [
        (3, "break-item"),
        (8, "unready-item"),
        (12, "unready-item"),
        (9, "drop-item"),
        (14, "drop-item"),
    ],
)
def test_b556_real_source_objects_have_actual_break_ready_drop_consequences(
    row: int, effect: str
) -> None:
    source = context().source.model_copy(
        update={"source_kind": "held-item", "item_id": "weapon", "breakage": "ordinary"}
    )
    state, encounter, result = resolve_innate_miss(
        initial(items=objects("a")),
        fight(held_by="a"),
        context(source=source, held=True),
        miss(),
        rng=RecordedDice(table(row)),
        system=True,
    )
    weapon = state.items[1]
    assert result.effect == effect and not weapon.ready
    assert weapon.condition and weapon.condition.disabled is (row == 3)
    assert weapon.equipped is (row in (8, 12))
    assert (weapon.ground is not None) is (row not in (8, 12))
    assert state.items[0].ready and state.items[2].equipped
    assert encounter.participants[0].posture == "standing"


@pytest.mark.parametrize("confirmation,broken", [(9, False), (18, True)])
def test_b556_ranged_reroll_then_resistant_confirmation_preserves_three_rolls(
    confirmation: int, broken: bool
) -> None:
    source = context().source.model_copy(
        update={"source_kind": "held-item", "item_id": "weapon", "breakage": "resistant"}
    )
    state, _, result = resolve_innate_miss(
        initial(items=objects("a")),
        fight(held_by="a"),
        context(source=source, held=True),
        miss(),
        rng=RecordedDice([*table(5), *table(3), *table(confirmation)]),
        system=True,
    )
    saved = load_innate_critical(state, "critical")
    assert saved and saved.table_rolls == (table(5), table(3), table(confirmation))
    assert result.effect == ("break-item" if broken else "drop-item")
    assert state.items[1].condition and state.items[1].condition.disabled is broken


def test_b382_critical_dodge_falls_once_and_defense_by_ten_rule_is_retained() -> None:
    check = success_roll(PROFILE, 3, rng=RecordedDice([4, 4, 5]))
    assert check.outcome is Outcome.CRITICAL_FAILURE
    state, encounter = critical_dodge_failure(initial(), fight(), "b", check)
    assert encounter.participants[1].posture == "prone"
    hp = next(p for p in state.pools if p.id == "hp:b")
    assert hp.injury and hp.injury.prone
    assert critical_dodge_failure(state, encounter, "b", check) == (state, encounter)
    success = success_roll(PROFILE, 8, rng=RecordedDice([1, 1, 1]))
    assert critical_dodge_failure(initial(), fight(), "b", success) == (initial(), fight())


@pytest.mark.parametrize("machine,penetration", [(False, 2), (False, 0), (True, 2)])
def test_b556_fatigue_major_wound_literal_penetration_has_no_fabricated_hp(
    machine: bool, penetration: int
) -> None:
    state = initial(items=objects("b"))
    if machine:
        state = state.model_copy(
            update={
                "pools": tuple(
                    p.model_copy(update={"injury": p.injury.model_copy(update={"machine": True})})
                    if p.id == "hp:b" and p.injury
                    else p
                    for p in state.pools
                )
            }
        )
    state, encounter, result = apply_fatigue_major_wound(
        state,
        fight(held_by="b"),
        "b",
        "fatigue-major",
        penetration=penetration,
        ht=10,
        rng=RecordedDice([3, 4, 4] if penetration and not machine else []),
        location_id="room",
        system=True,
    )
    hp = next(p for p in state.pools if p.id == "hp:b")
    assert hp.current == 10 and hp.injury and hp.injury.shock == 0
    assert hp.injury.prone is (penetration > 0 and not machine)
    assert result.basis == "B556-literal-positive-penetration"
    assert (result.check is not None) is (penetration > 0 and not machine)
    assert result.dropped_item_ids == (("torch", "weapon") if penetration and not machine else ())
    assert apply_fatigue_major_wound(
        state,
        encounter,
        "b",
        "fatigue-major",
        penetration=penetration,
        ht=10,
        rng=RecordedDice([]),
        location_id="room",
        system=True,
    ) == (state, encounter, result)


@pytest.mark.parametrize("fp,expected_hp,shock", [(10, 10, 0), (1, 8, 4)])
def test_b556_fatigue_double_shock_only_acts_on_real_hp_overflow(
    fp: int, expected_hp: int, shock: int
) -> None:
    command = FatigueCost(
        id="critical-fatigue", actor_id="b", expected_revision=0, amount=3, attack_damage=True
    )
    state, result = apply_fatigue(
        initial(fp=fp), command, ht=10, rng=RecordedDice([]), double_shock=True, system=True
    )
    hp = next(p for p in state.pools if p.id == "hp:b")
    assert hp.current == expected_hp and hp.injury and hp.injury.shock == shock
    assert result.fp_lost == 3 and result.hp_lost == 10 - expected_hp
    assert apply_fatigue(
        state, command, ht=10, rng=RecordedDice([]), double_shock=True, system=True
    ) == (state, result)
    with pytest.raises(ConflictError, match="reused"):
        apply_fatigue(state, command, ht=10, rng=RecordedDice([]), system=True)


def test_guard_preserves_unrelated_legacy_profiles_without_hp_pools() -> None:
    require_innate_action(ResourceState(), fight(), "a")


def test_b382_failure_by_ten_cannot_enter_innate_miss_table() -> None:
    check = success_roll(PROFILE, 6, rng=RecordedDice(table(16)))
    with pytest.raises(ValidationError, match="ranged critical failure"):
        resolve_innate_miss(initial(), fight(), context(), check, rng=RecordedDice([]), system=True)


def test_b556_missing_original_anatomy_retains_both_self_hit_rolls_before_policy() -> None:
    state = initial()
    state = state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(
                    update={"injury": InjuryStatus(profile_id=PROFILE, anatomy="creature")}
                )
                if p.id == "hp:a"
                else p
                for p in state.pools
            )
        }
    )
    state, encounter, pending = resolve_innate_miss(
        state,
        fight(),
        context("gaze"),
        miss(),
        rng=RecordedDice([*table(5), *table(6)]),
        system=True,
    )
    assert pending.status == "awaiting-adjudication" and pending.damage_dice == ()
    captured = load_innate_critical(state, "critical")
    assert captured and captured.table_rolls == (table(5), table(6))
    assert encounter.blocked_reason == "innate-critical:critical"


@pytest.mark.parametrize("damage_type", ["fat", "tox"])
def test_b61_b556_toxic_and_fatigue_self_hit_use_the_applicable_pool(
    damage_type: Literal["fat", "tox"],
) -> None:
    source = context().source.model_copy(update={"damage_type": damage_type})
    state, _, result = resolve_innate_miss(
        initial(),
        fight(),
        context(source=source),
        miss(),
        rng=RecordedDice([*table(5), *table(6), 1, 1, 5]),
        system=True,
    )
    fatigue = damage_type == "fat"
    assert result.effect == "self-hit"
    assert result.fp_lost == (1 if fatigue else 0) and result.injury == (0 if fatigue else 1)
    assert next(p.current for p in state.pools if p.id == "fp:a") == (9 if fatigue else 10)
    hp = next(p for p in state.pools if p.id == "hp:a")
    assert hp.current == (10 if fatigue else 9)
    assert hp.injury and hp.injury.shock == (0 if fatigue else 1)


@pytest.mark.parametrize("damage_type", ["fat", "tox"])
@pytest.mark.parametrize("anatomy", ["human", "creature", "swarm"])
def test_b61_machine_self_hit_immunity_precedes_resistance_damage_and_modifier_effects(
    damage_type: Literal["fat", "tox"], anatomy: Literal["human", "creature", "swarm"]
) -> None:
    profile = AttackProfile(
        damage_kind="fatigue" if damage_type == "fat" else "toxic",
        resistance_modifier=0,
        cyclic_interval_seconds=10,
        cyclic_cycles=3,
        cyclic_stop_condition="wash",
        symptom_spec=SymptomSpec(
            kind="attribute-penalty", attribute="dx", level=2, numerator=1, denominator=3
        ),
    )
    source = context().source.model_copy(
        update={"damage_type": damage_type, "modifier_profile": profile}
    )
    original = initial(items=objects("a"))
    original = original.model_copy(
        update={
            "pools": tuple(
                p.model_copy(
                    update={
                        "injury": p.injury.model_copy(update={"machine": True, "anatomy": anatomy})
                    }
                )
                if p.id == "hp:a" and p.injury
                else p
                for p in original.pools
            )
        }
    )
    state, encounter, result = resolve_innate_miss(
        original,
        fight(held_by="a"),
        context(source=source, held=True),
        miss(),
        rng=RecordedDice([*table(5), *table(6)]),
        system=True,
    )
    assert result.status == "resolved" and result.effect == "self-hit"
    assert result.reason == "machine-immune-to-ordinary-damage-type"
    assert result.resistance_check is None and result.damage_dice == ()
    assert result.injury == result.fp_lost == 0 and result.cyclic_attack_id is None
    assert state == original.model_copy(update={"events": state.events})
    assert encounter == fight(held_by="a")
    capture = load_innate_critical(state, "critical")
    assert capture and capture.table_rolls == (table(5), table(6))


def test_b556_fatigue_major_reuses_overflow_check_and_drops_actual_objects() -> None:
    state, fatigue = apply_fatigue(
        initial(fp=0, items=objects("b")),
        FatigueCost(
            id="overflow",
            actor_id="b",
            expected_revision=0,
            amount=6,
            attack_damage=True,
        ),
        ht=10,
        rng=RecordedDice([3, 4, 4]),
        system=True,
    )
    assert fatigue.injury
    prior = next(c.check for c in fatigue.injury.checks if c.reason == "major-wound")
    state, encounter, effect = apply_fatigue_major_wound(
        state,
        fight(held_by="b"),
        "b",
        "critical-overflow",
        penetration=6,
        ht=10,
        rng=RecordedDice([]),
        location_id="room",
        already_checked=True,
        prior_check=prior,
        system=True,
    )
    assert effect.check == prior and effect.dropped_item_ids == ("torch", "weapon")
    assert encounter.participants[1].posture == "prone"
    assert next(p.current for p in state.pools if p.id == "hp:b") == 4
    assert next(p.current for p in state.pools if p.id == "fp:b") == -6
    with pytest.raises(ValidationError, match="actual check evidence"):
        apply_fatigue_major_wound(
            state,
            encounter,
            "b",
            "no-evidence",
            penetration=6,
            ht=10,
            rng=RecordedDice([]),
            location_id="room",
            already_checked=True,
            system=True,
        )


def test_major_wound_drops_an_actual_unready_hand_bound_object() -> None:
    state = initial(
        items=tuple(
            i.model_copy(update={"ready": False}) if i.id == "torch" else i for i in objects("b")
        )
    )
    state, result = apply_injury(
        state,
        Wound(
            id="drop-unready",
            actor_id="b",
            expected_revision=0,
            basic_damage=2,
            resistance=0,
            damage_type="burn",
        ),
        ht=10,
        rng=RecordedDice([3, 4, 4]),
        system=True,
        force_major_wound=True,
        held_item_ids=("torch", "weapon"),
        held_item_locations=(("torch", "left-hand"), ("weapon", "right-hand")),
    )
    assert result.dropped_ready_items == ("torch", "weapon")
    assert state.items[2].equipped


@pytest.mark.parametrize("fatigue", [False, True])
def test_self_hit_uses_captured_attacker_vulnerability_in_canonical_pool(fatigue: bool) -> None:
    source = context().source.model_copy(update={"damage_type": "fat" if fatigue else "burn"})
    traits = AttackDefenseTraits(
        entries=(
            PurchasedAttackDefenseTrait(
                definition_id="disadvantage:vulnerability",
                levels=1,
                parameters=(("source", "natural-attacks"), ("multiplier", 2)),
            ),
        )
    )
    saved_context = context(source=source).model_copy(update={"attacker_traits": traits})
    state, _, outcome = resolve_innate_miss(
        initial(),
        fight(),
        saved_context,
        miss(),
        rng=RecordedDice([*table(5), *table(6), 1, 1, 5]),
        system=True,
    )
    assert next(p.current for p in state.pools if p.id == ("fp:a" if fatigue else "hp:a")) == 8
    assert outcome.fp_lost == (2 if fatigue else 0)
    assert outcome.injury == (0 if fatigue else 2)


@pytest.mark.parametrize("damage,injury", [(1, 0), (5, 1)])
def test_b103_cyclic_self_hit_registers_real_self_occurrence_and_debt(
    damage: int, injury: int
) -> None:
    profile = AttackProfile(
        damage_kind="burning",
        cyclic_interval_seconds=10,
        cyclic_cycles=3,
        cyclic_stop_condition="approved cooling procedure",
    )
    source = context().source.model_copy(update={"modifier_profile": profile})
    state, encounter, result = resolve_innate_miss(
        initial(),
        fight(),
        context(source=source),
        miss(),
        rng=RecordedDice([*table(5), *table(6), 1, 1, damage]),
        system=True,
    )
    assert result.status == "resolved" and result.basis == "selected-printing"
    assert result.cyclic_attack_id and result.location == "right-arm"
    assert next(p.current for p in state.pools if p.id == "hp:a") == 10 - injury
    capture = load_innate_critical(state, "critical")
    assert capture and capture.context.source.modifier_profile == profile
    assert capture.table_rolls == (table(5), table(6))
    occurrence = state.cyclic_attacks[0]
    assert occurrence.id == result.cyclic_attack_id
    assert occurrence.actor_id == occurrence.attacker_id == "a"
    assert occurrence.basic_damage == damage // 2 and occurrence.damage_dice == 1
    assert occurrence.resistance == 1 and occurrence.hp_debt == injury
    assert occurrence.remaining == 2 and occurrence.due == 10
    assert state.illnesses[0].hp_debt == injury and state.illnesses[0].blocks_rest
    assert resolve_innate_miss(
        state, encounter, context(source=source), miss(), rng=RecordedDice([]), system=True
    ) == (state, encounter, result)
    assert encounter.blocked_reason is None


@pytest.mark.parametrize("resisted", [False, True])
def test_b104_initial_resistance_can_end_self_hit_without_damage_or_future_cycle(
    resisted: bool,
) -> None:
    profile = AttackProfile(
        damage_kind="burning",
        cyclic_interval_seconds=10,
        cyclic_cycles=3,
        cyclic_stop_condition="wash",
        resistance_modifier=0,
    )
    source = context().source.model_copy(update={"modifier_profile": profile})
    state, _, result = resolve_innate_miss(
        initial(),
        fight(),
        context(source=source),
        miss(),
        rng=RecordedDice(
            [
                *table(5),
                *table(6),
                1,
                1,
                *([3, 3, 3] if resisted else [3, 4, 4]),
                *([] if resisted else [5]),
            ]
        ),
        system=True,
    )
    assert result.resistance_check and result.resistance_check.outcome.succeeded is resisted
    assert result.damage_dice == (() if resisted else (5,))
    assert len(state.cyclic_attacks) == (0 if resisted else 1)
    assert next(p.current for p in state.pools if p.id == "hp:a") == (10 if resisted else 9)


def test_b109_self_hit_registers_actual_symptom_cause_and_initial_cyclic_recovery_debt() -> None:
    profile = AttackProfile(
        damage_kind="burning",
        cyclic_interval_seconds=10,
        cyclic_cycles=3,
        cyclic_stop_condition="wash",
        symptom_spec=SymptomSpec(
            kind="attribute-penalty", attribute="dx", level=2, numerator=1, denominator=3
        ),
    )
    source = context().source.model_copy(update={"modifier_profile": profile})
    saved_context = context(source=source).model_copy(
        update={
            "limb_dr": (("left-arm", 0), ("right-arm", 0), ("left-leg", 0), ("right-leg", 0)),
        }
    )
    state, _, result = resolve_innate_miss(
        initial(),
        fight(),
        saved_context,
        miss(),
        rng=RecordedDice([*table(5), *table(5), 1, 1, 4]),
        system=True,
    )
    assert result.injury == 4 and result.basic_damage == 4
    assert next(p.current for p in state.pools if p.id == "hp:a") == 6
    effect = state.symptom_effects[0]
    assert effect.active and effect.actor_id == "a" and effect.source_id == "a:source:a"
    debt = state.symptom_debts[0]
    assert debt.id == "innate-self-hit:critical" and debt.remaining == 4
    assert debt.source_id == effect.source_id and debt.restriction_id == result.cyclic_attack_id
    assert state.cyclic_attacks[0].symptom_spec == profile.symptom_spec


def test_b381_fatigue_self_hit_overflow_knockdown_drops_actual_objects() -> None:
    source = context().source.model_copy(update={"damage_type": "fat", "damage_dice": 2})
    saved_context = context(source=source, held=True).model_copy(
        update={
            "limb_dr": (("left-arm", 0), ("right-arm", 0), ("left-leg", 0), ("right-leg", 0)),
        }
    )
    state, encounter, outcome = resolve_innate_miss(
        initial(fp=0, items=objects("a")),
        fight(held_by="a"),
        saved_context,
        miss(),
        rng=RecordedDice([*table(5), *table(5), 1, 1, 3, 3, 3, 4, 4]),
        system=True,
    )
    assert outcome.fp_lost == outcome.injury == 6
    assert outcome.dropped_item_ids == ("torch", "weapon")
    assert state.items[0].ground and state.items[1].ground
    assert encounter.participants[0].posture == "prone"


def test_b55_self_resistance_uses_defensive_ht_and_fit_without_raising_injury_ht() -> None:
    profile = AttackProfile(damage_kind="burning", resistance_modifier=0)
    source = context().source.model_copy(update={"modifier_profile": profile})
    saved_context = context(source=source).model_copy(
        update={"attacker_ht": 8, "attacker_resistance_ht": 10}
    )
    state = initial()
    state = state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(
                    update={
                        "injury": p.injury.model_copy(
                            update={"physical_traits": PhysicalTraits(fitness=2)}
                        )
                    }
                )
                if p.id == "hp:a" and p.injury
                else p
                for p in state.pools
            )
        }
    )
    state, _, outcome = resolve_innate_miss(
        state,
        fight(),
        saved_context,
        miss(),
        rng=RecordedDice([*table(5), *table(6), 1, 1, 4, 4, 4]),
        system=True,
    )
    check = outcome.resistance_check
    assert check and check.base_target == 10 and check.effective_target == 12
    assert check.outcome.succeeded and check.modifiers[-1].source_id == "B55"
    assert outcome.damage_dice == () and outcome.injury == 0
    captured = load_innate_critical(state, "critical")
    assert captured and captured.context.attacker_ht == 8
