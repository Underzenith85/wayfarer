"""Unarmed actions inside CombatService's existing CAS/receipt transaction.

Internal commands only: frozen v1 and saved coordinate systems are unchanged.
Unsupported critical consequences persist their dice and block continuation.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.gurps_melee import (
    build,
    catalog,
    defense_value,
    exertion,
    fatigue_ready,
    injury_turn,
    movement,
)
from wayfarer.orchestration.location_combat import disabled, unavailable_hand
from wayfarer.rules.checks import CheckTrace, Outcome
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.location_types import Hand, HumanLocation
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Combatant, CombatEngine, CombatResult, Encounter
from wayfarer.simulation.fatigue import fatigue_value
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.maneuvers import ManeuverState
from wayfarer.simulation.unarmed import (
    BASIC,
    Grip,
    PendingUnarmed,
    UnarmedTrace,
    contest,
    require_basic,
    striking_bonus,
    wrestling_bonus,
)

if TYPE_CHECKING:
    from wayfarer.orchestration.combat import (
        ChooseDefense,
        ResolveChokeEffects,
        TakeUnarmedTurn,
        TypedCombatCommand,
    )
    from wayfarer.orchestration.play import PlayService


def fighter(encounter: Encounter, actor_id: str) -> Combatant:
    actor = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Unarmed target is not in this encounter")
    return actor


def free_hands(state: PlayState, encounter: Encounter, actor_id: str) -> tuple[Hand, ...]:
    occupied = {h for _, h in fighter(encounter, actor_id).hand_bindings}
    occupied.update(h for g in encounter.grips if g.holder_id == actor_id for h in g.hands)
    # An arm grapple prevents use of that arm (B370).
    occupied.update(
        "left-hand" if g.location == "left-arm" else "right-hand"
        for g in encounter.grips
        if g.target_id == actor_id and g.location in ("left-arm", "right-arm")
    )
    unavailable = disabled(state, actor_id)
    return tuple(
        h
        for h in ("left-hand", "right-hand")
        if h not in occupied and not unavailable_hand(unavailable, h)
    )


def settle_control(state: PlayState, encounter: Encounter) -> Encounter:
    def conscious(actor_id: str) -> bool:
        hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
        return (
            hp.injury is not None and not hp.injury.incapacitated and fatigue_ready(state, actor_id)
        )

    grips = tuple(
        g
        for g in encounter.grips
        if conscious(g.holder_id)
        and not any(unavailable_hand(disabled(state, g.holder_id), h) for h in g.hands)
    )
    participants = tuple(
        p.model_copy(
            update={
                "arm_locked": any(g.target_id == p.actor_id and g.arm_lock for g in grips),
                "grappled": any(g.target_id == p.actor_id and g.location == "torso" for g in grips),
                "pinned": any(g.target_id == p.actor_id and g.pinned for g in grips),
            }
        )
        for p in encounter.participants
    )
    pairs = tuple(
        pair
        for pair in encounter.close_pairs
        if fighter(encounter, pair[0]).position == fighter(encounter, pair[1]).position
    )
    return encounter.model_copy(
        update={"grips": grips, "participants": participants, "close_pairs": pairs}
    )


def guard_control(encounter: Encounter, command: TypedCombatCommand) -> None:
    from wayfarer.orchestration.combat import ChooseDefense, TakeCombatTurn, TakeUnarmedTurn

    if encounter.pending_unarmed is not None:
        if (
            not isinstance(command, ChooseDefense)
            or command.actor_id != encounter.pending_unarmed.target_id
        ):
            raise ConflictError("Only the target may resolve the pending unarmed defense")
        return
    if isinstance(command, TakeUnarmedTurn):
        if (
            encounter.status != "active"
            or encounter.pending_defense
            or encounter.blocked_reason
            or encounter.wait_interrupt
        ):
            raise ConflictError("Encounter cannot accept unarmed action now")
        if command.actor_id != encounter.current_actor_id:
            raise ConflictError("Unarmed action is out of turn")
    if isinstance(command, TakeCombatTurn):
        actor = fighter(encounter, command.actor_id)
        if actor.pinned and command.maneuver != "do_nothing":
            raise ValidationError("Pinned actor must attempt a legal escape")
        engaged = any(command.actor_id in (g.holder_id, g.target_id) for g in encounter.grips)
        if engaged and (
            command.destination is not None or command.maneuver in ("move", "change_posture")
        ):
            raise ValidationError("Release or escape the grapple before moving")
        if engaged and command.maneuver in ("ready", "wait", "feint", "aim", "concentrate"):
            raise ValidationError("This maneuver while grappling requires further integration")
        if actor.grappled and command.posture is not None:
            raise ValidationError("A grapple prevents a posture step")


def skill_value(play: PlayService, state: PlayState, actor_id: str, skill: str) -> int:
    compiled = build(play, state, actor_id)
    value = next((v for v in compiled.sheet.values if v.target == skill), None)
    if value is None:
        raise ValidationError("Selected unarmed skill has no compiled level")
    return int(value.value)


def strength(play: PlayService, state: PlayState, actor_id: str, *, trained: bool = True) -> int:
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    wrestling = next(
        (int(v.value) for v in compiled.sheet.values if v.target == "skill:wrestling"), None
    )
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    return fatigue_value(fp, compiled.statistics.st) + (
        wrestling_bonus(compiled.statistics.dx, wrestling) if trained else 0
    )


def validate_action(
    play: PlayService, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> None:
    require_basic(catalog(play).profile_id)
    actor, target = fighter(encounter, command.actor_id), fighter(encounter, command.target_id)
    if actor.actor_id == target.actor_id:
        raise ValidationError("Unarmed action requires another actor")
    for participant in (actor, target):
        hp = next(p for p in state.resources.pools if p.id == f"hp:{participant.actor_id}")
        if hp.injury is None or hp.injury.anatomy != "human":
            raise ValidationError("Unarmed combat requires explicit living-human anatomy")
    if command.action in ("grapple", "pin", "takedown", "break_free"):
        for participant in (actor, target):
            compiled = build(play, state, participant.actor_id)
            if any(v.target == "size-modifier" and v.value != 0 for v in compiled.sheet.values):
                raise ValidationError("Unequal-size grappling requires body-size integration")
    setup = next(a for a in state.actors if a.actor_id == actor.actor_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    assert hp.injury is not None
    if setup.conditions or hp.injury.incapacitated or not fatigue_ready(state, actor.actor_id):
        raise ValidationError("Incapacitated actor cannot act")
    if setup.available_at > state.resources.game_time:
        raise ValidationError("Actor is recovering from injury")
    if any(p.maneuver_state.wait for p in encounter.participants):
        raise ValidationError("Unarmed attacks during armed Wait require interrupt integration")
    if command.action == "lock_damage" and (hp.injury.stunned or actor.forced_do_nothing):
        raise ValidationError("Stunned actor cannot apply arm-lock damage")
    if actor.pinned and command.action != "break_free":
        raise ValidationError("Pinned actor may only attempt escape")
    if command.enter_close_combat:
        if (
            command.action not in ("punch", "grapple")
            or actor.grappled
            or any(g.holder_id == actor.actor_id for g in encounter.grips)
        ):
            raise ValidationError("Close-combat entry is unavailable")
        if (
            CombatEngine.distance(actor.position, target.position) != 1
            or movement(play, state, actor.actor_id) == 0
        ):
            raise ValidationError("Close-combat entry requires an adjacent target and movement")
        if actor.posture == "prone":
            raise ValidationError("Prone close-combat entry requires crawling integration")
    distance = (
        0 if command.enter_close_combat else CombatEngine.distance(actor.position, target.position)
    )
    from wayfarer.simulation.tactical import attack_geometry

    if command.action in ("punch", "kick", "grapple", "arm_lock"):
        attack_geometry(encounter, actor, target)
        if command.enter_close_combat and encounter.hex_battlefield is not None:
            from wayfarer.simulation.hex_geometry import movement as hex_movement
            from wayfarer.simulation.tactical import occupants, pose

            hex_movement(
                encounter.hex_battlefield,
                pose(actor),
                (pose(target).position,),
                move=movement(play, state, actor.actor_id),
                step=True,
                occupants=occupants(encounter),
                actor_id=actor.actor_id,
                enter_close_combat=True,
            )
    if command.action in ("punch", "kick", "grapple", "arm_lock"):
        if command.grip_id is not None and command.action != "arm_lock":
            raise ValidationError("Attack cannot name an existing grip")
        if distance not in ((0, 1) if command.action == "kick" else (0,)):
            raise ValidationError("Unarmed attack is out of reach")
        skills = (
            {"attribute:dx", "skill:brawling", "skill:boxing", "skill:karate"}
            if command.action == "punch"
            else {"attribute:dx", "skill:brawling", "skill:karate"}
            if command.action == "kick"
            else {"attribute:dx", "skill:judo", "skill:wrestling", "skill:sumo-wrestling"}
        )
        if command.action == "arm_lock":
            skills = {"skill:judo", "skill:wrestling"}
        if command.skill not in skills:
            raise ValidationError("Skill does not support this unarmed action")
        skill_value(play, state, actor.actor_id, command.skill)
        if (
            command.action == "grapple"
            and target.posture != "standing"
            and actor.posture == "standing"
        ):
            raise ValidationError("Kneel or lie down before grappling a lowered foe")
        if command.action == "kick":
            if (
                command.hands
                or actor.posture != "standing"
                or disabled(state, actor.actor_id)
                & {"left-leg", "right-leg", "left-foot", "right-foot"}
                or any(
                    g.target_id == actor.actor_id and g.location in ("left-leg", "right-leg")
                    for g in encounter.grips
                )
            ):
                raise ValidationError("Kick requires two usable legs and standing posture")
        elif command.action == "arm_lock":
            grip = next((g for g in encounter.grips if g.id == command.grip_id), None)
            if (
                grip is None
                or grip.holder_id != actor.actor_id
                or grip.target_id != target.actor_id
                or grip.skill not in {"skill:judo", "skill:wrestling"}
                or grip.acquired_round >= encounter.round
                or grip.arm_lock
                or grip.pinned
            ):
                raise ValidationError(
                    "Arm lock requires a surviving Judo/Wrestling grapple from an earlier turn"
                )
            if (
                command.hands != grip.hands
                or len(grip.hands) != 2
                or command.location not in ("left-arm", "right-arm")
            ):
                raise ValidationError("Arm lock requires both grappling hands and a selected arm")
        elif (
            not command.hands
            or len(command.hands) > (1 if command.action == "punch" else 2)
            or len(set(command.hands)) != len(command.hands)
            or not set(command.hands) <= set(free_hands(state, encounter, actor.actor_id))
        ):
            raise ValidationError("Attack requires explicit free, usable hands")
        if command.action not in ("grapple", "arm_lock") and command.location != "torso":
            raise ValidationError("Unarmed strikes currently support torso only")
    else:
        if (
            command.hands
            or command.enter_close_combat
            or command.skill != "attribute:dx"
            or command.location != "torso"
        ):
            raise ValidationError("Control action has unexpected attack parameters")
        grip = next((g for g in encounter.grips if g.id == command.grip_id), None)
        if grip is None:
            raise ValidationError("Control action requires an existing grip")
        expected = (actor.actor_id, target.actor_id)
        actual = (
            (grip.target_id, grip.holder_id)
            if command.action == "break_free"
            else (grip.holder_id, grip.target_id)
        )
        if actual != expected:
            raise ValidationError("Actor does not control this grip")
        if command.action == "break_free" and encounter.round < grip.escape_after_round:
            raise ValidationError("A pinned escape attempt requires ten seconds between attempts")
        if command.action == "pin" and (
            grip.location != "torso" or target.posture != "prone" or grip.pinned
        ):
            raise ValidationError("Pin requires two hands grappling a prone torso")
        if command.action == "takedown" and (target.posture != "standing" or grip.pinned):
            raise ValidationError("Takedown requires a standing unpinned foe")
        if command.action == "strangle" and grip.location != "neck":
            raise ValidationError("Strangulation requires a neck grapple")
        if command.action == "lock_damage" and (
            not grip.arm_lock
            or encounter.round <= grip.acquired_round
            or grip.last_damage_round == encounter.round
        ):
            raise ValidationError("Arm-lock damage is available once on each subsequent turn")
        if command.action == "lock_damage" and grip.location in disabled(state, target.actor_id):
            raise ValidationError("Crippled-arm lock pain requires additional injury integration")


def execute_unarmed(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    command: TakeUnarmedTurn | ChooseDefense,
) -> tuple[PlayState, Encounter, CombatResult]:
    from wayfarer.orchestration.combat import ChooseDefense

    require_basic(catalog(play).profile_id)
    if isinstance(command, ChooseDefense):
        state, encounter, trace = defend(play, state, encounter, command)
    else:
        validate_action(play, state, encounter, command)
        if command.action in ("release", "lock_damage"):
            state, encounter, trace = control(play, state, encounter, command)
            encounter = settle_control(state, encounter)
            encounter = encounter.model_copy(
                update={"unarmed_history": encounter.unarmed_history + (trace,)}
            )
            return (
                state,
                encounter,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.grip_released",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    unarmed=trace,
                ),
            )
        actor = fighter(encounter, command.actor_id)
        state = injury_turn(
            play, state, actor.actor_id, command.id, start=True, do_nothing=actor.forced_do_nothing
        )
        hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
        assert hp.injury is not None
        allowed = not (hp.injury.incapacitated or hp.injury.stunned or actor.forced_do_nothing)
        if allowed:
            state, allowed = exertion(play, state, actor.actor_id, command.id)
        if not allowed:
            state = injury_turn(
                play, state, actor.actor_id, command.id, start=False, do_nothing=True
            )
            encounter = CombatEngine._replace(
                encounter,
                actor.model_copy(
                    update={
                        "forced_do_nothing": False,
                        "last_maneuver": "do_nothing",
                        "maneuver_state": ManeuverState(),
                    }
                ),
            )
            encounter = CombatEngine._advance(encounter)
            return (
                state,
                encounter,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.unarmed_unavailable",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                ),
            )
        actor = actor.model_copy(
            update={"last_maneuver": "attack", "maneuver_state": ManeuverState()}
        )
        encounter = CombatEngine._replace(encounter, actor)
        if command.action in ("punch", "kick", "grapple", "arm_lock"):
            if command.enter_close_combat:
                target = fighter(encounter, command.target_id)
                actor = actor.model_copy(update={"position": target.position})
                pairs = set(encounter.close_pairs)
                pairs.update(
                    (min(actor.actor_id, p.actor_id), max(actor.actor_id, p.actor_id))
                    for p in encounter.participants
                    if p.actor_id != actor.actor_id and p.position == target.position
                )
                encounter = CombatEngine._replace(encounter, actor).model_copy(
                    update={"close_pairs": tuple(sorted(pairs))}
                )
            allowed_defenses: list[str] = ["none"]
            for choice in ("dodge", "parry"):
                try:
                    unarmed_defense(play, state, encounter, command.target_id, choice, None)
                except ValidationError:
                    continue
                allowed_defenses.insert(0, choice)
            pending = PendingUnarmed.model_validate(
                {
                    "id": "unarmed:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    "actor_id": actor.actor_id,
                    "target_id": command.target_id,
                    "action": command.action,
                    "grip_id": command.grip_id,
                    "skill": command.skill,
                    "foot": command.foot,
                    "hands": command.hands,
                    "location": command.location,
                    "allowed": tuple(allowed_defenses),
                }
            )
            encounter = encounter.model_copy(update={"pending_unarmed": pending})
            return (
                state,
                encounter,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.unarmed_defense_required",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    pending_defense_id=pending.id,
                    available=pending.allowed,
                ),
            )
        state, encounter, trace = control(play, state, encounter, command)
    state = injury_turn(play, state, trace.actor_id, command.id, start=False, do_nothing=False)
    encounter = settle_control(state, encounter)
    encounter = encounter.model_copy(
        update={
            "pending_unarmed": None,
            "unarmed_history": encounter.unarmed_history + (trace,),
            "blocked_reason": trace.blocked_reason,
        }
    )
    if not trace.blocked_reason:
        encounter = CombatEngine._advance(encounter)
    return (
        state,
        encounter,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.unarmed_resolved",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            unarmed=trace,
        ),
    )


def unarmed_defense(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    selected: str,
    item_id: str | None,
) -> tuple[int | None, str | None]:
    actor = fighter(encounter, actor_id)
    if selected == "none":
        if item_id is not None:
            raise ValidationError("No defense cannot select equipment")
        return None, None
    if actor.pinned or actor.maneuver_state.defense_forbidden:
        raise ValidationError("Actor cannot defend")
    if encounter.hex_battlefield is not None and encounter.pending_unarmed is not None:
        from wayfarer.simulation.tactical import defense_adjustment

        defense_adjustment(encounter, fighter(encounter, encounter.pending_unarmed.actor_id), actor)
    if selected == "dodge":
        if item_id is not None:
            raise ValidationError("Dodge cannot select equipment")
        value, _ = defense_value(play, state, actor, "dodge")
        assert value is not None
        return int(value.value), None
    if selected != "parry" or actor.maneuver_state.parry_forbidden:
        raise ValidationError("Only Dodge or an unarmed Parry is supported")
    # Weapon parry against an unarmed limb needs a separate damage stage; fail closed.
    hand = item_id or next(iter(free_hands(state, encounter, actor_id)), None)
    if hand not in free_hands(state, encounter, actor_id):
        raise ValidationError("Unarmed parry requires a free usable hand")
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury is None or hp.injury.incapacitated or not fatigue_ready(state, actor_id):
        raise ValidationError("Incapacitated actor cannot parry")
    targets = [compiled.statistics.dx]
    targets.extend(
        int(v.value)
        for v in compiled.sheet.values
        if v.target in {"skill:brawling", "skill:boxing", "skill:karate", "skill:judo"}
    )
    penalty = (
        (-4 if hp.injury.stunned else 0)
        + (-3 if actor.posture == "prone" else -2 if actor.posture == "kneeling" else 0)
        + (-2 if actor.grappled else 0)
    )
    penalty += (
        actor.defense_penalty
        + actor.tactical_defense_bonus
        - 4 * int(actor.arm_locked)
        + (2 if actor.maneuver_state.enhanced_defense == "parry" else 0)
    )
    return max(targets) // 2 + 3 + penalty - 4 * actor.parries.count(hand), hand


def defend(
    play: PlayService, state: PlayState, encounter: Encounter, command: ChooseDefense
) -> tuple[PlayState, Encounter, UnarmedTrace]:
    pending = encounter.pending_unarmed
    if (
        pending is None
        or command.actor_id != pending.target_id
        or command.defense not in pending.allowed
    ):
        raise ValidationError("Defense is not authorized for this unarmed attack")
    if command.second_defense is not None or command.second_item_id is not None:
        raise ValidationError("Unarmed double-defense integration is not supported")
    defense_target, hand = unarmed_defense(
        play, state, encounter, command.actor_id, command.defense, command.item_id
    )
    actor, target = fighter(encounter, pending.actor_id), fighter(encounter, pending.target_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    assert hp.injury is not None
    value = skill_value(play, state, actor.actor_id, pending.skill) - hp.injury.shock
    value -= 4 if actor.grappled else 0
    value -= 4 if actor.posture == "prone" else 2 if actor.posture == "kneeling" else 0
    value -= 2 if pending.action == "kick" else 0
    value -= (
        {"torso": 0, "neck": 2, "left-arm": 1, "right-arm": 1, "left-leg": 1, "right-leg": 1}[
            pending.location
        ]
        if pending.action == "grapple"
        else 0
    )
    attack = success_roll(BASIC, value, rng=play.rng)
    checks: tuple[CheckTrace, ...] = (attack,)
    hit = attack.outcome.succeeded
    blocked = None
    table: tuple[int, ...] = ()
    if attack.outcome in (Outcome.CRITICAL_FAILURE, Outcome.CRITICAL_SUCCESS):
        # B557's unarmed critical-miss table is not the armed critical-miss table.
        table = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        blocked = f"basic-unarmed-critical:{attack.outcome.value}:{sum(table)}"
        hit = False
    if hit and defense_target is not None:
        state, can_defend = exertion(play, state, target.actor_id, command.id)
        if can_defend:
            defense = success_roll(BASIC, defense_target, rng=play.rng)
            checks += (defense,)
            hit = not defense.outcome.succeeded
            if hand:
                target = target.model_copy(update={"parries": target.parries + (hand,)})
            if defense.outcome in (Outcome.CRITICAL_FAILURE, Outcome.CRITICAL_SUCCESS):
                table = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
                blocked = f"basic-unarmed-defense-critical:{defense.outcome.value}:{sum(table)}"
                hit = False
            target = target.model_copy(
                update={
                    "maneuver_state": target.maneuver_state.model_copy(update={"defended": True})
                }
            )
            from wayfarer.orchestration.gurps_maneuvers import distracted

            encounter = CombatEngine._replace(encounter, target)
            encounter = distracted(
                play, state, encounter, target.actor_id, defended=True, injured=False
            )
    if pending.action == "kick" and not attack.outcome.succeeded and blocked is None:
        balance = success_roll(
            BASIC, skill_value(play, state, actor.actor_id, "attribute:dx"), rng=play.rng
        )
        checks += (balance,)
        if not balance.outcome.succeeded:
            actor = actor.model_copy(update={"posture": "prone"})
    encounter = CombatEngine._replace(encounter, actor)
    grip_id = None
    basic = injury = 0
    dice: tuple[int, ...] = ()
    if hit and pending.action in ("grapple", "arm_lock"):
        grip_id = pending.id
        grip = Grip(
            id=grip_id,
            holder_id=actor.actor_id,
            target_id=target.actor_id,
            hands=pending.hands,
            location=pending.location,
            skill=pending.skill,
            acquired_round=encounter.round,
            arm_lock=pending.action == "arm_lock",
        )
        encounter = encounter.model_copy(
            update={"grips": tuple(g for g in encounter.grips if g.id != pending.grip_id) + (grip,)}
        )
    elif hit:
        compiled = build(play, state, actor.actor_id)
        assert compiled.statistics is not None
        expression = compiled.statistics.thrust
        dice = tuple(play.rng.randbelow(6) + 1 for _ in range(expression.dice))
        bonus = striking_bonus(
            pending.skill,
            compiled.statistics.dx,
            skill_value(play, state, actor.actor_id, pending.skill),
        )
        basic = max(
            0,
            sum(dice)
            + expression.add
            + (-1 if pending.action == "punch" else 0)
            + bonus * expression.dice,
        )
        resistance = armor_dr(play, state, target.actor_id, "torso")
        state, encounter, injury = hurt(play, state, encounter, target.actor_id, pending.id, basic)
        if resistance >= 3 and basic >= 5:
            striking_part = pending.hands[0] if pending.action == "punch" else pending.foot
            state, encounter, _ = hurt(
                play,
                state,
                encounter,
                actor.actor_id,
                "unarmed-self:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                min(resistance, basic // 5),
                location=striking_part,
            )
    return (
        state,
        encounter,
        UnarmedTrace(
            action=pending.action,
            intent=pending,
            actor_id=actor.actor_id,
            target_id=target.actor_id,
            checks=checks,
            won=hit,
            grip_id=grip_id,
            basic_damage=basic,
            damage_dice=dice,
            injury=injury,
            blocked_reason=blocked,
            table_dice=table,
        ),
    )


def armor_dr(
    play: PlayService,
    state: PlayState,
    actor_id: str,
    location: HumanLocation,
    *,
    rigid_only: bool = False,
) -> int:
    from wayfarer.simulation.hit_locations import part

    entries = {e.definition_id: e for e in catalog(play).entries}
    resistance = max(
        (
            e.armor.dr
            for i in state.resources.items
            if i.owner_id == actor_id and i.equipped
            for e in (entries[i.definition_id],)
            if e.armor
            and (location in e.armor.locations or part(location) + "s" in e.armor.locations)
            and not (rigid_only and e.armor.flexible)
        ),
        default=0,
    )
    if play.engine.rules.abilities is not None:
        from wayfarer.simulation.abilities import damage_resistance

        resistance += damage_resistance(
            state.resources, actor_id, build_revision=build(play, state, actor_id).revision
        )
    return resistance


def hurt(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    basic: int,
    *,
    location: HumanLocation = "torso",
    rigid_only: bool = False,
) -> tuple[PlayState, Encounter, int]:
    target = fighter(encounter, actor_id)
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    resistance = armor_dr(play, state, actor_id, location, rigid_only=rigid_only)
    resources, result = apply_injury(
        state.resources,
        Wound(
            id=command_id,
            actor_id=actor_id,
            expected_revision=state.resources.revision,
            basic_damage=basic,
            resistance=resistance,
            damage_type="cr",
            location=location,
        ),
        ht=compiled.statistics.ht,
        dx=compiled.statistics.dx,
        rng=play.rng,
        system=True,
        held_item_ids=target.ready_item_ids,
        held_item_locations=target.hand_bindings,
    )
    state = state.model_copy(update={"resources": resources})
    hp = next(p for p in resources.pools if p.id == f"hp:{actor_id}")
    assert hp.injury is not None
    if hp.injury.incapacitated:
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(
                        update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                    )
                    if a.actor_id == actor_id
                    else a
                    for a in state.actors
                )
            }
        )
    target = target.model_copy(
        update={
            "posture": "prone" if hp.injury.prone else target.posture,
            "ready_item_ids": tuple(
                sorted(
                    i.id
                    for i in resources.items
                    if i.owner_id == actor_id and i.ready and i.equipped
                )
            ),
        }
    )
    encounter = CombatEngine._replace(encounter, target)
    from wayfarer.orchestration.gurps_maneuvers import distracted

    encounter = distracted(
        play, state, encounter, actor_id, defended=False, injured=result.injury > 0
    )
    return state, encounter, result.injury


def control(
    play: PlayService, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> tuple[PlayState, Encounter, UnarmedTrace]:
    grip = next(g for g in encounter.grips if g.id == command.grip_id)
    actor, target = fighter(encounter, command.actor_id), fighter(encounter, command.target_id)
    checks: tuple[CheckTrace, ...] = ()
    won = True
    damage = injury = 0
    if command.action == "release":
        encounter = encounter.model_copy(
            update={"grips": tuple(g for g in encounter.grips if g.id != grip.id)}
        )
    elif command.action == "break_free":
        first = strength(play, state, actor.actor_id) - grip.escape_penalty
        target_hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
        assert target_hp.injury is not None
        second = (
            strength(play, state, target.actor_id)
            + (5 if len(grip.hands) == 2 else 0)
            + (5 if grip.pinned else 0)
            + (4 if grip.arm_lock else 0)
            - (4 if target_hp.injury.stunned else 0)
        )
        won, checks, _ = contest(
            BASIC, actor.actor_id, target.actor_id, first, second, rng=play.rng
        )
        encounter = encounter.model_copy(
            update={
                "grips": tuple(
                    g.model_copy(update={"escape_after_round": encounter.round + 10})
                    if g.id == grip.id and g.pinned and not won
                    else g
                    for g in encounter.grips
                    if not (g.id == grip.id and won)
                )
            }
        )
        if grip.arm_lock and not won:
            encounter = encounter.model_copy(
                update={
                    "grips": tuple(
                        g.model_copy(update={"escape_penalty": g.escape_penalty + 1})
                        if g.id == grip.id
                        else g
                        for g in encounter.grips
                    )
                }
            )
    elif command.action in ("strangle", "lock_damage"):
        compiled = build(play, state, target.actor_id)
        assert compiled.statistics is not None
        first = strength(play, state, actor.actor_id, trained=False)
        if command.action == "strangle":
            first -= 5 if len(grip.hands) == 1 else 0
        else:
            attacker = build(play, state, actor.actor_id)
            first = max(
                first,
                *(
                    int(v.value)
                    for v in attacker.sheet.values
                    if v.target in {"skill:judo", "skill:wrestling"}
                ),
            )
        second = max(strength(play, state, target.actor_id, trained=False), compiled.statistics.ht)
        won, checks, _ = contest(
            BASIC, actor.actor_id, target.actor_id, first, second, rng=play.rng
        )
        if won:
            damage = checks[0].margin - checks[1].margin
            state, encounter, injury = hurt(
                play,
                state,
                encounter,
                target.actor_id,
                "control-damage:" + hashlib.sha256(command.id.encode()).hexdigest(),
                damage,
                location=grip.location,
                rigid_only=command.action == "lock_damage",
            )
        if command.action == "strangle" and injury > 0 and grip.hazard_id is None:
            state, grip = start_choke(play, state, grip, command.id)
        grip = grip.model_copy(update={"last_damage_round": encounter.round})
        encounter = encounter.model_copy(
            update={"grips": tuple(grip if g.id == grip.id else g for g in encounter.grips)}
        )
    elif command.action == "takedown":

        def score(actor_id: str) -> int:
            compiled = build(play, state, actor_id)
            return max(
                strength(play, state, actor_id),
                *(
                    int(v.value)
                    for v in compiled.sheet.values
                    if v.target
                    in {"attribute:dx", "skill:judo", "skill:wrestling", "skill:sumo-wrestling"}
                ),
            )

        won, checks, decided = contest(
            BASIC,
            actor.actor_id,
            target.actor_id,
            score(actor.actor_id)
            - (4 if actor.posture == "prone" else 2 if actor.posture == "kneeling" else 0),
            score(target.actor_id),
            rng=play.rng,
        )
        if decided:
            falling = target if won else actor
            encounter = CombatEngine._replace(
                encounter, falling.model_copy(update={"posture": "prone"})
            ).model_copy(
                update={
                    "grips": tuple(
                        g
                        for g in encounter.grips
                        if not (
                            g.holder_id == falling.actor_id
                            and g.target_id in (actor.actor_id, target.actor_id)
                        )
                    )
                }
            )
    else:
        # B370: compare free hands, counting hands already holding this grapple.
        hands_a = len(free_hands(state, encounter, actor.actor_id)) + len(grip.hands)
        hands_b = len(free_hands(state, encounter, target.actor_id))
        won, checks, _ = contest(
            BASIC,
            actor.actor_id,
            target.actor_id,
            strength(play, state, actor.actor_id) + (3 if hands_a > hands_b else 0),
            strength(play, state, target.actor_id) + (3 if hands_b > hands_a else 0),
            regular=True,
            rng=play.rng,
        )
        if won:
            encounter = encounter.model_copy(
                update={
                    "grips": tuple(
                        g.model_copy(
                            update={"pinned": True, "escape_after_round": encounter.round + 10}
                        )
                        if g.id == grip.id
                        else g
                        for g in encounter.grips
                    )
                }
            )
    return (
        state,
        encounter,
        UnarmedTrace(
            action=command.action,
            actor_id=actor.actor_id,
            target_id=target.actor_id,
            checks=checks,
            won=won,
            grip_id=grip.id,
            basic_damage=damage,
            injury=injury,
        ),
    )


def start_choke(
    play: PlayService, state: PlayState, grip: Grip, command_id: str
) -> tuple[PlayState, Grip]:
    """The existing suffocation schedule owns FP, consciousness and death timing."""
    from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec
    from wayfarer.simulation.hazards import HazardCommand, apply_hazard

    compiled = build(play, state, grip.target_id)
    assert compiled.statistics is not None
    entity = next(e for e in state.world.entities if e.id == grip.target_id)
    schedule = HazardSchedule(
        id="grip-air:" + hashlib.sha256(grip.id.encode()).hexdigest(),
        actor_id=grip.target_id,
        spec=HazardSpec(
            id=grip.id,
            kind="suffocation",
            scene_id=entity.location_id or "unknown",
            delay=1,
            interval=1,
            cycles=240,
            resistible=False,
            reference="B370/B436",
        ),
        started=state.resources.game_time,
        due=state.resources.game_time + 1,
        remaining=240,
        ht=compiled.statistics.ht,
        will=compiled.statistics.will,
        swimming=compiled.statistics.ht,
        no_air_since=state.resources.game_time,
    )
    resources, _ = apply_hazard(
        state.resources,
        HazardCommand(
            id="choke-start:" + hashlib.sha256(command_id.encode()).hexdigest(),
            actor_id=grip.target_id,
            expected_revision=state.resources.revision,
            kind="enter",
            hazard_id=grip.id,
        ),
        schedule,
        rng=play.rng,
        system=True,
    )
    return state.model_copy(update={"resources": resources}), grip.model_copy(
        update={"hazard_id": schedule.id}
    )


def resolve_choke(
    play: PlayService, state: PlayState, encounter: Encounter, command: ResolveChokeEffects
) -> tuple[PlayState, CombatResult]:
    from wayfarer.simulation.hazards import HazardCommand, apply_hazard

    require_basic(catalog(play).profile_id)
    grip = next((g for g in encounter.grips if g.id == command.grip_id), None)
    if grip is None or grip.target_id != command.actor_id or grip.hazard_id is None:
        raise ValidationError("Only the choking actor may settle this grip's due effects")
    schedule = next(h for h in state.resources.hazards if h.id == grip.hazard_id)
    resources, _ = apply_hazard(
        state.resources,
        HazardCommand(
            id="choke-tick:" + hashlib.sha256(command.id.encode()).hexdigest(),
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            kind="resolve",
            hazard_id=grip.id,
        ),
        schedule,
        rng=play.rng,
        system=True,
    )
    state = state.model_copy(update={"resources": resources})
    if not fatigue_ready(state, command.actor_id):
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(
                        update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                    )
                    if a.actor_id == command.actor_id
                    else a
                    for a in state.actors
                )
            }
        )
    return state, CombatResult(
        encounter_id=encounter.id,
        code="combat.choke_effects_resolved",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )


def retire_chokes(
    play: PlayService,
    state: PlayState,
    before: tuple[Grip, ...],
    after: tuple[Grip, ...],
    command_id: str,
) -> PlayState:
    from wayfarer.simulation.hazards import HazardCommand, apply_hazard

    remaining = {g.id for g in after}
    for grip in before:
        if grip.id in remaining or grip.hazard_id is None:
            continue
        schedule = next(h for h in state.resources.hazards if h.id == grip.hazard_id)
        resources, _ = apply_hazard(
            state.resources,
            HazardCommand(
                id="choke-end:" + hashlib.sha256((command_id + grip.id).encode()).hexdigest(),
                actor_id=grip.target_id,
                expected_revision=state.resources.revision,
                kind="leave",
                hazard_id=grip.id,
            ),
            schedule,
            rng=play.rng,
            system=True,
        )
        state = state.model_copy(update={"resources": resources})
    return state
