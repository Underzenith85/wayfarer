"""Unarmed actions inside CombatService's existing CAS/receipt transaction.

Internal commands only: frozen v1 and saved coordinate systems are unchanged.
Contextual critical consequences not yet implemented retain their dice and block continuation.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

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
from wayfarer.simulation.condition_checks import check_modifiers, retching_penalty
from wayfarer.simulation.fatigue import fatigue_value
from wayfarer.simulation.gurps_equipment import DamageType
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.maneuvers import ManeuverState, WaitInterrupt, WaitTrigger
from wayfarer.simulation.physical_traits import physical_traits
from wayfarer.simulation.unarmed import (
    BASIC,
    GrappleLocation,
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
        TakeCombatTurn,
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


def guard_control(encounter: Encounter, command: TypedCombatCommand, state: PlayState) -> None:
    from wayfarer.orchestration.combat import ChooseDefense, TakeCombatTurn, TakeUnarmedTurn

    if encounter.pending_unarmed is not None:
        if (
            not isinstance(command, ChooseDefense)
            or command.actor_id != encounter.pending_unarmed.target_id
        ):
            raise ConflictError("Only the target may resolve the pending unarmed defense")
        return
    if isinstance(command, TakeUnarmedTurn):
        if encounter.status != "active" or encounter.pending_defense or encounter.blocked_reason:
            raise ConflictError("Encounter cannot accept unarmed action now")
        interrupt = encounter.wait_interrupt
        if interrupt is not None:
            # Only the waiter's own declared reaction may act inside a paused turn.
            if interrupt.ready or interrupt.reacting or command.actor_id != interrupt.waiter_id:
                raise ConflictError("Resolve the interrupted Wait before another unarmed action")
        elif command.actor_id != encounter.current_actor_id:
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
        if engaged and command.maneuver == "ready":
            hands = (
                ("left-hand", "right-hand")
                if command.ready_hand == "both"
                else (command.ready_hand,)
            )
            if any(h not in free_hands(state, encounter, actor.actor_id) for h in hands):
                raise ValidationError("Ready while grappling requires explicit free usable hands")
            if command.reload_ammunition_id is not None or command.unload_ammunition:
                raise ValidationError("Reloading while grappling requires further integration")
        if engaged and command.maneuver in ("feint", "aim", "concentrate"):
            raise ValidationError("This maneuver while grappling requires further integration")
        if actor.grappled and command.posture is not None:
            raise ValidationError("A grapple prevents a posture step")


def skill_value(play: PlayService, state: PlayState, actor_id: str, skill: str) -> int:
    compiled = build(play, state, actor_id)
    value = next((v for v in compiled.sheet.values if v.target == skill), None)
    if value is None:
        raise ValidationError("Selected unarmed skill has no compiled level")
    return int(value.value)


def grapple_ready(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn,
) -> tuple[PlayState, Encounter]:
    """B371: drawing with a free hand requires DX; failure drops that item only."""
    from wayfarer.simulation.resources import ResourceEvent

    if not any(g.target_id == command.actor_id for g in encounter.grips):
        return state, encounter
    actor = fighter(encounter, command.actor_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{command.actor_id}")
    assert hp.injury is not None
    score = skill_value(play, state, command.actor_id, "attribute:dx") - hp.injury.shock
    score -= 4 if actor.grappled else 0
    check = success_roll(
        BASIC, score, check_modifiers(state.resources, command.actor_id, "dx"), rng=play.rng
    )
    resources = state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id="grapple-ready:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    at=state.resources.game_time,
                    target_id=command.actor_id,
                    kind=TypeAdapter(CheckTrace).dump_json(check).decode(),
                ),
            ),
        }
    )
    if not check.outcome.succeeded:
        resources = resources.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"ready": False, "equipped": False})
                    if i.id == command.item_id
                    else i
                    for i in resources.items
                )
            }
        )
        actor = actor.model_copy(
            update={
                "ready_item_ids": tuple(i for i in actor.ready_item_ids if i != command.item_id),
                "hand_bindings": tuple(
                    (i, h) for i, h in actor.hand_bindings if i != command.item_id
                ),
            }
        )
        encounter = CombatEngine._replace(encounter, actor)
    return state.model_copy(update={"resources": resources}), encounter


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


def declare_unarmed_wait(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    trigger: WaitTrigger,
) -> None:
    """B366: an unarmed Wait fixes the whole attack in advance, not once it fires.

    Only the waiter's own stable choices are checked here; reach, posture and the
    target's state belong to the reaction itself, which is validated when it happens.
    """
    declared = trigger.unarmed
    assert declared is not None
    require_basic(catalog(play).profile_id)
    if trigger.reaction_target_id is None or trigger.reaction_target_id == actor_id:
        raise ValidationError("An unarmed Wait reaction requires one declared foe")
    skills = {
        "punch": {"attribute:dx", "skill:brawling", "skill:boxing", "skill:karate"},
        "kick": {"attribute:dx", "skill:brawling", "skill:karate"},
        "grapple": {"attribute:dx", "skill:judo", "skill:wrestling", "skill:sumo-wrestling"},
        "arm_lock": {"skill:judo", "skill:wrestling"},
    }[declared.action]
    if declared.skill not in skills:
        raise ValidationError("Skill does not support this unarmed action")
    skill_value(play, state, actor_id, declared.skill)
    actor = fighter(encounter, actor_id)
    if declared.action == "kick":
        if declared.hands or actor.posture != "standing":
            raise ValidationError("Kick requires two usable legs and standing posture")
    elif (
        not declared.hands
        or len(declared.hands) > (1 if declared.action == "punch" else 2)
        or not set(declared.hands) <= set(free_hands(state, encounter, actor_id))
    ):
        raise ValidationError("Attack requires explicit free, usable hands")
    if declared.action == "arm_lock":
        grip = next((g for g in encounter.grips if g.id == declared.grip_id), None)
        if (
            grip is None
            or grip.holder_id != actor_id
            or grip.target_id != trigger.reaction_target_id
            or grip.hands != declared.hands
        ):
            raise ValidationError("An arm-lock reaction requires the waiter's own grapple")


def require_declared(encounter: Encounter, command: TakeUnarmedTurn) -> None:
    """A Wait reaction executes exactly the declaration recorded before the trigger."""
    interrupt = encounter.wait_interrupt
    assert interrupt is not None
    declaration = interrupt.declaration
    declared = declaration.unarmed
    if declared is None:
        raise ValidationError("The declared Wait reaction is not an unarmed attack")
    waiter = fighter(encounter, interrupt.waiter_id)
    # B366: an All-Out Attack reaction degrades to an Attack once the waiter has defended.
    degraded = declaration.reaction == "all_out_attack" and waiter.maneuver_state.defended
    reaction = "attack" if degraded else declaration.reaction
    option = None if degraded else declaration.attack_option
    if (
        command.maneuver != reaction
        or command.attack_option != option
        or command.enter_close_combat
        or (declaration.reaction_target_id or command.target_id) != command.target_id
        or (declared.action, declared.skill, declared.foot, declared.location, declared.grip_id)
        != (command.action, command.skill, command.foot, command.location, command.grip_id)
        or declared.hands != command.hands
    ):
        raise ValidationError("Wait reaction must match its recorded declaration")


def validate_action(
    play: PlayService, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> None:
    require_basic(catalog(play).profile_id)
    if command.maneuver == "all_out_attack":
        if command.attack_option not in ("determined", "strong"):
            raise ValidationError(
                "Unarmed All-Out Attack requires Determined or Strong; combined attacks remain unsupported"
            )
        if command.action not in ("punch", "kick", "grapple", "arm_lock"):
            raise ValidationError("This control action requires the ordinary Attack maneuver")
        if command.attack_option == "strong" and command.action not in ("punch", "kick"):
            raise ValidationError("Strong requires a damaging strike")
    elif command.attack_option is not None:
        raise ValidationError("Attack options require All-Out Attack")
    if command.maneuver == "move_and_attack" and command.action not in ("punch", "kick", "grapple"):
        raise ValidationError("Move and Attack requires a strike or grapple")
    actor, target = fighter(encounter, command.actor_id), fighter(encounter, command.target_id)
    if actor.actor_id == target.actor_id:
        raise ValidationError("Unarmed action requires another actor")
    for participant in (actor, target):
        hp = next(p for p in state.resources.pools if p.id == f"hp:{participant.actor_id}")
        if hp.injury is None or hp.injury.anatomy != "human":
            raise ValidationError("Unarmed combat requires explicit living-human anatomy")
    from wayfarer.simulation.hit_locations import require_location

    target_hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
    assert target_hp.injury is not None
    require_location(target_hp.injury, command.location)
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
    if encounter.wait_interrupt is not None:
        require_declared(encounter, command)
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
        attack_geometry(
            encounter,
            actor,
            target,
            frozenset({0, 1})
            if command.action == "kick" or command.enter_close_combat
            else frozenset({0}),
            location=command.location,
        )
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
        if command.skill in ("skill:judo", "skill:karate"):
            encumbrance_level(play, state, actor.actor_id)
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
    else:
        if (
            (command.hands and command.action != "release")
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
        if (
            command.action == "release"
            and command.hands
            and (
                len(set(command.hands)) != len(command.hands)
                or not set(command.hands) <= set(grip.hands)
                or (grip.arm_lock and set(command.hands) != set(grip.hands))
            )
        ):
            raise ValidationError("Release must select held hands; an arm lock requires both hands")
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


def interrupt_wait(
    play: PlayService, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> tuple[Encounter, CombatResult] | None:
    """B366: an unarmed action is an observable attack, so a declared Wait resolves first.

    Only turn-consuming actions trigger; releasing a grip and arm-lock damage are free.
    Close-combat entry is applied before the pause, exactly as an armed step is, and is
    stripped from the saved command so the resumed turn cannot move a second time.
    """
    engine = play.engine.combat
    assert engine is not None
    if command.action in ("release", "lock_damage"):
        return None
    actor = fighter(encounter, command.actor_id)
    entered, pairs = actor, encounter.close_pairs
    if command.enter_close_combat:
        target = fighter(encounter, command.target_id)
        entered = actor.model_copy(update={"position": target.position})
        merged = set(pairs)
        merged.update(
            (min(actor.actor_id, p.actor_id), max(actor.actor_id, p.actor_id))
            for p in encounter.participants
            if p.actor_id != actor.actor_id and p.position == target.position
        )
        pairs = tuple(sorted(merged))
    moved = CombatEngine._replace(encounter, entered).model_copy(update={"close_pairs": pairs})
    for waiter_id in encounter.turn_order:
        waiter = fighter(encounter, waiter_id)
        trigger = waiter.maneuver_state.wait
        if (
            waiter_id == command.actor_id
            or trigger is None
            or trigger.action != "attack"
            or (trigger.actor_id is not None and trigger.actor_id != command.actor_id)
            or (trigger.target_id is not None and trigger.target_id != command.target_id)
        ):
            continue
        if trigger.stop_thrust:
            # A stop thrust rewards a closing move; entering close combat is not that case.
            if command.enter_close_combat:
                raise ValidationError("A stop thrust against close-combat entry is unsupported")
            continue
        if encounter.hex_battlefield is not None:
            from wayfarer.simulation.tactical import sight

            if not sight(moved, waiter, entered):
                continue
        saved = command.model_copy(update={"enter_close_combat": False})
        paused = CombatEngine._replace(
            moved,
            waiter.model_copy(
                update={"maneuver_state": waiter.maneuver_state.model_copy(update={"wait": None})}
            ),
        ).model_copy(
            update={
                "wait_interrupt": WaitInterrupt(
                    waiter_id=waiter_id,
                    actor_id=command.actor_id,
                    turn_index=encounter.turn_index,
                    command_json=saved.model_dump_json(),
                    declaration=trigger,
                )
            }
        )
        return paused, CombatResult(
            encounter_id=paused.id,
            code="combat.wait_triggered",
            round=paused.round,
            current_actor_id=command.actor_id,
            available=engine.available(paused, waiter_id),
        )
    return None


def execute_unarmed(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    command: TakeUnarmedTurn | ChooseDefense,
) -> tuple[PlayState, Encounter, CombatResult]:
    from wayfarer.orchestration.combat import ChooseDefense

    require_basic(catalog(play).profile_id)
    # A declared Wait reaction borrows the interrupted turn; it is not a second turn.
    reacting = encounter.wait_interrupt is not None
    if isinstance(command, ChooseDefense):
        state, encounter, trace = defend(play, state, encounter, command)
    else:
        if reacting:
            assert encounter.wait_interrupt is not None
            encounter = encounter.model_copy(
                update={
                    "turn_index": encounter.turn_order.index(command.actor_id),
                    "wait_interrupt": encounter.wait_interrupt.model_copy(
                        update={"reacting": True}
                    ),
                }
            )
        validate_action(play, state, encounter, command)
        if not reacting:
            fired = interrupt_wait(play, state, encounter, command)
            if fired is not None:
                return state, fired[0], fired[1]
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
        if not reacting:
            state = injury_turn(
                play,
                state,
                actor.actor_id,
                command.id,
                start=True,
                do_nothing=actor.forced_do_nothing,
            )
        hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
        assert hp.injury is not None
        allowed = not (hp.injury.incapacitated or hp.injury.stunned or actor.forced_do_nothing)
        if allowed:
            state, allowed = exertion(play, state, actor.actor_id, command.id)
        if not allowed:
            if not reacting:
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
        previous = actor.maneuver_state
        actor = actor.model_copy(
            update={
                "last_maneuver": command.maneuver,
                "maneuver_state": ManeuverState(
                    evaluate_target_id=previous.evaluate_target_id
                    if actor.last_maneuver == "evaluate"
                    else None,
                    evaluate_bonus=previous.evaluate_bonus
                    if actor.last_maneuver == "evaluate"
                    else 0,
                    feint_target_id=previous.feint_target_id
                    if actor.last_maneuver == "feint"
                    else None,
                    feint_penalty=previous.feint_penalty if actor.last_maneuver == "feint" else 0,
                    defense_forbidden=command.maneuver == "all_out_attack",
                    parry_forbidden=command.maneuver == "move_and_attack",
                    attack_bonus=4
                    if command.attack_option == "determined"
                    else -4
                    if command.maneuver == "move_and_attack"
                    else 0,
                    attack_cap=9 if command.maneuver == "move_and_attack" else None,
                    strong=command.attack_option == "strong",
                ),
            }
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
                    unarmed_defense(
                        play,
                        state,
                        encounter,
                        command.target_id,
                        choice,
                        None,
                        attacker_id=actor.actor_id,
                        location=command.location,
                    )
                except ValidationError:
                    if choice != "parry":
                        continue
                    candidates = (
                        (i.id, m.id)
                        for i in state.resources.items
                        if i.id in fighter(encounter, command.target_id).ready_item_ids
                        for e in catalog(play).entries
                        if e.definition_id == i.definition_id
                        for m in e.modes
                    )
                    for item, selected_mode in candidates:
                        try:
                            unarmed_defense(
                                play,
                                state,
                                encounter,
                                command.target_id,
                                choice,
                                item,
                                attacker_id=actor.actor_id,
                                location=command.location,
                                mode_id=selected_mode,
                            )
                        except ValidationError:
                            continue
                        break
                    else:
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
    if not reacting:
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
    attacker_id: str | None = None,
    location: GrappleLocation = "torso",
    mode_id: str | None = None,
) -> tuple[int | None, str | None]:
    actor = fighter(encounter, actor_id)
    if mode_id is not None and (
        selected != "parry" or item_id in (None, "left-hand", "right-hand")
    ):
        raise ValidationError("A parry damage mode requires a selected weapon")
    if selected == "none":
        if item_id is not None:
            raise ValidationError("No defense cannot select equipment")
        return None, None
    if actor.pinned or actor.maneuver_state.defense_forbidden:
        raise ValidationError("Actor cannot defend")
    from wayfarer.simulation.fright import can_defend

    if not can_defend(state.resources, actor_id):
        raise ValidationError("Fright condition prevents active defense")
    height_bonus = 0
    if encounter.hex_battlefield is not None:
        from wayfarer.simulation.tactical import defense_adjustment, height_effect

        source_id = (
            encounter.pending_unarmed.actor_id
            if encounter.pending_unarmed is not None
            else attacker_id
        )
        if source_id is not None:
            attacker = fighter(encounter, source_id)
            defense_adjustment(encounter, attacker, actor)
            height_bonus = height_effect(
                encounter, attacker, actor, reach=1, location=location
            ).defender_modifier
    if selected == "dodge":
        if item_id is not None:
            raise ValidationError("Dodge cannot select equipment")
        value, _ = defense_value(play, state, actor, "dodge")
        assert value is not None
        return int(value.value) + height_bonus, None
    if selected != "parry" or actor.maneuver_state.parry_forbidden:
        raise ValidationError("Only Dodge or an unarmed Parry is supported")
    if item_id is not None and item_id not in ("left-hand", "right-hand"):
        from wayfarer.orchestration.gurps_melee import mode
        from wayfarer.simulation.gurps_equipment import MeleeMode

        weapon = mode(play, state, actor_id, item_id, mode_id)
        if not isinstance(weapon, MeleeMode):
            raise ValidationError("Armed parry requires an unambiguous melee mode")
        source_id = encounter.pending_unarmed.actor_id if encounter.pending_unarmed else attacker_id
        if (
            source_id is not None
            and fighter(encounter, source_id).position == actor.position
            and 0 not in weapon.reach
        ):
            raise ValidationError("A weapon parry in close combat requires reach C")
        value, selected_item = defense_value(
            play, state, actor, "parry", item_id, parry_mode_id=weapon.id
        )
        assert value is not None
        return int(value.value) + height_bonus, selected_item
    hand = item_id or next(iter(free_hands(state, encounter, actor_id)), None)
    if hand not in free_hands(state, encounter, actor_id):
        raise ValidationError("Unarmed parry requires a free usable hand")
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury is None or hp.injury.incapacitated or not fatigue_ready(state, actor_id):
        raise ValidationError("Incapacitated actor cannot parry")
    targets = [compiled.statistics.dx // 2 + 3]
    incoming_kick = (
        encounter.pending_unarmed is not None and encounter.pending_unarmed.action == "kick"
    )
    for v in compiled.sheet.values:
        if v.target in {"skill:brawling", "skill:boxing", "skill:karate", "skill:judo"}:
            score = int(v.value) // 2 + 3
            score -= 2 if v.target == "skill:boxing" and incoming_kick else 0
            if v.target in ("skill:boxing", "skill:judo", "skill:karate") and (
                encounter.pending_unarmed is not None
                and actor.retreat_attacker_id == encounter.pending_unarmed.actor_id
            ):
                score += 2  # B377: +3 total, including prepare_defense's ordinary +1.
            if v.target in ("skill:judo", "skill:karate"):
                try:
                    score -= encumbrance_level(play, state, actor_id)
                except ValidationError:
                    continue
            targets.append(score)
    from wayfarer.simulation.fright import stunned as fright_stunned

    penalty = (
        (-4 if hp.injury.stunned or fright_stunned(state.resources, actor_id) else 0)
        + (-3 if actor.posture == "prone" else -2 if actor.posture == "kneeling" else 0)
        + (-2 if actor.grappled else 0)
    )
    penalty += (
        actor.defense_penalty
        + actor.tactical_defense_bonus
        - 4 * int(actor.arm_locked)
        + (2 if actor.maneuver_state.enhanced_defense == "parry" else 0)
    )
    return max(targets) + int(
        hp.injury.physical_traits.combat_reflexes
    ) + penalty + height_bonus - 4 * actor.parries.count(hand), hand


def encumbrance_level(play: PlayService, state: PlayState, actor_id: str) -> int:
    from wayfarer.simulation.gurps_equipment import inventory_load

    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    level = inventory_load(
        catalog(play), play.engine.resources, state.resources, actor_id, compiled.statistics
    ).level
    if level is None:
        raise ValidationError("Overloaded Judo/Karate requires a supported encumbrance level")
    return int(level)


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
    defense_target, hand = unarmed_defense(
        play,
        state,
        encounter,
        command.actor_id,
        command.defense,
        command.item_id,
        location=pending.location,
        mode_id=command.parry_mode_id,
    )
    actor, target = fighter(encounter, pending.actor_id), fighter(encounter, pending.target_id)
    defenses = [(defense_target, hand)]
    if command.second_defense is None:
        if command.second_item_id is not None or command.second_parry_mode_id is not None:
            raise ValidationError("Second defense equipment requires a second defense")
    else:
        if (
            command.defense == "none"
            or command.second_defense == "none"
            or target.maneuver_state.enhanced_defense != "double"
        ):
            raise ValidationError("Second defense requires All-Out Defense (Double)")
        if command.second_defense not in pending.allowed:
            raise ValidationError("Second defense is not available against this attack")
        second_target, second_hand = unarmed_defense(
            play,
            state,
            encounter,
            command.actor_id,
            command.second_defense,
            command.second_item_id,
            location=pending.location,
            mode_id=command.second_parry_mode_id,
        )
        if command.defense == command.second_defense and not (
            command.defense == "parry" and hand != second_hand
        ):
            raise ValidationError(
                "Double defense requires different defenses or different parrying hands"
            )
        defenses.append((second_target, second_hand))
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    assert hp.injury is not None
    value = skill_value(play, state, actor.actor_id, pending.skill) - hp.injury.shock
    value += hp.injury.physical_traits.darkness(encounter.darkness_penalty)
    if pending.skill in ("skill:judo", "skill:karate"):
        value -= encumbrance_level(play, state, actor.actor_id)
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
    if pending.action in ("punch", "kick"):
        value -= {
            "torso": 0,
            "neck": 5,
            "left-arm": 2,
            "right-arm": 2,
            "left-leg": 2,
            "right-leg": 2,
        }[pending.location]
    from wayfarer.simulation.maneuvers import attack_modifier

    if actor.maneuver_state.feint_target_id == target.actor_id:
        defenses = [
            (v - actor.maneuver_state.feint_penalty if v is not None else None, h)
            for v, h in defenses
        ]
    if encounter.hex_battlefield is not None:
        from wayfarer.simulation.tactical import height_effect

        value += height_effect(
            encounter, actor, target, reach=1, location=pending.location
        ).attack_modifier
    value = attack_modifier(
        actor.maneuver_state,
        target.actor_id,
        value,
        check_adjustment=sum(
            m.value for m in check_modifiers(state.resources, actor.actor_id, "dx")
        ),
    )
    attack = success_roll(
        BASIC, value, check_modifiers(state.resources, actor.actor_id, "dx"), rng=play.rng
    )
    from wayfarer.simulation.hit_locations import torso_near_miss

    near_miss = pending.action in ("punch", "kick") and torso_near_miss(pending.location, attack)
    resolved_location = "torso" if near_miss else pending.location
    checks: tuple[CheckTrace, ...] = (attack,)
    hit = attack.outcome.succeeded or near_miss
    blocked = None
    table: tuple[int, ...] = ()
    effect_dice: tuple[int, ...] = ()
    effect_checks: tuple[CheckTrace, ...] = ()
    critical = 0
    if attack.outcome is Outcome.CRITICAL_SUCCESS:
        table = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        critical = sum(table)
    elif attack.outcome is Outcome.CRITICAL_FAILURE:
        # B557's unarmed critical-miss table is not the armed critical-miss table.
        table = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        state, encounter, effect_checks, effect_dice, handled = critical_miss(
            play, state, encounter, pending, actor.actor_id, table, None
        )
        actor = fighter(encounter, actor.actor_id)
        blocked = None if handled else f"basic-unarmed-critical:{attack.outcome.value}:{sum(table)}"
        hit = False
    if hit and defense_target is not None and not critical:
        state, can_defend = exertion(play, state, target.actor_id, command.id)
        if can_defend:
            for index, (defense_level, parrying_hand) in enumerate(defenses):
                assert defense_level is not None
                defense = success_roll(BASIC, defense_level, rng=play.rng)
                checks += (defense,)
                hit = not defense.outcome.succeeded
                if parrying_hand:
                    target = target.model_copy(
                        update={"parries": target.parries + (parrying_hand,)}
                    )
                if defense.outcome is Outcome.CRITICAL_FAILURE and parrying_hand is None:
                    # B382: a critical Dodge falls, without a critical-miss table roll.
                    target = target.model_copy(update={"posture": "prone"})
                elif defense.outcome in (Outcome.CRITICAL_FAILURE, Outcome.CRITICAL_SUCCESS):
                    table = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
                    subject = actor.actor_id if defense.outcome.succeeded else target.actor_id
                    encounter = CombatEngine._replace(encounter, target)
                    state, encounter, effect_checks, effect_dice, handled = critical_miss(
                        play,
                        state,
                        encounter,
                        pending,
                        subject,
                        table,
                        None if defense.outcome.succeeded else parrying_hand,
                    )
                    actor, target = (
                        fighter(encounter, actor.actor_id),
                        fighter(encounter, target.actor_id),
                    )
                    blocked = (
                        None
                        if handled
                        else f"basic-unarmed-defense-critical:{defense.outcome.value}:{sum(table)}"
                    )
                    hit = handled and not defense.outcome.succeeded
                elif defense.outcome.succeeded and parrying_hand not in (
                    None,
                    "left-hand",
                    "right-hand",
                ):
                    assert parrying_hand is not None
                    state, encounter, effect_checks, effect_dice = armed_parry_injury(
                        play,
                        state,
                        encounter,
                        pending,
                        parrying_hand,
                        command.parry_mode_id if index == 0 else command.second_parry_mode_id,
                    )
                    actor = fighter(encounter, actor.actor_id)
                if not hit or defense.outcome is Outcome.CRITICAL_FAILURE:
                    break
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
    if (
        pending.action == "kick"
        and attack.outcome is Outcome.FAILURE
        and not near_miss
        and blocked is None
    ):
        balance = success_roll(
            BASIC,
            skill_value(play, state, actor.actor_id, "attribute:dx"),
            check_modifiers(state.resources, actor.actor_id, "dx"),
            rng=play.rng,
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
        maximum = critical in (6, 15)
        dice = () if maximum else tuple(play.rng.randbelow(6) + 1 for _ in range(expression.dice))
        bonus = striking_bonus(
            pending.skill,
            compiled.statistics.dx,
            skill_value(play, state, actor.actor_id, pending.skill),
        )
        basic = max(
            0,
            (6 * expression.dice if maximum else sum(dice))
            + expression.add
            + (-1 if pending.action == "punch" else 0)
            + bonus * expression.dice,
            # B365: Strong adds two damage or one per die, whichever is better.
        )
        if actor.maneuver_state.strong:
            basic = max(
                0,
                (6 * expression.dice if maximum else sum(dice))
                + expression.add
                + (-1 if pending.action == "punch" else 0)
                + bonus * expression.dice
                + max(2, expression.dice),
            )
        basic *= 3 if critical in (3, 18) else 2 if critical in (5, 16) else 1
        resistance = armor_dr(play, state, target.actor_id, resolved_location)
        state, encounter, injury = hurt(
            play,
            state,
            encounter,
            target.actor_id,
            pending.id,
            basic,
            location=resolved_location,
            critical=critical,
        )
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
    if critical == 12:
        state, encounter = drop_held(state, encounter, target.actor_id)
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
            effect_dice=effect_dice,
            effect_checks=effect_checks,
            resolved_location=resolved_location if pending.action in ("punch", "kick") else None,
            defenses=tuple(
                (choice, selected_hand)
                for choice, (_, selected_hand) in zip(
                    (command.defense, command.second_defense), defenses, strict=False
                )
                if choice is not None
            ),
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
    critical: int = 0,
    damage_type: DamageType = "cr",
    ignore_dr: bool = False,
    armor_divisor: Decimal = Decimal(1),
    tight_beam: bool = False,
    pain_only: bool = False,
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
            damage_type=damage_type,
            location=location,
            armor_divisor=armor_divisor,
            tight_beam=tight_beam,
        ),
        ht=compiled.statistics.ht,
        dx=compiled.statistics.dx,
        rng=play.rng,
        system=True,
        held_item_ids=target.ready_item_ids,
        held_item_locations=target.hand_bindings,
        force_major_wound=critical in (7, 13, 14),
        double_shock=critical == 8,
        funny_bone=critical == 8,
        halve_dr="down" if critical in (4, 17) else None,
        ignore_dr=ignore_dr,
        pain_only=pain_only,
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
        play,
        state,
        encounter,
        actor_id,
        defended=False,
        injured=result.injury > 0 or (pain_only and result.penetration > 0),
    )
    return state, encounter, result.injury


def critical_miss(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    pending: PendingUnarmed,
    actor_id: str,
    table: tuple[int, ...],
    hand: str | None,
) -> tuple[PlayState, Encounter, tuple[CheckTrace, ...], tuple[int, ...], bool]:
    """B557 consequences that fit the current injury and tactical contracts.

    Remaining contextual outcomes retain their table roll and halt the transaction's
    continuation; a bare limb never selects the armed weapon-break/drop table.
    """
    if hand not in (None, "left-hand", "right-hand"):
        return state, encounter, (), (), False
    actor = fighter(encounter, actor_id)
    number = sum(table)
    checks: tuple[CheckTrace, ...] = ()
    dice: tuple[int, ...] = ()
    if number in (4, 5, 6, 16, 17):
        from wayfarer.simulation.gurps_equipment import MeleeMode
        from wayfarer.simulation.injury import DisableLocation, apply_location_effect

        if number in (5, 6, 16) and actor_id == pending.actor_id:
            opponent = fighter(encounter, pending.target_id)
            if any(
                m.damage.damage_type == "imp"
                for i in state.resources.items
                if i.id in opponent.ready_item_ids
                for e in catalog(play).entries
                if e.definition_id == i.definition_id
                for m in e.modes
                if isinstance(m, MeleeMode)
            ):
                return state, encounter, (), (), False

        kicking = actor_id == pending.actor_id and pending.action == "kick"
        selected_hand = hand
        if not kicking and selected_hand is None:
            if len(pending.hands) == 2:
                dice = (play.rng.randbelow(6) + 1,)
            selected_hand = pending.hands[1 if dice and dice[0] > 3 else 0]
        limb: HumanLocation = (
            ("left-leg" if pending.foot == "left-foot" else "right-leg")
            if kicking
            else ("left-arm" if selected_hand == "left-hand" else "right-arm")
        )
        if number in (5, 6, 16):
            compiled = build(play, state, actor_id)
            assert compiled.statistics is not None
            expression = compiled.statistics.thrust
            damage_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(expression.dice))
            damage = max(0, sum(damage_dice) + expression.add)
            state, encounter, _ = hurt(
                play,
                state,
                encounter,
                actor_id,
                pending.id + ":self-hit",
                damage // 2 if number == 6 else damage,
                location=limb,
            )
            return state, encounter, (), dice + damage_dice, True
        state, encounter, _ = hurt(
            play,
            state,
            encounter,
            actor_id,
            pending.id + ":strain-wound",
            1,
            location=limb,
            ignore_dr=True,
        )
        resources, _ = apply_location_effect(
            state.resources,
            DisableLocation.model_validate(
                {
                    "id": pending.id + ":strain",
                    "actor_id": actor_id,
                    "expected_revision": state.resources.revision,
                    "location": limb,
                    "duration_seconds": 1800,
                }
            ),
            system=True,
        )
        actor = fighter(encounter, actor_id)
        if kicking:
            actor = actor.model_copy(update={"posture": "prone"})
        return (
            state.model_copy(update={"resources": resources}),
            CombatEngine._replace(encounter, actor),
            (),
            dice,
            True,
        )
    fall = number == 8 or (number in (7, 14) and actor_id == pending.target_id)
    if number == 12:
        score = skill_value(play, state, actor_id, "attribute:dx")
        score -= 4 if actor_id == pending.actor_id and pending.action == "kick" else 0
        check = success_roll(
            BASIC, score, check_modifiers(state.resources, actor_id, "dx"), rng=play.rng
        )
        checks = (check,)
        fall = not check.outcome.succeeded
    elif number in (9, 10, 11):
        actor = actor.model_copy(update={"defense_penalty": actor.defense_penalty - 2})
    elif not fall:
        return state, encounter, (), (), False
    if fall:
        if actor.posture == "prone":
            dice = (play.rng.randbelow(6) + 1,)
            # Already-grounded subjects take general injury, bypassing armor.
            state, encounter, _ = hurt(
                play,
                state,
                encounter,
                actor_id,
                pending.id + ":fall",
                max(0, dice[0] - 3),
                ignore_dr=True,
            )
            actor = fighter(encounter, actor_id)
        else:
            actor = actor.model_copy(update={"posture": "prone"})
    return state, CombatEngine._replace(encounter, actor), checks, dice, True


def armed_parry_injury(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    pending: PendingUnarmed,
    item_id: str,
    mode_id: str | None,
) -> tuple[PlayState, Encounter, tuple[CheckTrace, ...], tuple[int, ...]]:
    """B376: a separate weapon-skill check, never a second defended attack."""
    from wayfarer.orchestration.gurps_melee import mode
    from wayfarer.simulation.gurps_equipment import MeleeMode

    weapon = mode(play, state, pending.target_id, item_id, mode_id)
    assert isinstance(weapon, MeleeMode)
    score = skill_value(play, state, pending.target_id, weapon.skill_id)
    score -= 4 if pending.skill in ("skill:judo", "skill:karate") else 0
    check = success_roll(
        BASIC,
        score,
        check_modifiers(state.resources, pending.target_id, "dx", defensive=True),
        rng=play.rng,
    )
    if not check.outcome.succeeded:
        return state, encounter, (check,), ()
    compiled = build(play, state, pending.target_id)
    assert compiled.statistics is not None
    expression = (
        compiled.statistics.swing if weapon.damage.basis == "swing" else compiled.statistics.thrust
    )
    dice = tuple(play.rng.randbelow(6) + 1 for _ in range(weapon.damage.dice or expression.dice))
    damage = max(
        0 if weapon.damage.damage_type == "cr" else 1,
        sum(dice) + weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add),
    )
    limb: HumanLocation = (
        ("left-leg" if pending.foot == "left-foot" else "right-leg")
        if pending.action == "kick"
        else ("left-arm" if pending.hands[0] == "left-hand" else "right-arm")
    )
    state, encounter, _ = hurt(
        play,
        state,
        encounter,
        pending.actor_id,
        pending.id + ":armed-parry",
        damage,
        location=limb,
        damage_type=weapon.damage.damage_type,
        armor_divisor=weapon.damage.armor_divisor,
        tight_beam=weapon.damage.tight_beam,
    )
    return state, encounter, (check,), dice


def drop_held(state: PlayState, encounter: Encounter, actor_id: str) -> tuple[PlayState, Encounter]:
    """B556 result 12 includes every held item, even through armor."""
    actor = fighter(encounter, actor_id)
    held = {i for i, _ in actor.hand_bindings}
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"ready": False, "equipped": False}) if i.id in held else i
                for i in state.resources.items
            )
        }
    )
    actor = actor.model_copy(
        update={
            "ready_item_ids": tuple(i for i in actor.ready_item_ids if i not in held),
            "hand_bindings": (),
        }
    )
    return state.model_copy(update={"resources": resources}), CombatEngine._replace(
        encounter, actor
    )


def control(
    play: PlayService, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> tuple[PlayState, Encounter, UnarmedTrace]:
    grip = next(g for g in encounter.grips if g.id == command.grip_id)
    actor, target = fighter(encounter, command.actor_id), fighter(encounter, command.target_id)
    checks: tuple[CheckTrace, ...] = ()
    won = True
    damage = injury = 0
    if command.action == "release":
        remaining = tuple(h for h in grip.hands if h not in command.hands) if command.hands else ()
        encounter = encounter.model_copy(
            update={
                "grips": tuple(
                    g.model_copy(update={"hands": remaining}) if g.id == grip.id else g
                    for g in encounter.grips
                    if g.id != grip.id or remaining
                )
            }
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
            BASIC,
            actor.actor_id,
            target.actor_id,
            first,
            second,
            first_modifiers=check_modifiers(state.resources, actor.actor_id, "st"),
            second_modifiers=check_modifiers(state.resources, target.actor_id, "st"),
            rng=play.rng,
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
                    int(v.value) + retching_penalty(state.resources, actor.actor_id)
                    for v in attacker.sheet.values
                    if v.target in {"skill:judo", "skill:wrestling"}
                ),
            )
        second = max(
            strength(play, state, target.actor_id, trained=False),
            compiled.statistics.ht + physical_traits(state.resources, target.actor_id).fitness,
        )
        won, checks, _ = contest(
            BASIC,
            actor.actor_id,
            target.actor_id,
            first,
            second,
            first_modifiers=check_modifiers(state.resources, actor.actor_id, "st"),
            second_modifiers=check_modifiers(state.resources, target.actor_id, "st"),
            rng=play.rng,
        )
        if won:
            damage = checks[0].margin - checks[1].margin
            target_hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
            assert target_hp.injury is not None
            pain = command.action == "lock_damage" and any(
                w.location == grip.location
                and w.kind == "crippled"
                and w.active(
                    now=state.resources.game_time, full_hp=target_hp.current == target_hp.maximum
                )
                for w in target_hp.injury.lasting_injuries
            )
            state, encounter, injury = hurt(
                play,
                state,
                encounter,
                target.actor_id,
                "control-damage:" + hashlib.sha256(command.id.encode()).hexdigest(),
                damage,
                location=grip.location,
                rigid_only=command.action == "lock_damage",
                pain_only=pain,
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
                    int(v.value) + retching_penalty(state.resources, actor_id)
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
            first_modifiers=check_modifiers(state.resources, actor.actor_id, "st"),
            second_modifiers=check_modifiers(state.resources, target.actor_id, "st"),
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
            first_modifiers=check_modifiers(state.resources, actor.actor_id, "st"),
            second_modifiers=check_modifiers(state.resources, target.actor_id, "st"),
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
