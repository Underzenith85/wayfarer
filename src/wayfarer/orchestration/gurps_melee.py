"""Profile-selected weapon melee within the existing encounter transaction.

Numeric baseline: Lite (August 2004), pp. 24-28; Basic Set B369-376,
B381-382 and B556. Complex Basic critical-miss consequences stop play with a
persisted table result rather than silently substituting ordinary damage.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import Outcome
from wayfarer.rules.effects import DerivedValue
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.location_types import HitLocation, HumanLocation
from wayfarer.rules.recovery_types import interrupt_tasks
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Combatant, Defense, Encounter, InjuryTrace
from wayfarer.simulation.fatigue import ContinueExertion, apply_fatigue, fatigue_value
from wayfarer.simulation.gurps_equipment import (
    EquipmentCatalog,
    MeleeMode,
    RangedMode,
    inventory_load,
    require_skill_procedure,
)
from wayfarer.simulation.hit_locations import (
    attack_penalty,
    missing_location,
    part,
    select_location,
)
from wayfarer.simulation.injury import InjuryTurn, Wound, apply_injury, impaired_movement
from wayfarer.simulation.maneuvers import ATTACK_MANEUVERS, attack_modifier


def fatigue_ready(state: PlayState, actor_id: str) -> bool:
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    if fp.fatigue is None:
        raise ValidationError("GURPS fatigue requires explicit migration")
    return not (
        fp.fatigue.collapsed
        or fp.fatigue.unconscious
        or fp.fatigue.heart_attack
        or fp.current <= -fp.maximum
    )


def exertion(
    play: PlayService, state: PlayState, actor_id: str, command_id: str
) -> tuple[PlayState, bool]:
    """Begin voluntary physical activity; persist a failed exertion result too."""
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    resources = state.resources.model_copy(
        update={
            "recovery_tasks": interrupt_tasks(
                state.resources.recovery_tasks, frozenset({actor_id}), state.resources.game_time
            )
        }
    )
    resources, result = apply_fatigue(
        resources,
        ContinueExertion(
            id="combat-exertion:" + hashlib.sha256(command_id.encode()).hexdigest(),
            actor_id=actor_id,
            expected_revision=resources.revision,
        ),
        ht=compiled.statistics.ht,
        will=compiled.statistics.will,
        rng=play.rng,
        system=True,
    )
    return state.model_copy(update={"resources": resources}), result.allowed


def movement(play: PlayService, state: PlayState, actor_id: str) -> int:
    from wayfarer.orchestration.location_combat import disabled

    if any(part(p) in ("leg", "foot") for p in disabled(state, actor_id)):
        # Supported combat movement is walking; crutches/crawling require an explicit mode.
        return 0
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    loaded = inventory_load(
        catalog(play), play.engine.resources, state.resources, actor_id, compiled.statistics
    )
    if loaded.move is None:
        return 0
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    return fatigue_value(fp, impaired_movement(hp, loaded.move))


def injury_turn(
    play: PlayService,
    state: PlayState,
    actor_id: str,
    command_id: str,
    *,
    start: bool,
    do_nothing: bool,
) -> PlayState:
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury is None or hp.injury.profile_id != compiled.statistics.profile_id:
        raise ValidationError("GURPS injury requires explicit migration")
    from wayfarer.simulation.spell_backfires import clear_stun, mental_stun, refund_due

    resources = state.resources
    if start:
        resources = refund_due(resources, actor_id, turn=hp.injury.turn + 1)
    was_mental = mental_stun(resources, actor_id)
    resources, _ = apply_injury(
        resources,
        InjuryTurn(
            id=f"injury-{'start' if start else 'end'}:"
            + hashlib.sha256(command_id.encode()).hexdigest(),
            actor_id=actor_id,
            expected_revision=state.resources.revision,
            turn=hp.injury.turn + int(start),
            phase="start" if start else "end",
            do_nothing=do_nothing,
        ),
        ht=compiled.statistics.ht,
        stun_iq=compiled.statistics.iq if was_mental else None,
        rng=play.rng,
        system=True,
    )
    after_hp = next(p for p in resources.pools if p.id == f"hp:{actor_id}")
    if was_mental and after_hp.injury and not after_hp.injury.stunned:
        resources = clear_stun(resources, actor_id, command_id)
    return state.model_copy(update={"resources": resources})


def catalog(play: PlayService) -> EquipmentCatalog:
    rules = play.engine.rules.combat
    if rules is None or rules.gurps_equipment is None:
        raise ValidationError("No GURPS equipment combat binding")
    return rules.gurps_equipment


def build(play: PlayService, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    compiled, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
    )
    if (
        compiled.statistics is None
        or compiled.statistics.profile_id != play.engine.reviewer.compiler.statistics_profile
    ):
        raise ValidationError("Melee requires the campaign's exact statistics profile")
    return compiled


def level(compiled: ValidatedBuild, target: str) -> DerivedValue:
    value = next((v for v in compiled.sheet.values if v.target == target), None)
    if value is None:
        raise ValidationError("Weapon skill has no trained or legal default level")
    return value


def mode(
    play: PlayService, state: PlayState, actor_id: str, item_id: str, mode_id: str | None
) -> MeleeMode | RangedMode:
    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or not item.equipped or not item.ready:
        raise ValidationError("Melee requires an owned, equipped, ready weapon")
    entry = next((e for e in catalog(play).entries if e.definition_id == item.definition_id), None)
    if entry is None:
        raise ValidationError("Weapon is not in the pinned combat catalog")
    from wayfarer.orchestration.object_combat import effective_entry

    entry = effective_entry(play, item)
    modes = tuple(m for m in entry.modes if (mode_id is None or m.id == mode_id))
    if len(modes) != 1:
        raise ValidationError("Select exactly one supported weapon mode")
    selected = modes[0]
    if selected.damage.damage_type == "fat" or (
        selected.damage.armor_divisor != 1 and catalog(play).profile_id != "gurps-basic-set-4e-2004"
    ):
        raise ValidationError("Weapon damage requires unsupported profile mechanics")
    from wayfarer.orchestration.location_combat import disabled, item_hands, unavailable_hand

    unavailable = disabled(state, actor_id) | frozenset(
        g.location
        for e in state.encounters
        for g in e.grips
        if g.target_id == actor_id and g.location in ("left-arm", "right-arm")
    )
    hands = item_hands(state, actor_id, item_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury and hp.injury.anatomy == "human" and len(hands) != selected.hands:
        raise ValidationError("Human weapon mode requires explicit matching hand bindings")
    if unavailable and (
        len(hands) != selected.hands or any(unavailable_hand(unavailable, h) for h in hands)
    ):
        raise ValidationError(
            "Selected grip uses a crippled hand or requires explicit hand bindings"
        )
    held_others = sum(
        1
        for other in state.resources.items
        if other.owner_id == actor_id
        and other.id != item_id
        and other.equipped
        and other.ready
        and any(
            e.definition_id == other.definition_id and (e.modes or e.shield)
            for e in catalog(play).entries
        )
    )
    if selected.hands + held_others > 2:
        raise ValidationError("Selected grip exceeds available hands")
    require_skill_procedure(catalog(play).profile_id, selected)
    level(build(play, state, actor_id), selected.skill_id)
    return selected


def heavy_parry_weight(
    play: PlayService,
    state: PlayState,
    participant: Combatant,
    incoming_item_id: str | None = None,
) -> int | None:
    """B376 incoming weight when the heavy-weapon parry limit applies, else None.

    Only the Basic profile carries the limit; Lite (pp. 24-28) states no weight
    rule. Callers inside the melee dispatch name the attacking item directly;
    otherwise the persisted pending attack supplies it, and a mode that is not
    melee, a spell attack or an unknown item carries no limit.
    """
    equipment = catalog(play)
    if equipment.profile_id != "gurps-basic-set-4e-2004":
        return None
    mode_id: str | None = None
    if incoming_item_id is None:
        encounter = next(
            (
                e
                for e in state.encounters
                if e.pending_defense and e.pending_defense.defender_id == participant.actor_id
            ),
            None,
        )
        if encounter is None:
            return None
        pending = encounter.pending_defense
        assert pending is not None
        if pending.spell_cast_id is not None:
            return None
        incoming_item_id, mode_id = pending.weapon_id, pending.mode_id
    item = next((i for i in state.resources.items if i.id == incoming_item_id), None)
    if item is None or not any(
        e.definition_id == item.definition_id and e.modes for e in equipment.entries
    ):
        return None
    from wayfarer.orchestration.object_combat import effective_entry

    entry = effective_entry(play, item)
    modes = tuple(m for m in entry.modes if mode_id is None or m.id == mode_id)
    if not modes or not all(isinstance(m, MeleeMode) for m in modes):
        return None
    return entry.weight_millipounds or None


def defense_value(
    play: PlayService,
    state: PlayState,
    participant: Combatant,
    selected: Defense,
    item_id: str | None = None,
    *,
    parry_mode_id: str | None = None,
    incoming_item_id: str | None = None,
) -> tuple[DerivedValue | None, str | None]:
    if selected == "none":
        return None, None
    from wayfarer.simulation.fright import can_defend

    if not can_defend(state.resources, participant.actor_id):
        raise ValidationError("Fright condition prevents active defense")
    from wayfarer.simulation.spell_effects import require_not_dazed

    require_not_dazed(state.resources, participant.actor_id)
    if participant.pinned:
        raise ValidationError("Pinned actors cannot defend")
    if participant.maneuver_state.defense_forbidden or (
        selected == "parry" and participant.maneuver_state.parry_forbidden
    ):
        raise ValidationError("The selected maneuver forbids this defense")
    compiled = build(play, state, participant.actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{participant.actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{participant.actor_id}")
    if not fatigue_ready(state, participant.actor_id):
        raise ValidationError("Exhausted actor cannot defend")
    if hp.injury is None or hp.injury.incapacitated:
        raise ValidationError("Incapacitated actor cannot defend")
    equipment = catalog(play)
    entries = {e.definition_id: e for e in equipment.entries}
    ready = [
        i
        for i in state.resources.items
        if i.id in participant.ready_item_ids
        and i.ready
        and i.equipped
        and (
            i.condition is None or not i.condition.disabled or i.condition.residual_roll is not None
        )
    ]
    shields = [
        (i, entries[i.definition_id].shield) for i in ready if entries[i.definition_id].shield
    ]
    from wayfarer.orchestration.location_combat import disabled, item_hands

    unavailable = disabled(state, participant.actor_id) | frozenset(
        g.location
        for e in state.encounters
        for g in e.grips
        if g.target_id == participant.actor_id and g.location in ("left-arm", "right-arm")
    )
    blind = "left-eye" in unavailable and "right-eye" in unavailable
    bonus = max(
        (
            max(
                0,
                s.defense_bonus
                - int(
                    any(
                        h.replace("hand", "arm") in unavailable
                        for h in item_hands(state, participant.actor_id, i.id)
                    )
                ),
            )
            for i, s in shields
            if s is not None
        ),
        default=0,
    )
    from wayfarer.simulation.fright import stunned as fright_stunned

    penalty = (
        participant.defense_penalty
        + participant.tactical_defense_bonus
        - 4 * int(participant.arm_locked)
        + (-1 if selected == "dodge" else -2) * int(participant.grappled)
        + (2 if participant.maneuver_state.enhanced_defense == selected else 0)
        + (-4 if hp.injury.stunned or fright_stunned(state.resources, participant.actor_id) else 0)
        + (-3 if participant.posture == "prone" else -2 if participant.posture == "kneeling" else 0)
        + (-4 if blind else 0)
    )

    def height_bonus(reach: int = 1) -> int:
        from wayfarer.simulation.combat_height import defense_height
        from wayfarer.simulation.tactical import pose

        encounter = next(
            (
                e
                for e in state.encounters
                if e.pending_defense and e.pending_defense.defender_id == participant.actor_id
            ),
            None,
        )
        if encounter is None or encounter.hex_battlefield is None:
            return 0
        pending = encounter.pending_defense
        assert pending is not None
        if pending.mode_id is None:
            return 0  # Preflight before the selected incoming mode is persisted.
        incoming = mode(play, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
        if not isinstance(incoming, MeleeMode):
            return 0
        attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        board = encounter.hex_battlefield
        return defense_height(
            board.cell(pose(participant).position).ground,
            board.cell(pose(attacker).position).ground,
            reach=reach,
        )

    if selected == "dodge":
        penalty += height_bonus()
        loaded = inventory_load(
            equipment,
            play.engine.resources,
            state.resources,
            participant.actor_id,
            compiled.statistics,
        )
        if loaded.dodge is None:
            raise ValidationError("Overloaded actor cannot dodge")
        return DerivedValue(
            "defense:dodge",
            Decimal(fatigue_value(fp, impaired_movement(hp, loaded.dodge)) + bonus + penalty),
            (),
        ), None
    candidates: list[tuple[int, str, str]] = []
    incoming_weight = (
        heavy_parry_weight(play, state, participant, incoming_item_id)
        if selected == "parry"
        else None
    )
    for item in ready:
        if item_id is not None and item.id != item_id:
            continue
        from wayfarer.orchestration.object_combat import effective_entry

        entry = effective_entry(play, item)
        if (
            selected == "block"
            and entry.shield
            and entry.shield.can_block
            and not participant.block_used
            and not any(
                h.replace("hand", "arm") in unavailable
                for h in item_hands(state, participant.actor_id, item.id)
            )
        ):
            value = level(compiled, entry.shield.skill_id)
            candidates.append(
                (int(value.value) // 2 + 3 + height_bonus(), item.id, entry.shield.skill_id)
            )
        if selected == "parry":
            # B376: a weapon cannot parry one weighing three or more times as
            # much; an item with no recorded weight states no ratio.
            if incoming_weight is not None and 0 < 3 * entry.weight_millipounds <= incoming_weight:
                continue
            for weapon_mode in entry.modes:
                if parry_mode_id is not None and weapon_mode.id != parry_mode_id:
                    continue
                if not isinstance(weapon_mode, MeleeMode) or weapon_mode.parry is None:
                    continue
                try:
                    mode(play, state, participant.actor_id, item.id, weapon_mode.id)
                except ValidationError:
                    continue
                parry = weapon_mode.parry
                if (
                    parry.unbalanced
                    and participant.last_maneuver in ATTACK_MANEUVERS
                    and participant.last_attack_item_id == item.id
                ):
                    continue
                value = level(compiled, weapon_mode.skill_id)
                repeats = participant.parries.count(item.id)
                # Lite permits only one parry with each weapon per turn.
                if repeats and equipment.profile_id == "gurps-lite-4e-2004":
                    continue
                repeat_penalty = repeats * (2 if parry.fencing else 4)
                candidates.append(
                    (
                        (
                            int(value.value)
                            - max(
                                0,
                                weapon_mode.minimum_st - fatigue_value(fp, compiled.statistics.st),
                            )
                        )
                        // 2
                        + 3
                        + parry.modifier
                        - repeat_penalty
                        + height_bonus(max(weapon_mode.reach)),
                        item.id,
                        weapon_mode.skill_id,
                    )
                )
    if not candidates:
        raise ValidationError("No available skill/equipment for this active defense")
    score, selected_item, skill = max(candidates)
    return DerivedValue(
        f"defense:{selected}:{skill}", Decimal(score + bonus + penalty), ()
    ), selected_item


def prepare_attack(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    mode_id: str | None,
    *,
    hit_location: HitLocation | None = None,
    target_item_id: str | None = None,
    shots: int = 1,
) -> Encounter:
    pending = encounter.pending_defense
    assert pending is not None
    selected = mode(play, state, pending.attacker_id, pending.weapon_id, mode_id)
    if target_item_id:
        from wayfarer.orchestration.object_combat import target_modifier

        if not isinstance(selected, MeleeMode) or selected.damage.damage_type not in (
            "cr",
            "cut",
            "imp",
            "pi-",
            "pi",
            "pi+",
            "pi++",
            "burn",
        ):
            raise ValidationError("Object target requires a supported melee damage mode")
        target_modifier(play, state, pending.defender_id, target_item_id)
    if isinstance(selected, RangedMode):
        from wayfarer.orchestration.gurps_ranged import prepare

        return prepare(play, state, encounter, selected, shots=shots, hit_location=hit_location)
    if shots != 1:
        raise ValidationError("Shot count requires a ranged mode")
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    from wayfarer.orchestration.location_combat import validate_target

    validate_target(
        play, state, encounter, pending.attacker_id, pending.defender_id, selected, hit_location
    )
    if attacker.maneuver_state.strong and selected.damage.basis == "fixed":
        raise ValidationError("Strong requires ST-based melee damage")
    if (
        attacker.maneuver_state.attacks_remaining
        and selected.ready_after_attack
        and attacker.maneuver_state.second_attack_item_id is None
    ):
        raise ValidationError("Double attack requires a weapon usable twice without readying")
    from wayfarer.simulation.combat import CombatEngine
    from wayfarer.simulation.tactical import attack_geometry, defense_adjustment

    attack_geometry(encounter, attacker, defender, frozenset(selected.reach), location=hit_location)
    if CombatEngine.distance(attacker.position, defender.position) not in selected.reach:
        raise ValidationError("Target is outside selected weapon reach")
    allowed: list[Defense] = ["none"]
    for candidate in ("dodge", "parry", "block"):
        try:
            defense_adjustment(encounter, attacker, defender)
            defense_value(play, state, defender, candidate, incoming_item_id=pending.weapon_id)
        except ValidationError:
            continue
        allowed.append(candidate)
    return encounter.model_copy(
        update={
            "pending_defense": pending.model_copy(
                update={
                    "mode_id": selected.id,
                    "allowed": tuple(allowed),
                    "hit_location": hit_location,
                    "target_item_id": target_item_id,
                }
            )
        }
    )


def validate_defense_choices(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
) -> None:
    pending = encounter.pending_defense
    if pending is None:
        raise ValidationError("No attack awaits defense")
    if selected not in pending.allowed or (
        second_defense is not None and second_defense not in pending.allowed
    ):
        raise ValidationError("Defense is not available against this attack")
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    _, first_item = defense_value(play, state, defender, selected, item_id)
    if second_defense is None:
        if second_item_id is not None:
            raise ValidationError("Second defense equipment requires a second defense")
        return
    if (
        selected == "none"
        or second_defense == "none"
        or defender.maneuver_state.enhanced_defense != "double"
    ):
        raise ValidationError("Second defense requires All-Out Defense (Double)")
    _, second_item = defense_value(play, state, defender, second_defense, second_item_id)
    if selected == second_defense and not (selected == "parry" and first_item != second_item):
        raise ValidationError(
            "Double defense requires different defenses or different parrying hands"
        )


def resolve_melee(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    *,
    second_defense: Defense | None = None,
    second_item_id: str | None = None,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    pending = encounter.pending_defense
    assert pending is not None
    if pending.spell_cast_id is not None:
        from wayfarer.orchestration.spell_missiles import resolve as resolve_spell

        return resolve_spell(
            play, state, encounter, selected, item_id, second_defense, second_item_id
        )
    equipment = catalog(play)
    weapon = mode(play, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if isinstance(weapon, RangedMode):
        from wayfarer.orchestration.gurps_ranged import resolve

        return resolve(
            play,
            state,
            encounter,
            weapon,
            selected,
            item_id,
            second_defense=second_defense,
            second_item_id=second_item_id,
        )
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    attack_build = build(play, state, pending.attacker_id)
    defend_build = build(play, state, pending.defender_id)
    assert attack_build.statistics is not None and defend_build.statistics is not None
    attack_value = level(attack_build, weapon.skill_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{pending.defender_id}")
    attacker_hp = next(p for p in state.resources.pools if p.id == f"hp:{pending.attacker_id}")
    attacker_fp = next(p for p in state.resources.pools if p.id == f"fp:{pending.attacker_id}")
    if hp.injury is None or attacker_hp.injury is None:
        raise ValidationError("GURPS injury pool requires explicit migration")
    defense_derived, defense_item = defense_value(
        play, state, defender, selected, item_id, incoming_item_id=pending.weapon_id
    )
    second_derived = None
    second_item = None
    if second_defense is not None:
        if (
            selected == "none"
            or second_defense == "none"
            or defender.maneuver_state.enhanced_defense != "double"
        ):
            raise ValidationError("Second defense requires All-Out Defense (Double)")
        second_derived, second_item = defense_value(
            play,
            state,
            defender,
            second_defense,
            second_item_id,
            incoming_item_id=pending.weapon_id,
        )
        if second_defense == selected and not (selected == "parry" and second_item != defense_item):
            raise ValidationError(
                "Double defense requires different defenses or different parrying hands"
            )
    elif second_item_id is not None:
        raise ValidationError("Second defense equipment requires a second defense")
    attack_target = (
        int(attack_value.value)
        - attacker_hp.injury.shock
        - max(0, weapon.minimum_st - fatigue_value(attacker_fp, attack_build.statistics.st))
    )
    from wayfarer.orchestration.object_combat import shock

    attack_target -= shock(state, pending.weapon_id)
    if pending.target_item_id:
        from wayfarer.orchestration.object_combat import target_modifier

        attack_target += target_modifier(play, state, pending.defender_id, pending.target_item_id)
    attack_target -= 4 if attacker.grappled else 0
    attack_target -= (
        4 if attacker.posture == "prone" else 2 if attacker.posture == "kneeling" else 0
    )
    from wayfarer.orchestration.location_combat import disabled

    eyes = disabled(state, pending.attacker_id) & {"left-eye", "right-eye"}
    attack_target -= 6 if len(eyes) == 2 else 1 if eyes else 0
    from wayfarer.simulation.tactical import height_effect

    height = height_effect(
        encounter, attacker, defender, reach=max(weapon.reach), location=pending.hit_location
    )
    attack_target += height.attack_modifier
    if pending.hit_location:
        entries = {e.definition_id: e for e in equipment.entries}
        shield_side = next(
            (
                hand.split("-")[0]
                for item, hand in defender.hand_bindings
                if any(
                    i.id == item and entries[i.definition_id].shield for i in state.resources.items
                )
            ),
            None,
        )
        attack_target += attack_penalty(pending.hit_location, shield_side=shield_side)
    attack_target = attack_modifier(attacker.maneuver_state, defender.actor_id, attack_target)
    if defense_derived is not None and attacker.maneuver_state.feint_target_id == defender.actor_id:
        defense_derived = DerivedValue(
            defense_derived.target,
            defense_derived.value - attacker.maneuver_state.feint_penalty,
            defense_derived.explanations,
        )
    attack = success_roll(equipment.profile_id, attack_target, rng=play.rng)
    defense = None
    second_trace = None
    from wayfarer.simulation.hit_locations import location_special_effects, torso_near_miss

    near_miss = torso_near_miss(pending.hit_location, attack)
    if near_miss:
        try:
            height_effect(encounter, attacker, defender, reach=max(weapon.reach), location="torso")
        except ValidationError:
            near_miss = False
    hit = attack.outcome.succeeded or near_miss
    critical_dice: tuple[int, ...] = ()
    critical_tables: tuple[tuple[int, ...], ...] = ()
    critical = 0
    blocked = None
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    effect_dice: tuple[int, ...] = ()
    lasting_ids: tuple[str, ...] = ()
    if (
        attack.outcome is Outcome.CRITICAL_FAILURE
        and equipment.profile_id == "gurps-basic-set-4e-2004"
    ):
        critical_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        blocked = f"basic-critical-miss:{sum(critical_dice)}"
    if hit and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense_derived is not None:
        defense = success_roll(equipment.profile_id, int(defense_derived.value), rng=play.rng)
        hit = not defense.outcome.succeeded
        if selected == "parry" and defense_item:
            defender = defender.model_copy(update={"parries": defender.parries + (defense_item,)})
        if selected == "block":
            defender = defender.model_copy(update={"block_used": True})
        if equipment.profile_id == "gurps-basic-set-4e-2004" and (
            defense.outcome is Outcome.CRITICAL_SUCCESS
            or (selected == "parry" and defense.outcome is Outcome.CRITICAL_FAILURE)
        ):
            critical_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
            blocked = f"basic-critical-miss:{sum(critical_dice)}:{'attacker' if defense.outcome.succeeded else 'defender'}"
            hit = defense.outcome is Outcome.CRITICAL_FAILURE and sum(critical_dice) in (
                7,
                8,
                9,
                10,
                11,
                12,
                13,
                14,
                16,
            )
        elif defense.outcome is Outcome.CRITICAL_FAILURE:
            if selected == "dodge":
                defender = defender.model_copy(update={"posture": "prone"})
            elif selected == "block" and defense_item:
                state = state.model_copy(
                    update={
                        "resources": state.resources.model_copy(
                            update={
                                "items": tuple(
                                    i.model_copy(update={"ready": False})
                                    if i.id == defense_item
                                    else i
                                    for i in state.resources.items
                                )
                            }
                        )
                    }
                )
                defender = defender.model_copy(
                    update={
                        "ready_item_ids": tuple(
                            i for i in defender.ready_item_ids if i != defense_item
                        )
                    }
                )
    if (
        attack.outcome is Outcome.CRITICAL_SUCCESS
        and equipment.profile_id == "gurps-basic-set-4e-2004"
    ):
        critical_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        critical = sum(critical_dice)
    if (
        hit
        and defense is not None
        and not defense.outcome.succeeded
        and second_derived is not None
        and blocked is None
    ):
        second_target = int(second_derived.value) - (
            attacker.maneuver_state.feint_penalty
            if attacker.maneuver_state.feint_target_id == defender.actor_id
            else 0
        )
        second_trace = success_roll(equipment.profile_id, second_target, rng=play.rng)
        hit = not second_trace.outcome.succeeded
        if second_defense == "parry" and second_item:
            defender = defender.model_copy(update={"parries": defender.parries + (second_item,)})
        if second_defense == "block":
            defender = defender.model_copy(update={"block_used": True})
        if second_trace.outcome is Outcome.CRITICAL_FAILURE:
            if second_defense == "dodge":
                defender = defender.model_copy(update={"posture": "prone"})
            elif second_defense == "parry":
                critical_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
                blocked = f"basic-critical-miss:{sum(critical_dice)}:defender"
                defense_item = second_item
                hit = sum(critical_dice) in (7, 8, 9, 10, 11, 12, 13, 14, 16)
            elif second_item:
                state = state.model_copy(
                    update={
                        "resources": state.resources.model_copy(
                            update={
                                "items": tuple(
                                    i.model_copy(update={"ready": False})
                                    if i.id == second_item
                                    else i
                                    for i in state.resources.items
                                )
                            }
                        )
                    }
                )
                defender = defender.model_copy(
                    update={
                        "ready_item_ids": tuple(
                            i for i in defender.ready_item_ids if i != second_item
                        )
                    }
                )
        elif (
            second_trace.outcome is Outcome.CRITICAL_SUCCESS
            and equipment.profile_id == "gurps-basic-set-4e-2004"
        ):
            critical_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
            blocked = f"basic-critical-miss:{sum(critical_dice)}:attacker"
    if blocked and blocked.startswith("basic-critical-miss:"):
        from wayfarer.orchestration.critical_limbs import resolve_limb

        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    defender if p.actor_id == defender.actor_id else p
                    for p in encounter.participants
                )
            }
        )
        parry_miss = blocked.endswith(":defender")
        state, encounter, limb = resolve_limb(
            play,
            state,
            encounter,
            table=critical_dice,
            defender_item=defense_item,
            blocker=blocked,
        )
        critical_tables = limb.table_rolls
        critical_dice = critical_tables[-1]
        effect_dice += tuple(d for roll in critical_tables[1:] for d in roll)
        effect_dice += limb.location_dice + limb.damage_dice
        lasting_ids += limb.lasting_injury_ids
        attacker = next(p for p in encounter.participants if p.actor_id == attacker.actor_id)
        defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
        if limb.resolved:
            blocked = None
            hit = parry_miss
        elif len(critical_tables) > 1:
            suffix = (
                ":defender" if parry_miss else ":attacker" if blocked.endswith(":attacker") else ""
            )
            blocked = f"basic-critical-miss:{sum(critical_dice)}{suffix}"
            hit = parry_miss and sum(critical_dice) in (7, 8, 9, 10, 11, 12, 13, 14, 16)
    if blocked and blocked.startswith("basic-critical-miss:"):
        from wayfarer.orchestration.object_combat import critical_breakage

        parrying = blocked.endswith(":defender")
        state, encounter, object_dice, resolved = critical_breakage(
            play,
            state,
            encounter,
            table=critical_dice,
            defender_item=defense_item,
            parrying=parrying,
        )
        effect_dice += object_dice
        if resolved:
            blocked = None
            hit = parrying
        attacker = next(p for p in encounter.participants if p.actor_id == attacker.actor_id)
        defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
    if hit and pending.hit_location:
        from wayfarer.orchestration.location_combat import from_behind

        location, location_dice = select_location(
            "torso" if near_miss else pending.hit_location,
            rng=play.rng,
            from_behind=from_behind(attacker, defender),
        )
        if pending.hit_location == "random" and missing_location(hp.injury, location):
            location = "torso"
    head = (
        location in ("face", "skull", "left-eye", "right-eye")
        and weapon.damage.damage_type != "tox"
        and location_special_effects(hp.injury, location)
    )
    critical_eye = False
    if head and critical in (6, 7) and location in ("face", "skull"):
        from wayfarer.orchestration.location_combat import from_behind

        if from_behind(attacker, defender) or (hp.injury.tolerance and hp.injury.tolerance.no_eyes):
            critical = 4
        else:
            eye_die = play.rng.randbelow(6) + 1
            effect_dice += (eye_die,)
            location = "right-eye" if eye_die <= 3 else "left-eye"
            critical_eye = True
    if head and critical == 8:
        defender = defender.model_copy(update={"forced_do_nothing": True})
    expression = (
        attack_build.statistics.swing
        if weapon.damage.basis == "swing"
        else attack_build.statistics.thrust
    )
    dice_count = weapon.damage.dice or expression.dice
    adds = weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    adds += attacker.maneuver_state.stop_thrust_damage_bonus
    if attacker.maneuver_state.strong:
        adds += max(2, dice_count)
    from wayfarer.orchestration.object_combat import intercepting_shield, shield_damage

    shield_hit = intercepting_shield(play, state, encounter, second_trace or defense)
    maximum = critical in ((3, 15) if head else (6, 15)) or (
        equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
    )
    dice = (
        tuple(play.rng.randbelow(6) + 1 for _ in range(dice_count))
        if (hit or shield_hit) and not maximum
        else ()
    )
    basic = (
        max(
            0 if weapon.damage.damage_type == "cr" else 1,
            (6 * dice_count if maximum else sum(dice)) + adds,
        )
        if hit or shield_hit
        else 0
    )
    basic *= (
        3
        if critical in ((18,) if head else (3, 18))
        else 2
        if critical in ((16,) if head else (5, 16))
        else 1
    )
    if shield_hit and not hit:
        state, encounter, basic = shield_damage(play, state, encounter, shield_hit, basic, weapon)
        defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
        hit = basic > 0
        if hit and pending.hit_location:
            side_die = play.rng.randbelow(6) + 1
            effect_dice += (side_die,)
            # Preserve the original grip even when this impact disables the shield.
            original = next(
                p
                for e in state.encounters
                if e.id == encounter.id
                for p in e.participants
                if p.actor_id == defender.actor_id
            )
            hand = next((h for i, h in original.hand_bindings if i == shield_hit), None)
            if side_die <= 2 and hand:
                location = "left-arm" if hand == "left-hand" else "right-arm"
            else:
                location, location_dice = select_location(pending.hit_location, rng=play.rng)
    entries = {e.definition_id: e for e in equipment.entries}
    resistance = max(
        (
            e.armor.dr
            for i in state.resources.items
            if i.owner_id == pending.defender_id
            and i.equipped
            and (i.condition is None or not i.condition.disabled)
            for e in (entries[i.definition_id],)
            if e.armor
            and (
                (location or "torso") in e.armor.locations
                or (part(location) + "s" if location else "torso") in e.armor.locations
            )
        ),
        default=0,
    )
    from wayfarer.simulation.abilities import damage_resistance

    if play.engine.rules.abilities is not None:
        resistance += damage_resistance(
            state.resources, pending.defender_id, build_revision=defend_build.revision
        )
    half = critical in ((4, 5, 17) if head else (4, 17))
    injury = 0
    held = tuple(
        i.id
        for i in state.resources.items
        if i.id in defender.ready_item_ids
        and (entries[i.definition_id].modes or entries[i.definition_id].shield)
    )
    if hit and pending.target_item_id:
        from wayfarer.orchestration.object_combat import synchronize
        from wayfarer.simulation.objects import DamageObject, apply_object

        resources, object_result = apply_object(
            play.engine.resources,
            state.resources,
            DamageObject.model_validate(
                {
                    "id": "target-object:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                    "actor_id": pending.attacker_id,
                    "expected_revision": state.resources.revision,
                    "item_id": pending.target_item_id,
                    "basic_damage": basic,
                    "damage_type": weapon.damage.damage_type,
                    "armor_divisor": weapon.damage.armor_divisor * (2 if half else 1),
                }
            ),
            system=True,
            rng=play.rng,
        )
        state = state.model_copy(update={"resources": resources})
        encounter = synchronize(state, encounter)
        defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
        resistance = object_result.effective_dr
        effect_dice += tuple(d for roll in object_result.checks for d in roll)
    elif hit:
        resources, result = apply_injury(
            state.resources,
            Wound(
                id=pending.id,
                actor_id=pending.defender_id,
                expected_revision=state.resources.revision,
                basic_damage=basic,
                resistance=resistance,
                damage_type=weapon.damage.damage_type,
                location=location,
                armor_divisor=weapon.damage.armor_divisor,
                tight_beam=weapon.damage.tight_beam,
                critical_eye=critical_eye,
            ),
            ht=defend_build.statistics.ht,
            rng=play.rng,
            system=True,
            held_item_ids=held,
            held_item_locations=tuple((i, h) for i, h in defender.hand_bindings if i in held),
            shield_item_ids=tuple(
                i
                for i in held
                if entries[
                    next(item.definition_id for item in state.resources.items if item.id == i)
                ].shield
            ),
            dx=defend_build.statistics.dx,
            force_major_wound=critical in ((4, 5) if head else (7, 13, 14)),
            double_shock=critical == 8 and not head,
            funny_bone=critical == 8 and not head,
            halve_dr=("up" if head else "down") if half else None,
            ignore_dr=head and critical == 3,
            head_trauma=(
                "deafened"
                if head and critical in (12, 13) and weapon.damage.damage_type == "cr"
                else "scarred"
                if head and critical in (12, 13)
                else None
            ),
            scar_levels=2 if weapon.damage.damage_type in ("burn", "cor") else 1,
        )
        injury = result.injury
        resistance = result.effective_resistance
        lasting_ids += result.lasting_injury_ids
        effect_dice += result.location_dice
        state = state.model_copy(update={"resources": resources})
    updated_hp = next(p for p in state.resources.pools if p.id == hp.id)
    status = updated_hp.injury
    assert status is not None
    drops = held if critical == 12 and not head and not pending.target_item_id else ()
    weapons = tuple(
        i
        for i in held
        if entries[next(item.definition_id for item in state.resources.items if item.id == i)].modes
    )
    if head and critical == 14 and weapons:
        if len(weapons) > 1:
            drop_die = play.rng.randbelow(6) + 1
            effect_dice += (drop_die,)
            drops = (weapons[0 if drop_die <= 3 else 1],)
        else:
            drops = weapons
    if drops:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False, "equipped": False})
                            if i.id in drops
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    defender = defender.model_copy(
        update={
            "posture": "prone" if status.prone else defender.posture,
            "ready_item_ids": tuple(
                sorted(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == defender.actor_id and i.ready and i.equipped
                )
            ),
        }
    )
    state = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(
                    update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                )
                if a.actor_id == defender.actor_id and status.incapacitated
                else a
                for a in state.actors
            )
        }
    )
    encounter = encounter.model_copy(
        update={
            "participants": tuple(
                defender if p.actor_id == defender.actor_id else p for p in encounter.participants
            ),
            "blocked_reason": blocked,
        }
    )
    if blocked and blocked.startswith("basic-critical-miss:"):
        number = sum(critical_dice)
        subject = defender if blocked.endswith(":defender") else attacker
        affected_item = defense_item if subject.actor_id == defender.actor_id else pending.weapon_id
        if number in (7, 13):
            subject = subject.model_copy(update={"defense_penalty": -2})
            blocked = None
        elif number == 16:
            subject = subject.model_copy(update={"posture": "prone"})
            blocked = None
        elif number in (8, 9, 10, 11, 12) or (
            number == 14
            and (weapon.damage.basis != "swing" or subject.actor_id == defender.actor_id)
        ):
            from wayfarer.orchestration.weapon_flight import position

            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                i.model_copy(
                                    update={
                                        "ready": False,
                                        "equipped": False
                                        if number in (9, 10, 11, 14)
                                        else i.equipped,
                                        "ground": position(encounter, subject)
                                        if number in (9, 10, 11, 14)
                                        else i.ground,
                                    }
                                )
                                if i.id == affected_item
                                else i
                                for i in state.resources.items
                            )
                        }
                    )
                }
            )
            subject = subject.model_copy(
                update={
                    "ready_item_ids": tuple(i for i in subject.ready_item_ids if i != affected_item)
                }
            )
            blocked = None
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    subject if p.actor_id == subject.actor_id else p for p in encounter.participants
                ),
                "blocked_reason": blocked,
            }
        )
    if blocked and sum(critical_dice) == 14 and not blocked.endswith(":defender"):
        from wayfarer.orchestration.weapon_flight import resolve_flight

        state, encounter, flight_dice = resolve_flight(play, state, encounter, critical_dice)
        effect_dice += flight_dice
        updated_hp = next(p for p in state.resources.pools if p.id == hp.id)
        status = updated_hp.injury
        assert status is not None
        injury = hp.current - updated_hp.current
        blocked = None
        encounter = encounter.model_copy(update={"blocked_reason": None})
    if blocked and blocked.startswith("basic-critical-miss:"):
        from wayfarer.orchestration.critical_context import capture_critical
        from wayfarer.orchestration.location_combat import from_behind
        from wayfarer.simulation.critical import IncomingWound

        incoming = (
            IncomingWound(
                actor_id=defender.actor_id,
                dice=dice_count,
                adds=adds,
                damage_type=weapon.damage.damage_type,
                resistance=resistance,
                ht=defend_build.statistics.ht,
                dx=defend_build.statistics.dx,
                hit_location=pending.hit_location,
                armor_divisor=weapon.damage.armor_divisor,
                tight_beam=weapon.damage.tight_beam,
                from_behind=from_behind(attacker, defender),
                held_item_ids=held,
                hand_bindings=defender.hand_bindings,
                shield_item_ids=tuple(
                    i.id
                    for i in state.resources.items
                    if i.id in held and entries[i.definition_id].shield
                ),
            )
            if blocked.endswith(":defender")
            else None
        )
        state = capture_critical(
            play,
            state,
            encounter,
            tables=critical_tables or (critical_dice,),
            defender_item=defense_item,
            incoming=incoming,
        )
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        second_defense=second_trace,
        attack_value=attack_value,
        defense_value=defense_derived,
        damage_dice=dice,
        basic_damage=basic,
        resistance=resistance,
        injury=injury,
        hp_before=hp.current,
        hp_after=updated_hp.current,
        incapacitated=status.incapacitated,
        profile_id=equipment.profile_id,
        rules_version="2004",
        critical_table=critical_dice,
        adjudication_required=blocked,
        location=location,
        location_dice=location_dice,
        effect_dice=effect_dice,
        lasting_injury_ids=lasting_ids,
    )
    from wayfarer.orchestration.gurps_maneuvers import distracted

    encounter = distracted(
        play, state, encounter, defender.actor_id, defended=defense is not None, injured=injury > 0
    )
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    if weapon.ready_after_attack:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False})
                            if i.id == pending.weapon_id
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
        actor = actor.model_copy(
            update={
                "ready_item_ids": tuple(i for i in actor.ready_item_ids if i != pending.weapon_id)
            }
        )
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    actor if p.actor_id == actor.actor_id else p for p in encounter.participants
                )
            }
        )
    from wayfarer.orchestration.location_combat import unavailable_hand

    attacker_status = next(
        p.injury for p in state.resources.pools if p.id == f"hp:{actor.actor_id}"
    )
    next_weapon = actor.maneuver_state.second_attack_item_id or pending.weapon_id
    attack_disabled = bool(
        attacker_status and (attacker_status.incapacitated or attacker_status.stunned)
    ) or any(
        unavailable_hand(disabled(state, actor.actor_id), hand)
        for item_id, hand in actor.hand_bindings
        if item_id == next_weapon
    )
    if actor.maneuver_state.attacks_remaining and (
        attack_disabled
        or not any(i.id == next_weapon and i.equipped and i.ready for i in state.resources.items)
    ):
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    p.model_copy(
                        update={
                            "maneuver_state": p.maneuver_state.model_copy(
                                update={"attacks_remaining": 0}
                            )
                        }
                    )
                    if p.actor_id == actor.actor_id
                    else p
                    for p in encounter.participants
                )
            }
        )
    return state, encounter, trace
