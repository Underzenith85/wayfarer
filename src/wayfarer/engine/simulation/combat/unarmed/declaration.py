"""Declaring an unarmed action, and whether it is legal to declare."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.rules.tables.unarmed import UNARMED_SKILLS
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, fatigue_ready, movement
from wayfarer.engine.simulation.combat.encounter import (
    Combatant,
    CombatResult,
    Encounter,
    basic_distance,
    basic_visible,
    move_basic,
)
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuvers import WaitInterrupt, WaitTrigger
from wayfarer.engine.simulation.combat.objects.locations import from_behind
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.tactical import attack_geometry, occupants, pose, sight
from wayfarer.engine.simulation.combat.unarmed.fighters import (
    encumbrance_level,
    fighter,
    free_hands,
    skill_value,
)
from wayfarer.engine.simulation.combat.unarmed.records import require_basic
from wayfarer.engine.simulation.health.hit_locations import disabled, require_location
from wayfarer.engine.simulation.hex_geometry import movement as hex_movement
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def declare_unarmed_wait(
    runtime: RulesContext,
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
    require_basic(catalog(runtime).profile_id)
    if trigger.reaction_target_id is None or trigger.reaction_target_id == actor_id:
        raise ValidationError("An unarmed Wait reaction requires one declared foe")
    skills = UNARMED_SKILLS[declared.action]
    if declared.skill not in skills:
        raise ValidationError("Skill does not support this unarmed action")
    skill_value(runtime, state, actor_id, declared.skill)
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
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> None:
    require_basic(catalog(runtime).profile_id)
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
    validate_choke_hold(encounter, command, actor, target)
    if actor.actor_id == target.actor_id:
        raise ValidationError("Unarmed action requires another actor")
    for participant in (actor, target):
        hp = next(p for p in state.resources.pools if p.id == f"hp:{participant.actor_id}")
        if hp.injury is None or hp.injury.anatomy != "human":
            raise ValidationError("Unarmed combat requires explicit living-human anatomy")

    target_hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
    assert target_hp.injury is not None
    require_location(target_hp.injury, command.location)
    if command.action in ("grapple", "pin", "takedown", "break_free"):
        for participant in (actor, target):
            compiled = build(runtime, state, participant.actor_id)
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
    separation = (
        basic_distance(encounter, actor.actor_id, target.actor_id)
        if isinstance(encounter.spatial, BasicSpatialContext)
        else float(CombatEngine.distance(actor.position, target.position))
    )
    if command.enter_close_combat:
        if (
            command.action not in ("punch", "grapple", "arm_lock")
            or actor.grappled
            or any(g.holder_id == actor.actor_id for g in encounter.grips)
        ):
            raise ValidationError("Close-combat entry is unavailable")
        if separation != 1 or movement(runtime, state, actor.actor_id) == 0:
            raise ValidationError("Close-combat entry requires an adjacent target and movement")
        if actor.posture == "prone":
            raise ValidationError("Prone close-combat entry requires crawling integration")
    distance = 0 if command.enter_close_combat else separation

    if command.action in ("punch", "kick", "grapple", "arm_lock"):
        attack_geometry(
            encounter,
            actor,
            target,
            frozenset({0, 1})
            if command.action == "kick" or command.enter_close_combat
            else frozenset({0}),
            location=command.location,
            board=runtime.hex_map(encounter),
        )
        if command.enter_close_combat and encounter.spatial_kind == "hex":
            hex_movement(
                runtime.require_hex(encounter),
                pose(actor),
                (pose(target).position,),
                move=movement(runtime, state, actor.actor_id),
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
        skills = UNARMED_SKILLS[command.action]
        if command.skill not in skills:
            raise ValidationError("Skill does not support this unarmed action")
        skill_value(runtime, state, actor.actor_id, command.skill)
        if command.skill in ("skill:judo", "skill:karate"):
            encumbrance_level(runtime, state, actor.actor_id)
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
                or disabled(state.resources, actor.actor_id)
                & {"left-leg", "right-leg", "left-foot", "right-foot"}
                or any(
                    g.target_id == actor.actor_id and g.location in ("left-leg", "right-leg")
                    for g in encounter.grips
                )
            ):
                raise ValidationError("Kick requires two usable legs and standing posture")
        elif command.action == "arm_lock":
            grip = next((g for g in encounter.grips if g.id == command.grip_id), None)
            parry_route = command.grip_id is None
            if parry_route:
                if (
                    actor.unarmed_lock_opportunity
                    != (target.actor_id, command.skill, encounter.round)
                    or len(command.hands) != 2
                    or len(set(command.hands)) != 2
                    or not set(command.hands) <= set(free_hands(state, encounter, actor.actor_id))
                    or command.location not in ("left-arm", "right-arm")
                    or encounter.wait_interrupt is not None
                ):
                    raise ValidationError(
                        "Defensive arm lock requires two free hands and the first turn after a skilled parry"
                    )
                return
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
                or ((grip.arm_lock or grip.choke_hold) and set(command.hands) != set(grip.hands))
            )
        ):
            raise ValidationError(
                "Release must select held hands; an arm lock or Choke Hold requires both hands"
            )
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


def validate_choke_hold(
    encounter: Encounter,
    command: TakeUnarmedTurn,
    actor: Combatant,
    target: Combatant,
) -> None:
    if not command.choke_hold:
        return
    if (
        command.action != "grapple"
        or command.location != "neck"
        or command.skill not in ("skill:judo", "skill:wrestling")
        or set(command.hands) != {"left-hand", "right-hand"}
        or not command.enter_close_combat
        or encounter.spatial_kind != "hex"
        or not from_behind(actor, target)
    ):
        raise ValidationError(
            "Choke Hold requires two hands, Judo/Wrestling and explicit rear hex entry"
        )
    if encounter.wait_interrupt is not None or any(
        participant.maneuver_state.wait is not None for participant in encounter.participants
    ):
        raise ValidationError("Choke Hold during a Wait requires rear-entry interruption context")


def interrupt_wait(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> tuple[Encounter, CombatResult] | None:
    """B366: an unarmed action is an observable attack, so a declared Wait resolves first.

    Only turn-consuming actions trigger; releasing a grip and arm-lock damage are free.
    Close-combat entry is applied before the pause, exactly as an armed step is, and is
    stripped from the saved command so the resumed turn cannot move a second time.
    """
    engine = runtime.combat
    assert engine is not None
    if command.action in ("release", "lock_damage"):
        return None
    actor = fighter(encounter, command.actor_id)
    entered, pairs = actor, encounter.close_pairs
    if command.enter_close_combat:
        target = fighter(encounter, command.target_id)
        merged = set(pairs)
        if isinstance(encounter.spatial, BasicSpatialContext):
            merged.add(
                (
                    min(actor.actor_id, target.actor_id),
                    max(actor.actor_id, target.actor_id),
                )
            )
            encounter = move_basic(
                encounter,
                actor_id=actor.actor_id,
                reference_actor_id=target.actor_id,
                direction="approach",
                yards=1,
                command_id=command.id,
                revision=state.revision,
            )
        else:
            entered = actor.model_copy(update={"position": target.position})
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
        if encounter.spatial_kind == "hex":
            if not sight(moved, waiter, entered, board=runtime.hex_map(moved)):
                continue
        elif encounter.spatial_kind == "basic" and not basic_visible(
            encounter, waiter.actor_id, entered.actor_id
        ):
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
