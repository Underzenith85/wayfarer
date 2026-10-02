"""Persist one inventory melee damage original before its canonical consequences."""

from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.simulation.abilities import interrupt_concentration
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.attack_roll import AttackRollChoice
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter, PendingDefense
from wayfarer.engine.simulation.combat.melee.damage_records import (
    MeleeDamageStage,
    PreparedMeleeDamage,
)
from wayfarer.engine.simulation.combat.melee.defense import exert_defense, validate_defense_choices
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.melee.resolution import (
    finish_melee_damage,
    refresh_melee_damage_target,
    resolve_melee,
)
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.ranged.damage_records import (
    PreparedRangedDamage,
    RangedDamageStage,
)
from wayfarer.engine.simulation.combat.ranged.resolution import (
    advance_ranged_damage,
    refresh_ranged_damage_target,
)
from wayfarer.engine.simulation.combat.ranged.resolution import (
    resolve as resolve_ranged,
)
from wayfarer.engine.simulation.combat.settlement import reconcile_equipment
from wayfarer.engine.simulation.combat.thrown.items import validate_catch
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.engine.simulation.magic.effects import require_not_dazed
from wayfarer.engine.simulation.magic.missile_damage_records import (
    MissileDamageStage,
    PreparedMissileDamage,
)
from wayfarer.engine.simulation.magic.missiles import (
    finish_missile_damage,
    refresh_missile_damage_target,
)
from wayfarer.engine.simulation.magic.missiles import resolve as resolve_missile
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.luck import LuckRoll
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, encounter_for
from wayfarer.orchestration.combat.defense import _failed_interposition, finish_inventory_defense
from wayfarer.orchestration.combat.encounters import _prepare_encounter
from wayfarer.orchestration.combat.preflight import _prepare_command
from wayfarer.orchestration.damage_settlement import settle_damage_response
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.owner_damage_luck import select_owner_damage
from wayfarer.orchestration.owner_damage_records import (
    ChooseOwnerDamage,
    OwnerDamageOutcome,
    PrepareOwnerDamage,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock
from wayfarer.orchestration.task_context import approved
from wayfarer.orchestration.task_records import TaskResult, TaskSnapshot, identity


def _refresh_target(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    pending: InventoryDamagePending,
) -> InventoryDamagePending:
    if encounter.pending_defense != pending.pending_attack:
        raise ConflictError("Captured inventory damage delivery identity changed")
    prepared = pending.preparation
    if isinstance(prepared, PreparedRangedDamage):
        prepared = refresh_ranged_damage_target(play.rules_context, state, encounter, prepared)
    elif isinstance(prepared, PreparedMissileDamage):
        prepared = prepared.model_copy(
            update={
                "inputs": refresh_missile_damage_target(
                    play.rules_context, state, encounter, prepared.inputs
                ),
            }
        )
    else:
        prepared = prepared.model_copy(
            update={
                "inputs": refresh_melee_damage_target(
                    play.rules_context, state, encounter, prepared.inputs
                ),
            }
        )
    return pending.model_copy(update={"preparation": prepared})


def _complete(
    play: PlayService,
    before: PlayState,
    updated: PlayState,
    encounter: Encounter,
    injury: InjuryTrace,
    response: ChooseDefense,
    selected_defense: Literal["none", "dodge", "parry", "block"],
    pending: PendingDefense,
    *,
    secret: bool,
    captured_end_build: ValidatedBuild | None = None,
) -> tuple[PlayState, OwnerDamageOutcome]:
    response = response.model_copy(update={"expected_revision": before.revision})
    step = finish_inventory_defense(
        updated,
        response,
        encounter,
        CombatContext(play, before),
        selected_defense=selected_defense,
        pending=pending,
        previous=encounter_for(before, encounter.id),
        injury=injury,
        captured_end_build=captured_end_build,
    )
    state, combat = settle_damage_response(play, before, step, response, secret=secret)
    return state, OwnerDamageOutcome(dice=injury.damage_dice, combat=combat)


def _resume(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    preparation: PreparedMeleeDamage | PreparedRangedDamage | PreparedMissileDamage,
    response: ChooseDefense,
    selected_defense: Literal["none", "dodge", "parry", "block"],
    dice: tuple[int, ...] | None,
    captured_attacker: ValidatedBuild,
) -> tuple[PlayState, OwnerDamageOutcome] | RangedDamageStage:
    if isinstance(preparation, PreparedMeleeDamage):
        updated, encounter, injury = finish_melee_damage(
            play.rules_context, state, encounter, preparation.inputs, selected_damage=dice
        )
        pending = preparation.inputs.pending
    elif isinstance(preparation, PreparedMissileDamage):
        updated, encounter, injury = finish_missile_damage(
            play.rules_context, state, encounter, preparation.inputs, selected_damage=dice
        )
        pending = preparation.inputs.pending
    else:
        resolved = advance_ranged_damage(
            play.rules_context,
            state,
            encounter,
            preparation.context,
            preparation.progress,
            selected_damage=dice,
            secret=preparation.secret,
        )
        if isinstance(resolved, RangedDamageStage):
            return resolved
        updated, encounter, injury = resolved
        pending = preparation.context.pending
    return _complete(
        play,
        state,
        updated,
        encounter,
        injury,
        response,
        selected_defense,
        pending,
        secret=preparation.secret,
        captured_end_build=captured_attacker,
    )


def _open_damage(
    state: PlayState,
    encounter: Encounter,
    preparation: PreparedMeleeDamage | PreparedRangedDamage | PreparedMissileDamage,
    response: ChooseDefense,
    selected: Literal["none", "dodge", "parry", "block"],
    command_id: str,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    captured_attacker: ValidatedBuild,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    state, encounter = reconcile_equipment(state, encounter)
    state = state.model_copy(
        update={
            "encounters": tuple(encounter if e.id == encounter.id else e for e in state.encounters)
        }
    )
    pending = (
        preparation.context.pending
        if isinstance(preparation, PreparedRangedDamage)
        else preparation.inputs.pending
    )
    identity_source = (
        response.id + ":hit:" + str(preparation.progress.index)
        if isinstance(preparation, PreparedRangedDamage)
        else response.id
    )
    opportunity = InventoryDamagePending(
        id=identity("owner-damage:", identity_source),
        actor_id=pending.attacker_id,
        encounter_id=encounter.id,
        opened_elapsed_microseconds=clock.elapsed_microseconds,
        preparation=preparation,
        response=response,
        selected_defense=selected,
        captured_attacker=captured_attacker,
    )
    if any(roll.id == opportunity.id for roll in saved.luck.rolls):
        raise ConflictError("Owner damage roll identity was already used")
    roll = LuckRoll(
        id=opportunity.id,
        actor_id=opportunity.actor_id,
        kind="damage",
        dice_count=preparation.dice_count,
        modifier=opportunity.modifier,
        original=preparation.original,
        secret=preparation.secret,
    )
    saved = saved.model_copy(
        update={
            "pending": opportunity,
            "luck": saved.luck.model_copy(
                update={
                    "revision": state.revision,
                    "game_time": state.resources.game_time,
                    "rolls": saved.luck.rolls + (roll,),
                    "pending_roll_id": opportunity.id,
                }
            ),
        }
    )
    return (
        state,
        saved,
        TaskResult(
            command_id=command_id,
            actor_id=opportunity.actor_id,
            status="pending",
            pending_id=opportunity.id,
            secret=preparation.secret,
            damage_json=opportunity.original_json,
        ),
    )


def _prepare_damage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    response: ChooseDefense,
    selected: Literal["none", "dodge", "parry", "block"],
    weapon: MeleeMode | RangedMode | None,
    *,
    secret: bool,
    selected_attack: AttackRollChoice | None,
) -> (
    MeleeDamageStage
    | RangedDamageStage
    | MissileDamageStage
    | tuple[PlayState, Encounter, InjuryTrace]
):
    if weapon is None:
        return resolve_missile(
            runtime,
            state,
            encounter,
            selected,
            response.item_id,
            response.second_defense if selected != "none" else None,
            response.second_item_id if selected != "none" else None,
            selected_attack=selected_attack.selected if selected_attack else None,
            prepare_damage=True,
            secret_damage=secret,
        )
    if isinstance(weapon, RangedMode):
        return resolve_ranged(
            runtime,
            state,
            encounter,
            weapon,
            selected,
            response.item_id,
            second_defense=response.second_defense if selected != "none" else None,
            second_item_id=response.second_item_id if selected != "none" else None,
            parry_mode_id=response.parry_mode_id if selected != "none" else None,
            second_parry_mode_id=response.second_parry_mode_id if selected != "none" else None,
            catch_thrown=response.catch_thrown,
            selected_attack=selected_attack.selected if selected_attack else None,
            prepare_damage=True,
            secret_damage=secret,
        )
    return resolve_melee(
        runtime,
        state,
        encounter,
        selected,
        response.item_id,
        second_defense=response.second_defense if selected != "none" else None,
        second_item_id=response.second_item_id if selected != "none" else None,
        parry_mode_id=response.parry_mode_id if selected != "none" else None,
        second_parry_mode_id=response.second_parry_mode_id if selected != "none" else None,
        catch_thrown=response.catch_thrown,
        selected_attack=selected_attack.selected if selected_attack else None,
        prepare_damage=True,
        secret_damage=secret,
    )


def open_inventory_damage(
    play: PlayService,
    state: PlayState,
    command: PrepareOwnerDamage,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    *,
    selected_attack: AttackRollChoice | None = None,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    response = command.response
    if not isinstance(response, ChooseDefense):
        raise ValidationError("Inventory damage requires the actual target defense")
    encounter = encounter_for(state, response.encounter_id)
    pending = encounter.pending_defense
    if (
        pending is None
        or pending.composed_attack_id
        or pending.shield_rush
        or encounter.pending_unarmed
    ):
        raise ValidationError("This owner damage route requires an inventory weapon attack")
    context = CombatContext(play, state, selected_attack=selected_attack)
    weapon = (
        None
        if pending.spell_cast_id
        else mode(
            context.attack_runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id
        )
    )
    captured_attacker = (
        selected_attack.captured_attacker
        if selected_attack is not None and selected_attack.captured_attacker is not None
        else approved(play, state, pending.attacker_id)
    )
    state = play.checkpoint(state)
    state, prepared_response, context = _prepare_command(state, response, context)
    assert isinstance(prepared_response, ChooseDefense)
    response = prepared_response
    encounter = _prepare_encounter(state, response, context)
    pending = encounter.pending_defense
    if (
        pending is None
        or pending.defender_id != response.actor_id
        or response.defense not in pending.allowed
    ):
        raise ValidationError("Defense is not available to this actor")
    if response.catch_thrown:
        validate_catch(context.attack_runtime, state, encounter, response)
    if response.defense != "none":
        require_not_dazed(state.resources, response.actor_id)
        state = state.model_copy(
            update={
                "resources": interrupt_concentration(
                    state.resources, response.actor_id, response.id, distraction=True
                )
            }
        )
    validate_defense_choices(
        context.attack_runtime,
        state,
        encounter,
        response.defense,
        response.item_id,
        response.second_defense,
        response.second_item_id,
        parry_mode_id=response.parry_mode_id,
        second_parry_mode_id=response.second_parry_mode_id,
        incoming_item_id=pending.weapon_id,
        incoming_mode_id=pending.mode_id,
    )
    state, encounter, selected = exert_defense(
        context.attack_runtime,
        state,
        encounter,
        response.actor_id,
        response.id,
        response.defense,
        response.item_id,
        parry_mode_id=response.parry_mode_id,
        incoming_item_id=pending.weapon_id,
        incoming_mode_id=pending.mode_id,
    )
    if pending.protected_defender_id and selected == "none":
        step = _failed_interposition(state, encounter, pending, encounter, context)
        state, combat = settle_damage_response(play, state, step, response, secret=command.secret)
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=pending.attacker_id,
                status="completed",
                secret=command.secret,
                damage_json=OwnerDamageOutcome(dice=(), combat=combat).model_dump_json(),
            ),
        )
    captured = _prepare_damage(
        context.attack_runtime,
        state,
        encounter,
        response,
        selected,
        weapon,
        secret=command.secret,
        selected_attack=selected_attack,
    )
    if isinstance(captured, tuple):
        updated, encounter, injury = captured
        state, outcome = _complete(
            play,
            state,
            updated,
            encounter,
            injury,
            response,
            selected,
            pending,
            secret=command.secret,
            captured_end_build=selected_attack.captured_attacker if selected_attack else None,
        )
    elif isinstance(captured, MeleeDamageStage) and not captured.preparation.rollable:
        completed = _resume(
            play,
            captured.state,
            captured.encounter,
            captured.preparation,
            response,
            selected,
            None,
            captured_attacker,
        )
        assert not isinstance(completed, RangedDamageStage)
        state, outcome = completed
    else:
        return _open_damage(
            captured.state,
            captured.encounter,
            captured.preparation,
            response,
            selected,
            command.id,
            saved,
            clock,
            captured_attacker,
        )
    return (
        state,
        saved,
        TaskResult(
            command_id=command.id,
            actor_id=pending.attacker_id,
            status="completed",
            secret=command.secret,
            damage_json=outcome.model_dump_json(),
        ),
    )


def choose_inventory_damage(
    play: PlayService,
    state: PlayState,
    command: ChooseOwnerDamage,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if not isinstance(pending, InventoryDamagePending) or (pending.id, pending.actor_id) != (
        command.pending_id,
        command.actor_id,
    ):
        raise ConflictError("Inventory damage is no longer this owner's immediate choice")
    encounter = encounter_for(state, pending.encounter_id)
    pending = _refresh_target(play, state, encounter, pending)
    saved, clock, receipt, dice = select_owner_damage(play, state, command, saved, clock, pending)
    resumed = _resume(
        play,
        state,
        encounter,
        pending.preparation,
        pending.response,
        pending.selected_defense,
        dice,
        pending.captured_attacker,
    )
    if isinstance(resumed, RangedDamageStage):
        state, saved, following = _open_damage(
            resumed.state,
            resumed.encounter,
            resumed.preparation,
            pending.response,
            pending.selected_defense,
            command.id,
            saved,
            clock,
            pending.captured_attacker,
        )
        return (
            state,
            saved,
            clock,
            following.model_copy(
                update={
                    "luck": receipt,
                    "damage_json": OwnerDamageOutcome(dice=dice).model_dump_json(),
                }
            ),
        )
    state, outcome = resumed
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
