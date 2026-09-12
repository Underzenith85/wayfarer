"""Scoring an active defense and checking the choices offered."""

from __future__ import annotations

from decimal import Decimal

from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.tables.combat import minimum_strength_penalty
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, exertion, fatigue_ready, level
from wayfarer.engine.simulation.combat.combat_height import defense_height
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.entangle import defense_penalty as entangle_defense_penalty
from wayfarer.engine.simulation.combat.equipment_entry import effective_entry
from wayfarer.engine.simulation.combat.maneuvers import ATTACK_MANEUVERS
from wayfarer.engine.simulation.combat.melee.modes import heavy_parry_weight, mode
from wayfarer.engine.simulation.combat.objects.locations import item_hands
from wayfarer.engine.simulation.combat.tactical import pose
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode, inventory_load
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.fright import can_defend
from wayfarer.engine.simulation.health.fright import stunned as fright_stunned
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.health.injury import impaired_movement
from wayfarer.engine.simulation.magic.effects import require_not_dazed
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def defense_height_bonus(
    runtime: RulesContext, state: PlayState, participant: Combatant, reach: int = 1
) -> int:

    encounter = next(
        (
            e
            for e in state.encounters
            if e.pending_defense and e.pending_defense.defender_id == participant.actor_id
        ),
        None,
    )
    if encounter is None or encounter.spatial_kind != "hex":
        return 0
    pending = encounter.pending_defense
    assert pending is not None
    if pending.mode_id is None:
        return 0  # Preflight before the selected incoming mode is persisted.
    incoming = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if not isinstance(incoming, MeleeMode):
        return 0
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    board = runtime.require_hex(encounter)
    return defense_height(
        board.cell(pose(participant).position).ground,
        board.cell(pose(attacker).position).ground,
        reach=reach,
    )


def defense_value(
    runtime: RulesContext,
    state: PlayState,
    participant: Combatant,
    selected: Defense,
    item_id: str | None = None,
    *,
    parry_mode_id: str | None = None,
    incoming_item_id: str | None = None,
    incoming_mode_id: str | None = None,
) -> tuple[DerivedValue | None, str | None]:
    if selected == "none":
        return None, None
    object_target = next(
        (
            e.pending_defense.target_item_id
            for e in state.encounters
            if e.status == "active"
            and e.pending_defense
            and e.pending_defense.defender_id == participant.actor_id
        ),
        None,
    )
    # deferred: melee.defense -> objects.combat -> melee.defense.
    # Scoring a defense reads the item being defended with; damaging an item reads the
    # defense that failed to stop it.
    from wayfarer.engine.simulation.combat.objects.combat import weapon_target

    if object_target and next(i for i in state.resources.items if i.id == object_target).ground:
        raise ValidationError("Unheld objects have no active defense")
    targeted_weapon = weapon_target(runtime, state, object_target)
    if targeted_weapon:
        if selected == "block":
            raise ValidationError("A weapon target cannot be protected by Block")
        if selected == "parry":
            if item_id not in (None, object_target):
                raise ValidationError("Only the targeted weapon can parry this attack")
            item_id = object_target

    if not can_defend(state.resources, participant.actor_id):
        raise ValidationError("Fright condition prevents active defense")

    require_not_dazed(state.resources, participant.actor_id)
    if participant.pinned:
        raise ValidationError("Pinned actors cannot defend")
    if participant.maneuver_state.defense_forbidden or (
        selected == "parry" and participant.maneuver_state.parry_forbidden
    ):
        raise ValidationError("The selected maneuver forbids this defense")
    if selected == "parry" and item_id in ("left-hand", "right-hand"):
        # deferred: melee.defense -> unarmed.defense -> melee.defense.  Genuine mutual
        # recursion: a barehanded parry defers to the unarmed scorer, which asks back for
        # the dodge and weapon-parry values.
        from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense

        encounter = next(
            (
                e
                for e in state.encounters
                if e.pending_defense and e.pending_defense.defender_id == participant.actor_id
            ),
            None,
        )
        if encounter is None or encounter.pending_defense is None:
            raise ValidationError("Barehanded projectile parry requires a pending throw")
        pending = encounter.pending_defense
        incoming = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
        if not isinstance(incoming, RangedMode) or not incoming.catchable:
            raise ValidationError("Barehanded projectile parry requires an opted-in thrown mode")
        encounter = CombatEngine._replace(encounter, participant)
        bare_value, hand = unarmed_defense(
            runtime,
            state,
            encounter,
            participant.actor_id,
            selected,
            item_id,
            mode_id=parry_mode_id,
        )
        assert bare_value is not None
        return DerivedValue("defense:parry", Decimal(bare_value), ()), hand
    compiled = build(runtime, state, participant.actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{participant.actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{participant.actor_id}")
    if not fatigue_ready(state, participant.actor_id):
        raise ValidationError("Exhausted actor cannot defend")
    if hp.injury is None or hp.injury.incapacitated:
        raise ValidationError("Incapacitated actor cannot defend")
    equipment = catalog(runtime)
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

    unavailable = disabled(state.resources, participant.actor_id) | frozenset(
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
    if targeted_weapon:
        bonus = 0

    penalty = (
        entangle_defense_penalty(participant)
        + participant.defense_penalty
        + int(hp.injury.physical_traits.combat_reflexes)
        + participant.tactical_defense_bonus
        - 4 * int(participant.arm_locked)
        + (-1 if selected == "dodge" else -2) * int(participant.grappled)
        + (2 if participant.maneuver_state.enhanced_defense == selected else 0)
        + (-4 if hp.injury.stunned or fright_stunned(state.resources, participant.actor_id) else 0)
        + (-3 if participant.posture == "prone" else -2 if participant.posture == "kneeling" else 0)
        + (-4 if blind else 0)
    )

    if selected == "dodge":
        penalty += defense_height_bonus(runtime, state, participant)
        loaded = inventory_load(
            equipment,
            runtime.resources,
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
        heavy_parry_weight(runtime, state, participant, incoming_item_id, incoming_mode_id)
        if selected == "parry"
        else None
    )
    for item in ready:
        if item_id is not None and item.id != item_id:
            continue

        entry = effective_entry(runtime, item)
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
                (
                    int(value.value) // 2 + 3 + defense_height_bonus(runtime, state, participant),
                    item.id,
                    entry.shield.skill_id,
                )
            )
        if selected == "parry":
            for weapon_mode in entry.modes:
                if parry_mode_id is not None and weapon_mode.id != parry_mode_id:
                    continue
                if not isinstance(weapon_mode, MeleeMode) or weapon_mode.parry is None:
                    continue
                try:
                    mode(runtime, state, participant.actor_id, item.id, weapon_mode.id)
                except ValidationError:
                    continue
                if incoming_weight is not None:
                    # B376: the hard limit is BL (twice BL for a two-handed mode).
                    # Three times weapon weight introduces breakage, not a ban.
                    if incoming_weight > compiled.statistics.basic_lift * 1000 * weapon_mode.hands:
                        continue
                    if 0 < 3 * entry.weight_millipounds <= incoming_weight:
                        # deferred: melee.defense -> melee.heavy_parry -> objects.combat -> melee.defense.
                        # Heavy-parry breakage still synchronizes equipment through object combat.
                        from wayfarer.engine.simulation.combat.melee.heavy_parry import (
                            require_breakage,
                        )

                        try:
                            require_breakage(entry, item)
                        except ValidationError:
                            if item_id is not None:
                                raise
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
                            - minimum_strength_penalty(
                                weapon_mode.minimum_st, fatigue_value(fp, compiled.statistics.st)
                            )
                        )
                        // 2
                        + 3
                        + parry.modifier
                        - repeat_penalty
                        + defense_height_bonus(runtime, state, participant, max(weapon_mode.reach)),
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


def exert_defense(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    selected: Defense,
    item_id: str | None,
    *,
    parry_mode_id: str | None = None,
) -> tuple[PlayState, Encounter, Defense]:
    """Spend the defender's effort and equipment; either failing leaves no active defense.

    B426: a defender whose exertion fails is too spent to defend and takes the blow.
    Worn protection is stressed whether or not they defend. The implement chosen
    for a parry or block is stressed by that use, and one that breaks under it
    defends with nothing. A hand is not an implement and never breaks here.
    """
    # deferred: melee.defense -> objects.combat -> melee.defense, as above.
    from wayfarer.engine.simulation.combat.objects.combat import defense_stress, worn_stress

    if selected != "none":
        state, allowed = exertion(runtime, state, actor_id, command_id)
        if not allowed:
            selected = "none"
    state, encounter = worn_stress(runtime, state, encounter, actor_id, command_id)
    if selected == "none":
        return state, encounter, "none"
    participant = next(p for p in encounter.participants if p.actor_id == actor_id)
    _, used = defense_value(
        runtime, state, participant, selected, item_id, parry_mode_id=parry_mode_id
    )
    bare = used in ("left-hand", "right-hand")
    state, encounter = defense_stress(
        runtime, state, encounter, actor_id, command_id, None if bare else used
    )
    if used and not bare and not any(i.id == used and i.ready for i in state.resources.items):
        return state, encounter, "none"
    return state, encounter, selected


def validate_defense_choices(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
    *,
    parry_mode_id: str | None = None,
    second_parry_mode_id: str | None = None,
) -> None:
    pending = encounter.pending_defense
    if pending is None:
        raise ValidationError("No attack awaits defense")
    if selected not in pending.allowed or (
        second_defense is not None and second_defense not in pending.allowed
    ):
        raise ValidationError("Defense is not available against this attack")
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    if (parry_mode_id is not None and selected != "parry") or (
        second_parry_mode_id is not None and second_defense != "parry"
    ):
        raise ValidationError("Parry damage mode requires the corresponding Parry defense")
    _, first_item = defense_value(
        runtime, state, defender, selected, item_id, parry_mode_id=parry_mode_id
    )
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
    _, second_item = defense_value(
        runtime, state, defender, second_defense, second_item_id, parry_mode_id=second_parry_mode_id
    )
    if selected == second_defense and not (selected == "parry" and first_item != second_item):
        raise ValidationError(
            "Double defense requires different defenses or different parrying hands"
        )
