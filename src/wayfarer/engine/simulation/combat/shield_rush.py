"""B368/B371/B406 shield rushes through the authoritative Slam procedure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, Outcome, draw_dice
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.combat import strong_damage_bonus
from wayfarer.engine.simulation.abilities import damage_resistance
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
from wayfarer.engine.simulation.combat.critical import IncomingWound
from wayfarer.engine.simulation.combat.criticals.context import capture_critical
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.equipment_effects import synchronize
from wayfarer.engine.simulation.combat.maneuver_transitions import distracted
from wayfarer.engine.simulation.combat.maneuvers import attack_modifier
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.tactical import move_hex, pose
from wayfarer.engine.simulation.combat.visibility import combat_visibility
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile, Shield
from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.hex_geometry import Hex, arc
from wayfarer.engine.simulation.movement.vehicles.collisions import collision_dice, roll_damage
from wayfarer.engine.simulation.resources import Item
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def _shield(
    runtime: RulesContext, state: PlayState, actor_id: str, item_id: str
) -> tuple[Item, EquipmentProfile, Shield]:
    item = next((i for i in state.resources.items if i.id == item_id), None)
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    entry = entries.get(item.definition_id) if item is not None else None
    shield = entry.shield if entry is not None else None
    if (
        item is None
        or item.owner_id != actor_id
        or not item.ready
        or not item.equipped
        or item.condition is not None
        and item.condition.disabled
        or shield is None
        or not shield.can_rush
    ):
        raise ValidationError("Shield rush requires an owned, ready, equipped rushing shield")
    assert item is not None and entry is not None and shield is not None
    return item, entry, shield


def validate_declaration(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn,
) -> None:
    """Reject ineligible equipment and geometry before any turn entropy is consumed."""

    if not command.shield_rush:
        return
    if catalog(runtime).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Shield rush requires exact Basic Set dispatch")
    item, _, shield = _shield(runtime, state, command.actor_id, command.item_id or "")
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    target = next((p for p in encounter.participants if p.actor_id == command.target_id), None)
    hands = tuple(hand for item_id, hand in actor.hand_bindings if item_id == item.id)
    if len(hands) != 1 or shield.occupies_hand and hands[0] not in ("left-hand", "right-hand"):
        raise ValidationError("Shield rush requires the shield's authoritative hand binding")
    if (
        encounter.spatial_kind != "hex"
        or target is None
        or target.actor_id == actor.actor_id
        or not command.hex_path
        or not command.enter_close_combat
        or command.hex_path[-1] not in encounter.occupied_hexes(target.actor_id)
    ):
        raise ValidationError("Shield rush requires a mapped path into the target's hex")
    assert isinstance(actor.position, Hex)
    assert isinstance(target.position, Hex)
    before_impact = move_hex(
        encounter,
        actor,
        command.maneuver,
        command.hex_path[:-1],
        None,
        command.defense_option,
        board=runtime.require_hex(encounter),
    )
    side = "left" if hands[0] == "left-hand" else "right"
    if arc(pose(before_impact), target.position) not in ("front", side):
        raise ValidationError("Shield rush target must be in the front or shield-side hex")


def prepare(runtime: RulesContext, state: PlayState, encounter: Encounter) -> Encounter:
    """Bind Shield skill, legal defenses, and the persisted collision velocity."""

    pending = encounter.pending_defense
    assert pending is not None and pending.shield_rush
    _, entry, shield = _shield(runtime, state, pending.attacker_id, pending.weapon_id)
    if pending.collision_velocity < 1:
        raise ValidationError("Shield rush requires nonzero relative velocity")
    modes = tuple(mode for mode in entry.modes if getattr(mode, "shield_attack", False))
    if len(modes) != 1:
        raise ValidationError("Rushing shield requires one authoritative shield attack mode")
    visibility = combat_visibility(encounter, pending.attacker_id, pending.defender_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    allowed: list[Defense] = ["none"]
    for candidate in visibility.defenses:
        try:
            defense_value(
                runtime,
                state,
                defender,
                candidate,
                incoming_item_id=pending.weapon_id,
                incoming_mode_id=modes[0].id,
            )
        except ValidationError:
            continue
        allowed.append(candidate)
    return encounter.model_copy(
        update={
            "pending_defense": pending.model_copy(
                update={
                    "mode_id": modes[0].id,
                    "allowed": tuple(allowed),
                    "visibility_attack_penalty": visibility.attack_penalty,
                    "visibility_defense_penalty": visibility.defense_penalty,
                }
            )
        }
    )


def _torso_dr(runtime: RulesContext, state: PlayState, actor_id: str, revision: str) -> int:
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    worn = max(
        (
            entry.armor.dr
            for item in state.resources.items
            if item.owner_id == actor_id
            and item.equipped
            and (item.condition is None or not item.condition.disabled)
            for entry in (entries[item.definition_id],)
            if entry.armor is not None and "torso" in entry.armor.locations
        ),
        default=0,
    )
    return worn + (
        damage_resistance(state.resources, actor_id, build_revision=revision)
        if runtime.rules.abilities is not None
        else 0
    )


def _knockdown(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    dx: int,
) -> CheckTrace:
    return success_roll(
        catalog(runtime).profile_id,
        dx,
        check_modifiers(state.resources, actor_id, "dx"),
        rng=runtime.rng,
    )


def fall_resolution(
    attacker_damage: int, defender_damage: int
) -> Literal["attacker", "defender", "check-defender", "none"]:
    """B371's mutually exclusive fall comparison, exposed for property coverage."""

    if defender_damage >= 2 * attacker_damage and defender_damage > 0:
        return "attacker"
    if attacker_damage < defender_damage:
        return "none"
    if attacker_damage >= 2 * defender_damage and attacker_damage > 0:
        return "defender"
    return "check-defender"


@dataclass(frozen=True, slots=True)
class _AttackOutcome:
    state: PlayState
    defender: Combatant
    attack: CheckTrace
    defense_value: DerivedValue | None
    defense_item: str | None
    defense: CheckTrace | None
    hit: bool
    table: tuple[int, ...]
    blocked: str | None


def _attack_outcome(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    attacker: Combatant,
    defender: Combatant,
    attack_value: DerivedValue,
    shock: int,
    selected: Defense,
    item_id: str | None,
) -> _AttackOutcome:
    """Resolve the attack/active-defense table family before collision damage."""

    pending = encounter.pending_defense
    assert pending is not None and pending.mode_id is not None
    commitment = attacker.maneuver_state
    if attacker.last_maneuver == "move_and_attack":
        commitment = commitment.model_copy(update={"attack_bonus": 0, "attack_cap": None})
    target = attack_modifier(
        commitment,
        defender.actor_id,
        int(attack_value.value) + pending.visibility_attack_penalty - shock,
    )
    attack = success_roll(
        catalog(runtime).profile_id,
        target,
        check_modifiers(state.resources, attacker.actor_id, "dx"),
        rng=runtime.rng,
    )
    derived, defense_item = defense_value(
        runtime,
        state,
        defender,
        selected,
        item_id,
        incoming_item_id=pending.weapon_id,
        incoming_mode_id=pending.mode_id,
    )
    defense = None
    hit = attack.outcome.succeeded
    table: tuple[int, ...] = ()
    blocked = None
    if attack.outcome is Outcome.CRITICAL_FAILURE:
        table = draw_dice(runtime.rng, 3)
        blocked = f"basic-critical-miss:{sum(table)}"
        hit = False
    elif attack.outcome is Outcome.CRITICAL_SUCCESS:
        table = draw_dice(runtime.rng, 3)
    elif hit and derived is not None:
        defense = success_roll(
            catalog(runtime).profile_id,
            int(derived.value) + pending.visibility_defense_penalty,
            rng=runtime.rng,
        )
        hit = not defense.outcome.succeeded
        if selected == "parry" and defense_item:
            defender = defender.model_copy(update={"parries": defender.parries + (defense_item,)})
        if selected == "block":
            defender = defender.model_copy(update={"block_used": True})
        if defense.outcome is Outcome.CRITICAL_FAILURE and selected == "parry":
            table = draw_dice(runtime.rng, 3)
            blocked = f"basic-critical-miss:{sum(table)}:defender"
            hit = sum(table) in (7, 8, 9, 10, 11, 12, 13, 14, 16)
        elif defense.outcome is Outcome.CRITICAL_FAILURE:
            hit = True
            if selected == "dodge":
                defender = defender.model_copy(update={"posture": "prone"})
            elif selected == "block" and defense_item:
                state = state.model_copy(
                    update={
                        "resources": state.resources.model_copy(
                            update={
                                "items": tuple(
                                    value.model_copy(update={"ready": False})
                                    if value.id == defense_item
                                    else value
                                    for value in state.resources.items
                                )
                            }
                        )
                    }
                )
        elif defense.outcome is Outcome.CRITICAL_SUCCESS:
            table = draw_dice(runtime.rng, 3)
            blocked = f"basic-critical-miss:{sum(table)}:attacker"
            hit = False
    return _AttackOutcome(
        state, defender, attack, derived, defense_item, defense, hit, table, blocked
    )


def resolve(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    """Resolve one shield rush; each collision body is rolled and damaged once."""

    pending = encounter.pending_defense
    assert pending is not None and pending.shield_rush and pending.mode_id is not None
    shield_item, entry, shield = _shield(runtime, state, pending.attacker_id, pending.weapon_id)
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    attacking = build(runtime, state, attacker.actor_id)
    defending = build(runtime, state, defender.actor_id)
    assert attacking.statistics is not None and defending.statistics is not None
    attacker_hp = next(p for p in state.resources.pools if p.id == f"hp:{attacker.actor_id}")
    defender_hp = next(p for p in state.resources.pools if p.id == f"hp:{defender.actor_id}")
    if attacker_hp.injury is None or defender_hp.injury is None:
        raise ValidationError("Shield rush requires authoritative GURPS injury pools")

    attack_value = level(attacking, shield.skill_id)
    outcome = _attack_outcome(
        runtime,
        state,
        encounter,
        attacker,
        defender,
        attack_value,
        attacker_hp.injury.shock,
        selected,
        item_id,
    )
    state, defender = outcome.state, outcome.defender
    attack, defense = outcome.attack, outcome.defense
    defense_derived, defense_item = outcome.defense_value, outcome.defense_item
    hit, critical_table, blocked = outcome.hit, outcome.table, outcome.blocked

    attacking_rolls: tuple[int, ...] = ()
    defending_rolls: tuple[int, ...] = ()
    knockdown: CheckTrace | None = None
    raw = injury = resistance = 0
    before = defender_hp.current
    if hit:
        attacker_dice = collision_dice(attacker_hp.maximum, pending.collision_velocity)
        defender_dice = collision_dice(defender_hp.maximum, pending.collision_velocity)
        critical = sum(critical_table) if attack.outcome is Outcome.CRITICAL_SUCCESS else 0
        maximum = critical in (6, 15)
        if maximum:
            raw = 6 * attacker_dice[0] + attacker_dice[1]
        else:
            raw, attacking_rolls = roll_damage(attacker_dice, runtime.rng)
        reciprocal, defending_rolls = roll_damage(defender_dice, runtime.rng)
        raw += shield.defense_bonus
        if attacker.maneuver_state.strong:
            raw += strong_damage_bonus(attacker_dice[0])
        raw *= 3 if critical in (3, 18) else 2 if critical in (5, 16) else 1
        resistance = _torso_dr(runtime, state, defender.actor_id, defending.revision)
        held = tuple(
            item.id
            for item in state.resources.items
            if item.owner_id == defender.actor_id and item.ready and item.equipped
        )
        resources, wound = apply_injury(
            state.resources,
            Wound(
                id=pending.id + ":target",
                actor_id=defender.actor_id,
                expected_revision=state.resources.revision,
                basic_damage=raw,
                resistance=resistance,
                damage_type="cr",
                location="torso",
            ),
            ht=defending.statistics.ht,
            dx=defending.statistics.dx,
            rng=runtime.rng,
            system=True,
            held_item_ids=held,
            held_item_locations=tuple((i, h) for i, h in defender.hand_bindings if i in held),
            shield_item_ids=tuple(
                i
                for i in held
                if next(
                    e
                    for e in catalog(runtime).entries
                    if e.definition_id
                    == next(
                        held_item.definition_id
                        for held_item in state.resources.items
                        if held_item.id == i
                    )
                ).shield
            ),
            force_major_wound=critical in (7, 13, 14),
            double_shock=critical == 8,
            funny_bone=critical == 8,
            halve_dr="down" if critical in (4, 17) else None,
        )
        injury = wound.injury
        resistance = wound.effective_resistance
        state = state.model_copy(update={"resources": resources})
        if critical == 12:
            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                item.model_copy(update={"ready": False, "equipped": False})
                                if item.id in held
                                else item
                                for item in state.resources.items
                            )
                        }
                    )
                }
            )
        resources, _ = apply_object(
            runtime.resources,
            state.resources,
            DamageObject(
                id=pending.id + ":shield",
                actor_id=attacker.actor_id,
                expected_revision=state.resources.revision,
                item_id=shield_item.id,
                basic_damage=reciprocal,
                damage_type="cr",
            ),
            system=True,
            rng=runtime.rng,
        )
        state = state.model_copy(update={"resources": resources})

        fallen: set[str] = set()
        fall = fall_resolution(raw, reciprocal)
        if fall == "attacker":
            fallen.add(attacker.actor_id)
        if fall == "defender":
            fallen.add(defender.actor_id)
        if fall == "check-defender":
            knockdown = _knockdown(runtime, state, defender.actor_id, defending.statistics.dx)
            if not knockdown.outcome.succeeded:
                fallen.add(defender.actor_id)
        attacker = attacker.model_copy(
            update={"posture": "prone" if attacker.actor_id in fallen else attacker.posture}
        )
        updated_defender_hp = next(
            p for p in state.resources.pools if p.id == f"hp:{defender.actor_id}"
        )
        assert updated_defender_hp.injury is not None
        defender = defender.model_copy(
            update={
                "posture": "prone"
                if defender.actor_id in fallen or updated_defender_hp.injury.prone
                else defender.posture
            }
        )

    encounter = encounter.model_copy(
        update={
            "participants": tuple(
                attacker
                if p.actor_id == attacker.actor_id
                else defender
                if p.actor_id == defender.actor_id
                else p
                for p in encounter.participants
            ),
            "blocked_reason": blocked,
        }
    )
    if blocked:
        attacker_dice = collision_dice(attacker_hp.maximum, pending.collision_velocity)
        held = tuple(
            item.id
            for item in state.resources.items
            if item.owner_id == defender.actor_id and item.ready and item.equipped
        )
        incoming = (
            IncomingWound(
                actor_id=defender.actor_id,
                dice=attacker_dice[0],
                adds=attacker_dice[1] + shield.defense_bonus,
                damage_type="cr",
                resistance=_torso_dr(runtime, state, defender.actor_id, defending.revision),
                ht=defending.statistics.ht,
                dx=defending.statistics.dx,
                hit_location="torso",
                held_item_ids=held,
                hand_bindings=tuple((i, h) for i, h in defender.hand_bindings if i in held),
                shield_item_ids=tuple(
                    i
                    for i in held
                    if next(
                        e
                        for e in catalog(runtime).entries
                        if e.definition_id
                        == next(
                            held_item.definition_id
                            for held_item in state.resources.items
                            if held_item.id == i
                        )
                    ).shield
                ),
            )
            if blocked.endswith(":defender")
            else None
        )
        state = capture_critical(
            runtime,
            state,
            encounter,
            tables=(critical_table,),
            defender_item=defense_item,
            incoming=incoming,
            defender_mode_id=None,
        )
    encounter = synchronize(state, encounter)
    updated_hp = next(p for p in state.resources.pools if p.id == f"hp:{defender.actor_id}")
    assert updated_hp.injury is not None
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        attack_value=DerivedValue("skill:shield-rush", attack_value.value, ()),
        defense_value=defense_derived,
        damage_dice=attacking_rolls,
        basic_damage=raw,
        resistance=resistance,
        injury=injury,
        hp_before=before,
        hp_after=updated_hp.current,
        incapacitated=updated_hp.injury.incapacitated,
        profile_id=catalog(runtime).profile_id,
        rules_version="2004",
        critical_table=critical_table,
        adjudication_required=blocked,
        effect_dice=defending_rolls + (() if knockdown is None else knockdown.dice),
    )
    encounter = distracted(
        runtime,
        state,
        encounter,
        defender.actor_id,
        defended=defense is not None,
        injured=injury > 0,
    )
    return state, encounter, trace
