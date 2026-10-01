"""B216 Push owns the attack and defender response, with B378 displacement."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.statistics import damage as strength_damage
from wayfarer.engine.character.traits.mastery import trained_by_master
from wayfarer.engine.rules.checks import CheckTrace, Outcome, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.location import Hand
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, exertion, fatigue_ready, injury_turn
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.close_combat import (
    validate_defense as validate_close_defense,
)
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.displacement import (
    DisplacementResult,
    displace,
    potential_knockback,
    require_immovable,
)
from wayfarer.engine.simulation.combat.encounter import Encounter, basic_visible
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuver_transitions import distracted
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState, attack_modifier
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.tactical import (
    TacticalApproach,
    attack_approach,
    defense_adjustment,
    height_effect,
    pose,
)
from wayfarer.engine.simulation.combat.unarmed.attack import enter_close
from wayfarer.engine.simulation.combat.unarmed.declaration import validate_action
from wayfarer.engine.simulation.combat.unarmed.defense import parry_candidates, unarmed_defense
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter, free_hands, settle_control
from wayfarer.engine.simulation.combat.unarmed.injury import (
    armed_parry_injury,
    critical_miss,
    drop_held,
)
from wayfarer.engine.simulation.combat.unarmed.records import BASIC, PendingUnarmed
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fright_state import blocked as fright_blocked
from wayfarer.engine.simulation.hex_geometry import DIRECTIONS, Hex
from wayfarer.engine.simulation.magic.effects import require_not_dazed
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.skills.power_blow import power_blow_strength
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Id, Record


class PushCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    build_revision: str
    encounter_id: Id
    target_id: Id
    hands: tuple[Hand, ...] = Field(min_length=1, max_length=2)
    enter_close_combat: bool = False


class PushDefense(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    build_revision: str
    encounter_id: Id
    attack_command_id: Id
    defense: Literal["dodge", "parry", "block", "none"]
    item_id: Id | None = None
    mode_id: Id | None = None
    immovable_stance: bool = False


class PushPending(Record):
    attacker_id: Id
    defender_id: Id
    attacker_build_revision: str
    defender_build_revision: str
    encounter_digest: str
    at: int = Field(ge=0)
    strength: int = Field(ge=1)
    hands: tuple[Hand, ...]
    attack_origin: GridPoint | Hex | None = None
    attack_direction: tuple[int, int] | None = None
    attack: CheckTrace
    critical_table: tuple[int, ...] = ()


class PushResult(Record):
    command_id: Id
    attack_command_id: Id
    encounter_id: Id
    stage: Literal["defense-required", "missed", "resolved", "exertion-denied"]
    pending: PushPending | None = None
    attack: CheckTrace | None = None
    defense: CheckTrace | None = None
    critical_table: tuple[int, ...] = ()
    damage_dice: tuple[int, ...] = ()
    basic_damage: int = 0
    displacement: DisplacementResult | None = None
    effect_checks: tuple[CheckTrace, ...] = ()
    effect_dice: tuple[int, ...] = ()


def _event_id(command_id: str) -> str:
    return "push:" + hashlib.sha256(command_id.encode()).hexdigest()


def _history(state: PlayState) -> tuple[PushResult, ...]:
    return tuple(
        PushResult.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith("push:")
    )


def _replay(
    runtime: RulesContext,
    state: PlayState,
    command: PushCommand | PushDefense,
    authorized_actor_id: str,
) -> PushResult | None:
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Push command actor lacks authority")
    approved = build(runtime, state, command.actor_id)
    if approved.revision != command.build_revision:
        raise ValidationError("Push command build approval changed")
    receipt = next((r for r in state.resources.receipts if r.command_id == command.id), None)
    if receipt:
        if receipt.digest != hashlib.sha256(command.model_dump_json().encode()).hexdigest():
            raise ConflictError("Push command ID reused with another intent")
        result = next((r for r in _history(state) if r.command_id == command.id), None)
        if result is None:
            raise ValidationError("Push receipt has no stored outcome")
        return result
    if command.expected_revision != state.resources.revision:
        raise ConflictError("Push command revision conflict")
    return None


def _save(
    state: PlayState,
    encounter: Encounter,
    command: PushCommand | PushDefense,
    result: PushResult,
) -> tuple[PlayState, PushResult]:
    resources = state.resources.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "receipts": state.resources.receipts
            + (
                Receipt(
                    command_id=command.id,
                    digest=hashlib.sha256(command.model_dump_json().encode()).hexdigest(),
                ),
            ),
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=_event_id(command.id),
                    at=state.resources.game_time,
                    target_id=command.actor_id,
                    kind=result.model_dump_json(),
                ),
            ),
        }
    )
    return state.model_copy(
        update={
            "resources": resources,
            "encounters": tuple(encounter if e.id == encounter.id else e for e in state.encounters),
        }
    ), result


def _finish(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str, command_id: str
) -> tuple[PlayState, Encounter]:
    state = injury_turn(runtime, state, actor_id, command_id, start=False, do_nothing=False)
    actor = fighter(encounter, actor_id)
    encounter = CombatEngine._replace(
        encounter,
        actor.model_copy(update={"last_maneuver": "attack", "maneuver_state": ManeuverState()}),
    )
    encounter = settle_control(state, encounter)
    state = _sync_falls(state, encounter)
    return state, encounter if encounter.blocked_reason else CombatEngine._advance(encounter)


def _sync_falls(state: PlayState, encounter: Encounter) -> PlayState:
    fallen = {p.actor_id for p in encounter.participants if p.posture == "prone"}
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"injury": p.injury.model_copy(update={"prone": True})})
                        if p.injury is not None and p.id.removeprefix("hp:") in fallen
                        else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )


def _encounter(state: PlayState, encounter_id: str) -> Encounter:
    encounter = next((e for e in state.encounters if e.id == encounter_id), None)
    if encounter is None:
        raise ValidationError("Unknown Push encounter")
    return encounter


def _digest(encounter: Encounter) -> str:
    return hashlib.sha256(
        encounter.model_copy(update={"blocked_reason": None}).model_dump_json().encode()
    ).hexdigest()


def _declaration(
    runtime: RulesContext, state: PlayState, command: PushCommand, compiled: ValidatedBuild
) -> tuple[Encounter, TakeUnarmedTurn, int]:
    if not trained_by_master(compiled):
        raise ValidationError("Push requires Trained By A Master")
    level = next((int(v.value) for v in compiled.sheet.values if v.target == "skill:push"), None)
    if level is None or not any(p.definition_id == "skill:push" for p in compiled.purchases):
        raise ValidationError("Approved actor does not know Push")
    encounter = _encounter(state, command.encounter_id)
    assert compiled.statistics is not None
    if (
        power_blow_strength(
            state.resources,
            command.actor_id,
            compiled.revision,
            "unarmed:" + hashlib.sha256(command.id.encode()).hexdigest(),
            encounter.id,
            encounter.round,
            encounter.turn_index,
            compiled.statistics.st,
        )
        != compiled.statistics.st
    ):
        raise ValidationError(
            "Combining Power Blow with Push requires a supported strength contract"
        )
    if (
        encounter.status != "active"
        or encounter.current_actor_id != command.actor_id
        or encounter.blocked_reason
        or encounter.pending_defense
        or encounter.pending_unarmed
        or encounter.wait_interrupt
    ):
        raise ValidationError("Push requires the current uncommitted ordinary attack turn")
    if any(p.maneuver_state.wait is not None for p in encounter.participants):
        raise ValidationError("Push against an outstanding Wait requires interruption integration")
    actor, target = fighter(encounter, command.actor_id), fighter(encounter, command.target_id)
    build(runtime, state, command.target_id)  # Preflight both approvals before any random draw.
    if actor.unarmed_balance_lost or any(
        p.personal_flight or p.high_speed for p in (actor, target)
    ):
        raise ValidationError("Push requires a balanced, grounded ordinary combat turn")
    if any({g.holder_id, g.target_id} & {actor.actor_id, target.actor_id} for g in encounter.grips):
        raise ValidationError("Push displacement of an existing grapple requires adjudication")
    if isinstance(encounter.spatial, BasicSpatialContext) and not basic_visible(
        encounter, actor.actor_id, target.actor_id
    ):
        raise ValidationError("Push target is unavailable")
    if len(set(command.hands)) != len(command.hands) or not set(command.hands) <= set(
        free_hands(state, encounter, command.actor_id)
    ):
        raise ValidationError("Push requires the selected free usable hands")
    declaration = TakeUnarmedTurn(
        id=command.id,
        actor_id=command.actor_id,
        expected_revision=command.expected_revision,
        encounter_id=command.encounter_id,
        target_id=command.target_id,
        action="punch",
        hands=(command.hands[0],),
        enter_close_combat=command.enter_close_combat,
    )
    validate_action(runtime, state, encounter, declaration)
    require_not_dazed(state.resources, command.actor_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    assert hp.injury is not None
    if (
        hp.injury.stunned
        or actor.forced_do_nothing
        or fright_blocked(state.resources, command.actor_id)
    ):
        raise ValidationError("Stunned actor cannot Push")
    return encounter, declaration, level


def _attack_setup(encounter: Encounter, actor_id: str) -> Encounter:
    actor = fighter(encounter, actor_id)
    previous = actor.maneuver_state
    return CombatEngine._replace(
        encounter,
        actor.model_copy(
            update={
                "last_maneuver": "attack",
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
                ),
            }
        ),
    )


def declare_push(
    runtime: RulesContext, state: PlayState, command: PushCommand, *, authorized_actor_id: str
) -> tuple[PlayState, PushResult]:
    replay = _replay(runtime, state, command, authorized_actor_id)
    if replay is not None:
        return state, replay
    compiled = build(runtime, state, command.actor_id)
    encounter, declaration, level = _declaration(runtime, state, command, compiled)
    encounter = _attack_setup(encounter, command.actor_id)
    actor, target = fighter(encounter, command.actor_id), fighter(encounter, command.target_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    assert hp.injury is not None and compiled.statistics is not None
    value = level - hp.injury.shock + hp.injury.physical_traits.darkness(encounter.darkness_penalty)
    value -= 4 if actor.grappled else 0
    value -= 4 if actor.posture == "prone" else 2 if actor.posture == "kneeling" else 0
    if encounter.spatial_kind == "hex":
        value += height_effect(
            encounter, actor, target, reach=1, location="torso", board=runtime.hex_map(encounter)
        ).attack_modifier
    if target.unarmed_guard_dropped and actor.maneuver_state.evaluate_target_id == target.actor_id:
        value += actor.maneuver_state.evaluate_bonus
    value = attack_modifier(
        actor.maneuver_state,
        target.actor_id,
        value,
        check_adjustment=sum(
            m.value for m in check_modifiers(state.resources, actor.actor_id, "dx")
        ),
    )
    origin = actor.runtime_position
    direction: tuple[int, int] | None = None
    if origin is not None and origin == target.runtime_position:
        direction = (
            DIRECTIONS[actor.hex_facing]
            if actor.hex_facing is not None
            else {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}[actor.facing]
        )
    state = injury_turn(runtime, state, command.actor_id, command.id, start=True, do_nothing=False)
    state, allowed = exertion(runtime, state, command.actor_id, command.id)
    if not allowed:
        state, encounter = _finish(runtime, state, encounter, command.actor_id, command.id)
        return _save(
            state,
            encounter,
            command,
            PushResult(
                command_id=command.id,
                attack_command_id=command.id,
                encounter_id=encounter.id,
                stage="exertion-denied",
            ),
        )
    encounter = enter_close(runtime, state, encounter, declaration)
    attack = success_roll(
        BASIC, value, check_modifiers(state.resources, command.actor_id, "dx"), rng=runtime.rng
    )
    table = (
        draw_dice(runtime.rng, 3)
        if attack.outcome in (Outcome.CRITICAL_SUCCESS, Outcome.CRITICAL_FAILURE)
        else ()
    )
    effect_checks: tuple[CheckTrace, ...] = ()
    effect_dice: tuple[int, ...] = ()
    if not attack.outcome.succeeded:
        if attack.outcome is Outcome.CRITICAL_FAILURE:
            bare = PendingUnarmed(
                id=_event_id(command.id),
                actor_id=command.actor_id,
                target_id=command.target_id,
                action="punch",
                skill="attribute:dx",
                hands=command.hands,
                allowed=("dodge", "parry", "none"),
            )
            state, encounter, effect_checks, effect_dice, handled = critical_miss(
                runtime, state, encounter, bare, command.actor_id, table, None
            )
            if not handled:
                encounter = encounter.model_copy(
                    update={"blocked_reason": "Push critical miss requires adjudication"}
                )
        state, encounter = _finish(runtime, state, encounter, command.actor_id, command.id)
        return _save(
            state,
            encounter,
            command,
            PushResult(
                command_id=command.id,
                attack_command_id=command.id,
                encounter_id=encounter.id,
                stage="missed",
                effect_checks=effect_checks,
                effect_dice=effect_dice,
                attack=attack,
                critical_table=table,
            ),
        )
    pending = PushPending(
        defender_build_revision=build(runtime, state, command.target_id).revision,
        encounter_digest=_digest(encounter),
        at=state.resources.game_time,
        attacker_id=command.actor_id,
        defender_id=command.target_id,
        attacker_build_revision=compiled.revision,
        strength=max(compiled.statistics.st, level),
        hands=command.hands,
        attack_origin=origin,
        attack_direction=direction,
        attack=attack,
        critical_table=table,
    )
    encounter = encounter.model_copy(
        update={"blocked_reason": "Push defense required:" + _event_id(command.id)}
    )
    return _save(
        state,
        encounter,
        command,
        PushResult(
            command_id=command.id,
            attack_command_id=command.id,
            encounter_id=encounter.id,
            stage="defense-required",
            attack=attack,
            critical_table=table,
            pending=pending,
        ),
    )


def _pending(
    runtime: RulesContext, state: PlayState, command: PushDefense
) -> tuple[Encounter, PushPending]:
    records = [r for r in _history(state) if r.attack_command_id == command.attack_command_id]
    if not records or records[-1].stage != "defense-required" or records[-1].pending is None:
        raise ConflictError("Push attack is no longer awaiting defense")
    pending = records[-1].pending
    if pending.defender_id != command.actor_id or records[-1].encounter_id != command.encounter_id:
        raise AuthorizationError("Only the actual pushed defender may choose defense")
    encounter = _encounter(state, command.encounter_id)
    if encounter.blocked_reason != "Push defense required:" + _event_id(command.attack_command_id):
        raise ConflictError("Push encounter commitment changed")
    if (
        encounter.status != "active"
        or encounter.current_actor_id != pending.attacker_id
        or _digest(encounter) != pending.encounter_digest
        or pending.at != state.resources.game_time
    ):
        raise ConflictError("Push encounter or clock changed")
    if build(runtime, state, pending.attacker_id).revision != pending.attacker_build_revision:
        raise ConflictError("Push attacker approval changed")
    if build(runtime, state, pending.defender_id).revision != pending.defender_build_revision:
        raise ConflictError("Push defender approval changed")
    return encounter, pending


def _defense_approach(encounter: Encounter, pending: PushPending) -> TacticalApproach | None:
    if encounter.spatial_kind != "hex" or not isinstance(pending.attack_origin, Hex):
        return None
    # Entering the target's hex must not erase the direction from which this
    # particular attack approached. Height still uses the actual current poses.
    attacker = pose(fighter(encounter, pending.attacker_id)).model_copy(
        update={"position": pending.attack_origin}
    )
    return attack_approach(pose(fighter(encounter, pending.defender_id)), attacker)


def _defense_value(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    pending: PushPending,
    command: PushDefense,
) -> tuple[int | None, str | None, str | None]:
    defender = fighter(encounter, command.actor_id)
    setup = next(a for a in state.actors if a.actor_id == command.actor_id)
    if command.defense != "none" and "unconscious" in setup.conditions:
        raise ValidationError("Unconscious actor cannot defend")
    if command.defense != "none":
        require_not_dazed(state.resources, command.actor_id)
    if command.immovable_stance:
        require_immovable(runtime, state, encounter, command.actor_id)
    if command.defense == "block":
        validate_close_defense(defense=command.defense)
    defense_level, parrying = unarmed_defense(
        runtime,
        state,
        encounter,
        command.actor_id,
        command.defense,
        command.item_id,
        attacker_id=pending.attacker_id,
        mode_id=command.mode_id,
    )
    selected_item = command.item_id
    if defense_level is not None:
        defense_level += defense_adjustment(
            encounter,
            fighter(encounter, pending.attacker_id),
            defender,
            approach=_defense_approach(encounter, pending),
        )
    attacker = fighter(encounter, pending.attacker_id)
    if defense_level is not None and attacker.maneuver_state.feint_target_id == command.actor_id:
        defense_level -= attacker.maneuver_state.feint_penalty * (
            2 if defender.unarmed_guard_dropped else 1
        )
    return defense_level, parrying, selected_item


def defend_push(
    runtime: RulesContext, state: PlayState, command: PushDefense, *, authorized_actor_id: str
) -> tuple[PlayState, PushResult]:
    replay = _replay(runtime, state, command, authorized_actor_id)
    if replay is not None:
        return state, replay
    encounter, pending = _pending(runtime, state, command)
    defender = fighter(encounter, command.actor_id)
    defense_level, parrying, selected_item = _defense_value(
        runtime, state, encounter, pending, command
    )
    bare = PendingUnarmed(
        id=_event_id(command.attack_command_id),
        actor_id=pending.attacker_id,
        target_id=pending.defender_id,
        action="punch",
        skill="attribute:dx",
        hands=pending.hands,
        allowed=("dodge", "parry", "none"),
    )
    defense: CheckTrace | None = None
    effect_checks: tuple[CheckTrace, ...] = ()
    effect_dice: tuple[int, ...] = ()
    table = pending.critical_table
    hit = True
    encounter = encounter.model_copy(update={"blocked_reason": None})
    allowed = True
    defense_exerted = (
        defense_level is not None and pending.attack.outcome is not Outcome.CRITICAL_SUCCESS
    )
    if defense_exerted:
        state, allowed = exertion(runtime, state, command.actor_id, command.id)
    if defense_level is not None and pending.attack.outcome is not Outcome.CRITICAL_SUCCESS:
        if allowed:
            defense = success_roll(BASIC, defense_level, rng=runtime.rng)
            hit = not defense.outcome.succeeded
            defender = defender.model_copy(
                update={
                    "parries": defender.parries + (parrying,) if parrying else defender.parries,
                    "maneuver_state": defender.maneuver_state.model_copy(update={"defended": True}),
                }
            )
            if (
                defense.outcome.succeeded
                and parrying in ("left-hand", "right-hand")
                and len(free_hands(state, encounter, command.actor_id)) == 2
                and max(parry_candidates(runtime, state, encounter, command.actor_id))[1]
                == "skill:judo"
            ):
                next_round = encounter.round + int(
                    encounter.turn_order.index(command.actor_id) <= encounter.turn_index
                )
                defender = defender.model_copy(
                    update={
                        "unarmed_lock_opportunity": (pending.attacker_id, "skill:judo", next_round)
                    }
                )
            encounter = CombatEngine._replace(encounter, defender)
            encounter = distracted(
                runtime, state, encounter, command.actor_id, defended=True, injured=False
            )
            if defense.outcome is Outcome.CRITICAL_FAILURE and command.defense == "dodge":
                encounter = CombatEngine._replace(
                    encounter,
                    fighter(encounter, command.actor_id).model_copy(update={"posture": "prone"}),
                )
            elif defense.outcome in (Outcome.CRITICAL_SUCCESS, Outcome.CRITICAL_FAILURE):
                table = draw_dice(runtime.rng, 3)
                subject = pending.attacker_id if defense.outcome.succeeded else command.actor_id
                state, encounter, effect_checks, effect_dice, handled = critical_miss(
                    runtime,
                    state,
                    encounter,
                    bare,
                    subject,
                    table,
                    None if defense.outcome.succeeded else parrying or selected_item,
                    command.mode_id,
                )
                if not handled:
                    encounter = encounter.model_copy(
                        update={"blocked_reason": "Push critical defense requires adjudication"}
                    )
                    hit = False
            elif defense.outcome.succeeded and parrying not in (None, "left-hand", "right-hand"):
                assert parrying is not None
                state, encounter, effect_checks, effect_dice = armed_parry_injury(
                    runtime, state, encounter, bare, parrying, command.mode_id
                )
    dice: tuple[int, ...] = ()
    basic = 0
    displacement: DisplacementResult | None = None
    if hit:
        _, expression = strength_damage(BASIC, pending.strength)
        critical = sum(pending.critical_table)
        maximum = critical in (6, 15)
        dice = () if maximum else draw_dice(runtime.rng, expression.dice)
        basic = (
            max(
                0,
                (expression.dice * 6 if maximum else sum(dice))
                + expression.add
                - (expression.dice if len(pending.hands) == 1 else 0),
            )
            * 2
        )
        basic *= 3 if critical in (3, 18) else 2 if critical in (5, 16) else 1
        if (
            command.immovable_stance
            and not defense_exerted
            and potential_knockback(runtime, state, command.actor_id, basic) > 0
        ):
            state, allowed = exertion(runtime, state, command.actor_id, command.id)
        hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
        assert hp.injury is not None
        stance = (
            command.immovable_stance
            and allowed
            and not hp.injury.incapacitated
            and fatigue_ready(state, command.actor_id)
        )
        if critical == 12:
            state, encounter = drop_held(state, encounter, command.actor_id)
        state, encounter, displacement = displace(
            runtime,
            state,
            encounter,
            source_id=pending.attacker_id,
            target_id=pending.defender_id,
            basic_damage=basic,
            use_immovable=stance,
            attack_origin=pending.attack_origin,
            attack_direction=pending.attack_direction,
        )
    state, encounter = _finish(runtime, state, encounter, pending.attacker_id, command.id)
    return _save(
        state,
        encounter,
        command,
        PushResult(
            command_id=command.id,
            attack_command_id=command.attack_command_id,
            encounter_id=command.encounter_id,
            stage="resolved",
            attack=pending.attack,
            defense=defense,
            critical_table=table,
            damage_dice=dice,
            basic_damage=basic,
            displacement=displacement,
            effect_checks=effect_checks,
            effect_dice=effect_dice,
        ),
    )
