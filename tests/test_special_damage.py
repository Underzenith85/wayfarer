"""Independent B416-B417/B428 affliction, penetration, and cinematic fixtures."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from test_attack_defense_traits import (
    approved,
    channel,
    command,
    resources,
    tolerance_options,
    world,
)
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_resources import engine as resource_engine
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import (
    AttackProfile,
    ModifierApproval,
    ModifierSelection,
    apply_attack_modifiers,
)
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.special_damage import (
    PenetrationContext,
    active_afflictions,
    resolve_affliction_penetration,
)
from wayfarer.engine.simulation.combat.turn_commitment import prepare
from wayfarer.engine.simulation.resources import Advance, Item, ResourceState
from wayfarer.engine.simulation.traits.attack_defense import apply_trait_attack
from wayfarer.errors import ValidationError


@pytest.mark.parametrize(
    ("context", "dr", "applies", "bonus", "modifier"),
    (
        (PenetrationContext(), 6, True, 6, 0),
        (
            PenetrationContext(modifier="armor-divisor", armor_divisor=Decimal(2)),
            6,
            True,
            3,
            0,
        ),
        (
            PenetrationContext(modifier="armor-divisor", armor_divisor=Decimal("0.5")),
            0,
            True,
            1,
            0,
        ),
        (PenetrationContext(modifier="blood-agent"), 0, False, 0, 0),
        (
            PenetrationContext(modifier="blood-agent", open_wound_or_mucous_membrane=True),
            6,
            True,
            0,
            0,
        ),
        (
            PenetrationContext(modifier="contact-agent", bare_skin_or_porous_clothing=True),
            2,
            False,
            0,
            0,
        ),
        (
            PenetrationContext(
                modifier="contact-agent",
                bare_skin_or_porous_clothing=True,
                tough_skin=True,
            ),
            2,
            True,
            0,
            0,
        ),
        (
            PenetrationContext(modifier="respiratory-agent", holding_breath=True),
            10,
            False,
            0,
            0,
        ),
        (
            PenetrationContext(
                modifier="sense-based", targeted_sense="vision", protected_sense_bonus=3
            ),
            10,
            True,
            3,
            0,
        ),
        (
            PenetrationContext(modifier="follow-up", carrier_hit=True, carrier_penetration=1),
            10,
            True,
            0,
            0,
        ),
        (
            PenetrationContext(delivery="side-effect", side_effect_injury=5),
            10,
            True,
            0,
            -2,
        ),
    ),
)
def test_special_penetration_separates_delivery_dr_and_resistance(
    context: PenetrationContext,
    dr: int,
    applies: bool,
    bonus: int,
    modifier: int,
) -> None:
    result = resolve_affliction_penetration(dr, context)
    assert (result.applies, result.dr_bonus, result.resistance_modifier) == (
        applies,
        bonus,
        modifier,
    )


def test_affliction_persists_typed_condition_and_replays_exactly_once() -> None:
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:affliction",
            amount=2,
            trait=options(effect="incapacitation"),
        )
    )
    target, _ = approved()
    attack = channel(
        definition_id="advantage:affliction",
        kind="affliction",
        basic_damage=0,
        attack_score=14,
        attack_roll=10,
        resistance_score=11,
        resistance_roll=12,
        duration_seconds=60,
        condition="choking",
        penetration=PenetrationContext(
            modifier="contact-agent",
            bare_skin_or_porous_clothing=True,
            tough_skin=True,
        ),
    )
    state, outcome = apply_trait_attack(
        resources(),
        world(),
        command("advantage:affliction"),
        attacker,
        target,
        compiler.definitions,
        (attack,),
        target_ht=12,
        rng=SimpleNamespace(),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "applied" and outcome.resistance_target == 11
    assert [
        (effect.actor_id, effect.condition, effect.expires_at) for effect in state.afflictions
    ] == [("b", "choking", 60)]
    assert active_afflictions(state, "b") == state.afflictions
    assert apply_trait_attack(
        state,
        world(),
        command("advantage:affliction"),
        attacker,
        target,
        compiler.definitions,
        (attack,),
        target_ht=12,
        rng=SimpleNamespace(),
        authorized_actor_id="a",
        system=True,
    ) == (state, outcome)
    clock = Advance(id="affliction-expiry", actor_id="a", expected_revision=1, to=60)
    expired = resource_engine().apply(state, clock, system=True)
    assert active_afflictions(expired, "b") == ()
    assert expired.afflictions == state.afflictions
    assert expired.fired == (state.scheduled[0].id,)
    assert sum(event.id == f"schedule:{state.scheduled[0].id}" for event in expired.events) == 1
    assert resource_engine().apply(expired, clock, system=True) == expired


def test_affliction_dr_bonus_and_injury_tolerance_are_distinct_boundaries() -> None:
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:affliction",
            trait=options(effect="incapacitation"),
        )
    )
    target, _ = approved(
        Purchase(definition_id="advantage:damage-resistance", amount=4),
        Purchase(
            definition_id="advantage:injury-tolerance",
            trait=tolerance_options(no_neck=True),
        ),
    )
    attack = channel(
        definition_id="advantage:affliction",
        kind="affliction",
        basic_damage=0,
        resistance_score=16,
        resistance_roll=17,
        condition="choking",
    )
    state, outcome = apply_trait_attack(
        resources(),
        world(),
        command("advantage:affliction"),
        attacker,
        target,
        compiler.definitions,
        (attack,),
        target_ht=12,
        rng=SimpleNamespace(),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "unaffected"
    assert outcome.resistance_target == 16
    assert outcome.penetration_reason == "injury-tolerance-no-neck"
    assert not state.afflictions


def test_modifier_projection_names_special_penetration_instead_of_anonymous_tags() -> None:
    contact = apply_attack_modifiers(
        AttackProfile(),
        "affliction",
        (ModifierSelection(definition_id="modifier:limitation:contact-agent"),),
    )
    assert contact.modified.penetration_modifier == "contact-agent"
    sense = "modifier:enhancement:sense-based"
    sensed = apply_attack_modifiers(
        AttackProfile(),
        "affliction",
        (ModifierSelection(definition_id=sense, option="vision"),),
        (ModifierApproval(sense, "vision", 150, frozenset({"affliction"})),),
    )
    assert (sensed.modified.penetration_modifier, sensed.modified.penetration_sense) == (
        "sense-based",
        "vision",
    )
    side_effect = "modifier:enhancement:side-effect"
    projected = apply_attack_modifiers(
        AttackProfile(),
        "innate-attack",
        (ModifierSelection(definition_id=side_effect, option="stunning"),),
        (ModifierApproval(side_effect, "stunning", 50, frozenset({"innate-attack"})),),
    )
    assert projected.modified.affliction_delivery == "side-effect"
    with pytest.raises(ValidationError, match="Incompatible ability modifiers"):
        apply_attack_modifiers(
            AttackProfile(),
            "affliction",
            (
                ModifierSelection(definition_id="modifier:limitation:contact-agent"),
                ModifierSelection(definition_id=sense, option="vision"),
            ),
            (ModifierApproval(sense, "vision", 150, frozenset({"affliction"})),),
        )


def _combatant(actor_id: str, *, weapons: tuple[str, ...] = ()) -> Combatant:
    return Combatant(
        actor_id=actor_id,
        initiative=10,
        reach=1,
        movement_allowance=5,
        ready_item_ids=weapons,
        hand_bindings=tuple(
            (weapon, "right-hand" if index == 0 else "left-hand")
            for index, weapon in enumerate(weapons)
        ),
    )


def test_dual_weapon_attack_is_profile_gated_and_preserves_two_penalties() -> None:
    actor = _combatant("a", weapons=("right", "left"))
    encounter = Encounter(
        id="fight",
        spatial_context=BasicSpatialContext(),
        participants=(actor, _combatant("b")),
        turn_order=("a", "b"),
    )

    def prepare_dual(engine: CombatEngine) -> Combatant:
        return prepare(
            engine,
            encounter,
            actor,
            ResourceState(),
            actor_id="a",
            maneuver="attack",
            item_id="right",
            target_id="b",
            attack_option=None,
            defense_option=None,
            wait_trigger=None,
            second_item_id="left",
            second_target_id="b",
            second_mode_id="swing",
            basic=True,
        )

    with pytest.raises(ValidationError, match="enabled Dual-Weapon Attack"):
        prepare_dual(
            cast(CombatEngine, SimpleNamespace(rules=SimpleNamespace(optional_rules=()))),
        )
    prepared = prepare_dual(
        cast(
            CombatEngine,
            SimpleNamespace(
                rules=SimpleNamespace(optional_rules=("gurps.techniques.dual-weapon-attack",))
            ),
        ),
    )
    assert prepared.maneuver_state.dual_weapon_attack
    assert prepared.maneuver_state.attacks_remaining == 1
    assert (
        prepared.maneuver_state.attack_bonus,
        prepared.maneuver_state.second_attack_penalty,
    ) == (
        -4,
        -8,
    )


async def test_dual_weapon_attack_keeps_two_rolls_defenses_and_weapon_state(
    tmp_path: Path,
) -> None:
    left = Item(
        id="sword-a-left",
        definition_id="equipment:broadsword",
        owner_id="a",
        equipped=True,
        ready=True,
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        combat_optional_rules=("gurps.techniques.dual-weapon-attack",),
        extra_attacker_hands=((left.id, "left-hand"),),
        extra_items=(left,),
    )
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        second_item_id=left.id,
        second_target_id="b",
        second_mode_id="swing",
    )
    first = play._load(await play.store.read(cid)).encounters[0]
    assert first.pending_defense is not None
    assert first.pending_defense.weapon_id == "sword-a"
    assert first.pending_defense.attention_defense_penalty == -1
    assert first.participants[0].maneuver_state.attack_bonus == -4

    play.rng = RecordedDice((5,) * 12)
    await defend(cid, play, "b", "none")
    second = play._load(await play.store.read(cid)).encounters[0]
    assert second.pending_defense is not None
    assert second.pending_defense.weapon_id == left.id
    assert second.pending_defense.attention_defense_penalty == -1
    assert second.participants[0].maneuver_state.attacks_remaining == 0
    assert set(second.participants[0].ready_item_ids) >= {"sword-a", left.id}

    play.rng = RecordedDice((5,) * 12)
    await defend(cid, play, "b", "none")
    finished = play._load(await play.store.read(cid)).encounters[0]
    assert finished.pending_defense is None
    assert finished.participants[0].maneuver_state.attacks_remaining == 0
    assert set(finished.participants[0].ready_item_ids) >= {"sword-a", left.id}
