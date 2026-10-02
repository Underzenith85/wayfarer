"""B66 worst opponent attack through the existing task CAS and combat reducer."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.attack_visibility import (
    SecretAttackSource,
    conceal_attack_source,
    secret_source,
)
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.traits.composed_phases import ComposedAttackChoice
from wayfarer.engine.simulation.traits.composed_records import ResistComposedAttack
from wayfarer.engine.simulation.traits.composed_resolution import resolve as resolve_composed
from wayfarer.engine.simulation.traits.luck import (
    LuckRoll,
)
from wayfarer.engine.simulation.traits.opponent_attack import (
    captured_choice,
    prepare_opponent_attack,
    validate_opponent_attack,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep, encounter_for
from wayfarer.orchestration.combat.steps import reduce_combat
from wayfarer.orchestration.damage_settlement import settle_damage_response
from wayfarer.orchestration.opponent_attack_luck import select_opponent_roll
from wayfarer.orchestration.opponent_attack_privacy import hide_secret_totals
from wayfarer.orchestration.opponent_attack_records import (
    BeginOpponentAttack,
    ChooseOpponentAttack,
    OpponentAttackPending,
)
from wayfarer.orchestration.owner_damage_records import OwnerDamageOutcome, PrepareOwnerDamage
from wayfarer.orchestration.owner_damage_tasks import open_owner_damage
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import RealPlayClock
from wayfarer.orchestration.task_records import TaskResult, TaskSnapshot, identity


def open_opponent_attack(
    play: PlayService,
    state: PlayState,
    command: BeginOpponentAttack,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    if saved.pending is not None:
        raise ConflictError("Another immediate Luck choice is pending")
    if (
        command.visibility == "public"
        and secret_source(state.resources, command.encounter_id, command.attack_id) is not None
    ):
        raise ValidationError("Recorded secret attack visibility cannot become public")
    state = play.checkpoint(state)
    preparation = prepare_opponent_attack(
        play.rules_context,
        state,
        encounter_for(state, command.encounter_id),
        owner_id=command.actor_id,
        attack_id=command.attack_id,
        secret=command.visibility == "secret",
        resist=command.resist,
    )
    if preparation.secret:
        state = state.model_copy(
            update={
                "resources": conceal_attack_source(
                    state.resources,
                    SecretAttackSource(
                        encounter_id=preparation.encounter_id,
                        attack_id=preparation.attack_id,
                        attacker_id=preparation.attacker_id,
                        target_id=preparation.owner_id,
                    ),
                    system=True,
                )
            }
        )
    pending = OpponentAttackPending(
        id=identity("opponent-attack:", command.id),
        actor_id=command.actor_id,
        opened_elapsed_microseconds=clock.elapsed_microseconds,
        preparation=preparation,
        prepare_owner_damage=command.prepare_owner_damage,
    )
    if any(roll.id == pending.id for roll in saved.luck.rolls):
        raise ConflictError("Opponent attack choice identity was already used")
    original = preparation.original
    roll = LuckRoll(
        id=pending.id,
        actor_id=preparation.attacker_id,
        kind="success",
        scope="attack",
        affected_actor_ids=(command.actor_id,),
        original=original.dice if original else None,
        secret=pending.secret,
        task_class="weapon",
        failed=bool(original and not original.outcome.succeeded),
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
            actor_id=command.actor_id,
            status="pending",
            pending_id=pending.id,
            check=original,
            secret=pending.secret,
        ),
    )


def choose_opponent_attack(
    play: PlayService,
    state: PlayState,
    command: ChooseOpponentAttack,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if not isinstance(pending, OpponentAttackPending) or (pending.id, pending.actor_id) != (
        command.pending_id,
        command.actor_id,
    ):
        raise ConflictError("Opponent attack is no longer the immediate choice for this owner")
    if command.choice == "cancel":
        if not pending.secret:
            raise ValidationError("An already rolled attack original cannot be cancelled")
        saved = saved.model_copy(
            update={
                "pending": None,
                "luck": saved.luck.model_copy(update={"pending_roll_id": None}),
            }
        )
        return (
            state,
            saved,
            clock,
            TaskResult(
                command_id=command.id, actor_id=command.actor_id, status="cancelled", secret=True
            ),
        )
    response = command.response
    assert response is not None
    if response.encounter_id != pending.preparation.encounter_id:
        raise ConflictError("Opponent attack response names another encounter")
    declared_resistance = pending.preparation.malediction
    if declared_resistance is not None:
        if not isinstance(response, ResistComposedAttack) or (
            response.resist,
            response.pending_id,
        ) != (declared_resistance.resist, pending.preparation.attack_id):
            raise ConflictError(
                "Malediction response differs from its original resistance declaration"
            )
    elif not isinstance(response, ChooseDefense):
        raise ValidationError("Ordinary attacks require a defense response")
    encounter = encounter_for(state, pending.preparation.encounter_id)
    validate_opponent_attack(play.rules_context, state, encounter, pending.preparation)
    saved, clock, original, selected, receipt = select_opponent_roll(
        play, state, command, saved, clock, pending
    )
    choice = captured_choice(pending.preparation, selected, original=original)
    if pending.prepare_owner_damage:
        state, saved, following = open_owner_damage(
            play,
            state,
            PrepareOwnerDamage(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                response=response,
                secret=pending.secret,
            ),
            saved,
            clock,
            selected_attack=choice,
        )
        combat_json = None
        if following.status == "completed" and following.damage_json is not None:
            damage = OwnerDamageOutcome.model_validate_json(following.damage_json)
            if damage.combat is not None:
                combat_json = damage.combat.model_dump_json()
        return (
            state,
            saved,
            clock,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="completed",
                check=selected,
                luck=receipt,
                combat_json=combat_json,
                secret=pending.secret,
            ),
        )
    if isinstance(response, ChooseDefense):
        state, combat = reduce_combat(
            state, response, CombatContext(play, state, selected_attack=choice)
        )
    else:
        if not isinstance(choice, ComposedAttackChoice):
            raise ValidationError("Resistance continuation requires its captured Malediction")
        resolved = resolve_composed(
            play.rules_context, state, encounter, response, selected_attack=choice
        )
        state, combat = settle_damage_response(
            play,
            state,
            CombatStep(
                resolved.state, resolved.encounter, resolved.state.resources, resolved.result
            ),
            response,
            secret=pending.secret,
        )

    if pending.secret:
        state = hide_secret_totals(state, encounter.id, command.id)
    state = play.checkpoint(state)
    return (
        state,
        saved,
        clock,
        TaskResult(
            command_id=command.id,
            actor_id=command.actor_id,
            status="completed",
            check=selected,
            luck=receipt,
            combat_json=combat.model_dump_json(),
            secret=pending.secret,
        ),
    )
