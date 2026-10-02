"""B66 owner damage selects dice before canonical armor, injury and resource commit."""

from dataclasses import replace

from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.attack_roll import AttackRollChoice
from wayfarer.engine.simulation.combat.attack_visibility import pending_secret_source
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.settlement import reconcile_equipment
from wayfarer.engine.simulation.magic.area_fire import armor
from wayfarer.engine.simulation.traits.attack_defense import (
    PreparedOwnerDamage,
    prepare_owner_damage,
)
from wayfarer.engine.simulation.traits.composed_phases import ComposedAttackChoice, ComposedDelivery
from wayfarer.engine.simulation.traits.composed_resolution import (
    ResolvedAttack,
    finish_delivery,
    owner_damage_arguments,
    prepare_delivery,
)
from wayfarer.engine.simulation.traits.composed_sources import pending_binding, raw_build
from wayfarer.engine.simulation.traits.luck import LuckRoll
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep, encounter_for
from wayfarer.orchestration.combat.encounters import _prepare_encounter
from wayfarer.orchestration.combat.preflight import _prepare_command
from wayfarer.orchestration.damage_settlement import settle_damage_response
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.inventory_damage_tasks import (
    choose_inventory_damage,
    open_inventory_damage,
)
from wayfarer.orchestration.owner_damage_luck import select_owner_damage
from wayfarer.orchestration.owner_damage_records import (
    ChooseOwnerDamage,
    OwnerDamageOutcome,
    OwnerDamagePending,
    PrepareOwnerDamage,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock
from wayfarer.orchestration.task_records import TaskResult, TaskSnapshot, identity


def _finish(
    play: PlayService,
    before: PlayState,
    resolved: ResolvedAttack,
    delivery: ComposedDelivery,
    *,
    secret: bool = False,
) -> tuple[PlayState, OwnerDamageOutcome]:
    step = CombatStep(resolved.state, resolved.encounter, resolved.state.resources, resolved.result)
    state, combat = settle_damage_response(play, before, step, delivery.command, secret=secret)
    return state, OwnerDamageOutcome(
        dice=resolved.outcome.damage_dice, combat=combat, attack=resolved.outcome
    )


def _resume(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    delivery: ComposedDelivery,
    preparation: PreparedOwnerDamage,
    selected: tuple[int, ...] | None,
) -> tuple[PlayState, OwnerDamageOutcome]:
    resolved = finish_delivery(
        play.rules_context,
        state,
        encounter,
        delivery,
        prepared_damage=preparation,
        selected_damage=selected,
    )
    return _finish(play, state, resolved, delivery, secret=preparation.secret)


def open_owner_damage(
    play: PlayService,
    state: PlayState,
    command: PrepareOwnerDamage,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    *,
    selected_attack: AttackRollChoice | None = None,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    if saved.pending is not None:
        raise ConflictError("Another immediate Luck choice is pending")
    response = command.response
    encounter = encounter_for(state, response.encounter_id)
    if pending_secret_source(state.resources, encounter) is not None:
        command = command.model_copy(update={"secret": True})
    if (
        encounter.pending_defense is not None
        and encounter.pending_defense.composed_attack_id is None
    ):
        return open_inventory_damage(
            play, state, command, saved, clock, selected_attack=selected_attack
        )
    if selected_attack is not None and not isinstance(selected_attack, ComposedAttackChoice):
        raise ValidationError("A composed damage continuation requires its own captured source")
    state = play.checkpoint(state)
    if isinstance(response, ChooseDefense):
        state, prepared_response, context = _prepare_command(
            state, response, CombatContext(play, state, selected_attack=selected_attack)
        )
        assert isinstance(prepared_response, ChooseDefense)
        response = prepared_response
        encounter = _prepare_encounter(state, response, context)
    else:
        encounter = encounter_for(state, response.encounter_id)
    state, encounter, delivery = prepare_delivery(
        play.rules_context, state, encounter, response, selected_attack=selected_attack
    )
    # Defense exertion refreshes ready equipment; persist its canonical ordering
    # without settling turns or drawing anything after the damage original.
    state, encounter = reconcile_equipment(state, encounter)
    state = state.model_copy(
        update={
            "encounters": tuple(encounter if e.id == encounter.id else e for e in state.encounters)
        }
    )
    args = owner_damage_arguments(play.rules_context, state, encounter, delivery)
    preparation = prepare_owner_damage(
        state.resources,
        state.world,
        args.command,
        args.attacker_build,
        args.target_build,
        play.engine.reviewer.compiler.definitions,
        args.channels,
        target_ht=args.target_ht,
        rng=play.rng,
        authorized_actor_id=args.command.actor_id,
        system=True,
        consequences=args.consequences,
        secret=command.secret,
    )
    if not preparation.rollable:
        state, outcome = _resume(play, state, encounter, delivery, preparation, None)
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=preparation.actor_id,
                status="completed",
                secret=command.secret,
                damage_json=outcome.model_dump_json(),
            ),
        )
    pending = OwnerDamagePending(
        id=identity("owner-damage:", command.id),
        actor_id=preparation.actor_id,
        encounter_id=encounter.id,
        opened_elapsed_microseconds=clock.elapsed_microseconds,
        preparation=preparation,
        continuation=delivery,
    )
    if any(roll.id == pending.id for roll in saved.luck.rolls):
        raise ConflictError("Owner damage roll identity was already used")
    roll = LuckRoll(
        id=pending.id,
        actor_id=pending.actor_id,
        kind="damage",
        dice_count=preparation.dice_count,
        original=preparation.original,
        secret=command.secret,
    )
    saved = saved.model_copy(
        update={
            "pending": pending,
            "luck": saved.luck.model_copy(
                update={
                    "revision": state.revision,
                    "game_time": state.resources.game_time,
                    "rolls": saved.luck.rolls + (roll,),
                    "pending_roll_id": pending.id,
                }
            ),
        }
    )
    return (
        state,
        saved,
        TaskResult(
            command_id=command.id,
            actor_id=pending.actor_id,
            status="pending",
            pending_id=pending.id,
            secret=command.secret,
            damage_json=pending.original_json,
        ),
    )


def choose_owner_damage(
    play: PlayService,
    state: PlayState,
    command: ChooseOwnerDamage,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if isinstance(pending, InventoryDamagePending):
        return choose_inventory_damage(play, state, command, saved, clock)
    if not isinstance(pending, OwnerDamagePending) or (
        pending.id != command.pending_id or pending.actor_id != command.actor_id
    ):
        raise ConflictError("Owner damage is no longer the immediate pending choice")
    encounter = encounter_for(state, pending.encounter_id)
    captured = pending.continuation
    defense = encounter.pending_defense
    if defense is None or pending_binding(state.resources, encounter, defense) != captured.binding:
        raise ConflictError("Owner damage continuation no longer matches its delivered attack")
    target = raw_build(play.rules_context, state, captured.binding.target_id)
    resistance = attack_defense_traits(
        target, play.engine.reviewer.compiler.definitions
    ).damage_resistance() + armor(play.rules_context, state, captured.binding.target_id)
    captured = captured.model_copy(
        update={
            "current": replace(captured.current, target=target, resistance=resistance),
            "hp_before": next(
                p.current
                for p in state.resources.pools
                if p.id == "hp:" + captured.binding.target_id
            ),
        }
    )
    arguments = owner_damage_arguments(play.rules_context, state, encounter, captured)
    preparation = pending.preparation.model_copy(
        update={
            "target_revision": target.revision,
            "target_ht": arguments.target_ht,
            "consequences": arguments.consequences,
        }
    )
    saved, clock, receipt, selected = select_owner_damage(
        play, state, command, saved, clock, pending
    )
    state, outcome = _resume(play, state, encounter, captured, preparation, selected)
    return (
        state,
        saved,
        clock,
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="completed",
            luck=receipt,
            secret=pending.secret,
            damage_json=outcome.model_dump_json(),
        ),
    )
