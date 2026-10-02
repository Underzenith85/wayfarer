"""B66 choices over actual ordinary and B346 task consequences, on one CAS."""

from __future__ import annotations

import json

from pydantic import TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import CheckTrace, RandomSource, evaluate_success
from wayfarer.engine.rules.types.recovery import interrupt_tasks
from wayfarer.engine.simulation.abilities import interrupt_concentration
from wayfarer.engine.simulation.action_engine.calendar import require_action_time
from wayfarer.engine.simulation.action_engine.engine import TaskCheckPreparation
from wayfarer.engine.simulation.actions import Inspect, PlayState, Social
from wayfarer.engine.simulation.actors import exertion
from wayfarer.engine.simulation.campaign._task_phases import (
    LongTaskPreparation,
    ensure_long_task_available,
    prepare_long_task,
    roll_long_task_check,
    select_long_task_check,
)
from wayfarer.engine.simulation.campaign.activities import PerformActivity
from wayfarer.engine.simulation.events import action_result
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.traits.luck import LuckCommand, LuckRoll, apply_luck, luck_cooldown
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.entropy import current_command_instant
from wayfarer.orchestration.membership import member_for, require_control
from wayfarer.orchestration.pipeline import CommandPlan, Control, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.real_play_clock import (
    RealPlayClock,
    settle_real_play,
    spend_real_play_cooldown,
)
from wayfarer.orchestration.task_context import (
    activity_actor,
    approved,
    bind,
    binding,
    overtime_modifiers,
    ready,
    require_noncinematic,
    require_work_context,
    supervision_skill,
    task_modifiers,
)
from wayfarer.orchestration.task_records import (
    ADAPTER,
    CLOCK_PREFIX,
    RESULT_PREFIX,
    STATE_PREFIX,
    BeginTaskCheck,
    BeginTaskWork,
    BindLongTask,
    ChooseTaskCheck,
    SetRealPlayClock,
    TaskCommand,
    TaskPending,
    TaskResult,
    TaskSnapshot,
    append_record,
    identity,
    snapshot,
)

PREPARATION: TypeAdapter[TaskCheckPreparation] = TypeAdapter(TaskCheckPreparation)


def real_play_clock(state: PlayState) -> RealPlayClock:
    event = next(
        (event for event in reversed(state.resources.events) if event.id.startswith(CLOCK_PREFIX)),
        None,
    )
    return RealPlayClock() if event is None else RealPlayClock.model_validate_json(event.kind)


def _controls(
    play: PlayService, state: PlayState, command: TaskCommand, principal: str
) -> tuple[Control, ...]:
    member = member_for(state, principal)
    director = isinstance(command, (BindLongTask, SetRealPlayClock)) or (
        isinstance(command, BeginTaskCheck) and command.secret
    )
    if member.role == "gm" or director:
        return Seats(state), Trusted(play.engine.reviewer.gm_ids)
    actor_ids: tuple[str, ...] = (command.actor_id,)
    if isinstance(command, BeginTaskWork) and command.supervisor_actor_id is not None:
        actor_ids += (command.supervisor_actor_id,)
    return tuple(Controls(member, actor_id) for actor_id in actor_ids)


def _visible(play: PlayService, state: PlayState, result: TaskResult, principal: str) -> TaskResult:
    member = member_for(state, principal)
    if member.role == "gm":
        Trusted(play.engine.reviewer.gm_ids)(principal)
        return result
    require_control(member, result.actor_id, state)
    if result.secret:
        return result.model_copy(
            update={
                "check": None,
                "luck": None,
                "action": None,
                "activity": None,
                "pending_id": None,
            }
        )
    return result


def _opened(state: PlayState, saved: TaskSnapshot, pending: TaskPending) -> TaskSnapshot:
    if saved.pending is not None:
        raise ConflictError("Another task roll is awaiting its immediate decision")
    if any(roll.id == pending.id for roll in saved.luck.rolls):
        raise ConflictError("Task roll identity was already used")
    roll = LuckRoll(
        id=pending.id,
        actor_id=pending.actor_id,
        original=pending.original.dice,
        secret=pending.secret,
        failed=not pending.original.outcome.succeeded,
        task_class="social"
        if isinstance(pending.ordinary, Social)
        else "job"
        if pending.ordinary is None
        else "other",
    )
    return saved.model_copy(
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


def _rescore(original: CheckTrace, dice: tuple[int, int, int]) -> CheckTrace:
    return evaluate_success(
        original.base_target,
        original.modifiers,
        dice,
        rules_package=original.rules_package,
        rules_version=original.rules_version,
        rule_id=original.rule_id,
    )


def _advance(
    play: PlayService, state: PlayState, actor_id: str, command_id: str, seconds: int
) -> PlayState:
    require_action_time(state, actor_id, seconds)
    resources = interrupt_concentration(state.resources, actor_id, command_id)
    resources = resources.model_copy(
        update={
            "recovery_tasks": interrupt_tasks(
                resources.recovery_tasks, frozenset({actor_id}), resources.game_time
            )
        }
    )
    state = state.model_copy(update={"resources": resources})
    state = play.advance_clock(
        state,
        Advance(
            id=identity("task-time:", command_id),
            actor_id=actor_id,
            expected_revision=state.resources.revision,
            to=state.resources.game_time + seconds,
        ),
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"revision": state.revision})}
    )
    if state.party.groups:
        state = state.model_copy(
            update={
                "party": state.party.model_copy(
                    update={
                        "groups": tuple(
                            group.model_copy(update={"ready_through": state.resources.game_time})
                            for group in state.party.groups
                        ),
                    }
                )
            }
        )
    return state


def _begin_check(
    play: PlayService,
    state: PlayState,
    command: BeginTaskCheck,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    rule = next((rule for rule in play.engine.rules.checks if rule.id == command.check_id), None)
    if rule is None:
        raise ValidationError("Unknown authored ordinary task check")
    require_noncinematic(play, rule.definition_id)
    ready(play, state, command.actor_id)
    ordinary = (Inspect if rule.action == "inspect" else Social)(
        id=command.id,
        actor_id=command.actor_id,
        expected_revision=state.revision,
        target_id=rule.target_id,
    )
    play.engine.prepare_task_check(state, ordinary)
    if isinstance(ordinary, Inspect):
        state, allowed = exertion(
            play.rules_context, state, command.actor_id, identity("task-exertion:", command.id)
        )
        state = state.model_copy(
            update={"resources": state.resources.model_copy(update={"revision": state.revision})}
        )
        if not allowed:
            state = play.checkpoint(state)
            return (
                state,
                saved,
                TaskResult(
                    command_id=command.id,
                    actor_id=command.actor_id,
                    status="interrupted",
                    reason="Task actor could not continue exertion",
                    secret=command.secret,
                ),
            )
    cost = play.engine.rules.fatigue_cost
    if cost:
        build = play.rules_context.approved_build(state, command.actor_id)
        assert build.statistics is not None
        resources, _ = apply_fatigue(
            state.resources,
            FatigueCost(
                id=identity("task-cost:", command.id),
                actor_id=command.actor_id,
                expected_revision=state.resources.revision,
                amount=cost,
            ),
            ht=build.statistics.ht,
            rng=play.rng,
            system=True,
        )
        state = state.model_copy(
            update={"resources": resources.model_copy(update={"revision": state.revision})}
        )
    state = _advance(play, state, command.actor_id, command.id, rule.duration)
    # Clock consequences, including other actors' rolls, precede this original.
    state = play.checkpoint(state)
    ordinary = ordinary.model_copy(update={"expected_revision": state.revision})
    try:
        ready(play, state, command.actor_id)
        prepared = play.engine.prepare_task_check(state, ordinary, fatigue_already_paid=True)
    except ValidationError, ConflictError:
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="interrupted",
                reason="Task context changed during its elapsed time",
                secret=command.secret,
            ),
        )
    original = prepared.check(rng=play.rng)
    pending = TaskPending(
        id=identity("task-check:", command.id),
        actor_id=command.actor_id,
        original=original,
        opened_elapsed_microseconds=clock.elapsed_microseconds,
        secret=command.secret,
        ordinary=ordinary,
        preparation_json=PREPARATION.dump_json(prepared).decode(),
    )
    return (
        state,
        _opened(state, saved, pending),
        TaskResult(
            command_id=command.id,
            actor_id=pending.actor_id,
            status="pending",
            pending_id=pending.id,
            check=original,
            secret=pending.secret,
        ),
    )


def _work_pending(
    prepared: LongTaskPreparation, clock: RealPlayClock, rng: RandomSource
) -> TaskPending:
    original = roll_long_task_check(prepared, rng=rng)
    return TaskPending(
        id=prepared.check_id,
        actor_id=prepared.check_actor_id,
        original=original,
        opened_elapsed_microseconds=clock.elapsed_microseconds,
        preparation_json=prepared.model_dump_json(),
    )


def _refresh_work_phase(
    play: PlayService, state: PlayState, prepared: LongTaskPreparation
) -> LongTaskPreparation:
    """Refresh only an unresolved check; selected prerequisite traces stay fixed."""
    source = binding(state, prepared.rule.id)
    actor_id = prepared.check_actor_id
    supervisor = actor_id != prepared.actor.actor_id
    require_work_context(state, source, actor_id, equipment=not supervisor)
    target = source.supervisor_skill_id if supervisor else source.rule.target_id
    if target is None:
        raise ValidationError("Unbound supervisor skill")
    actor = activity_actor(play, state, actor_id, target)
    return prepared.model_copy(
        update={
            "supervisor" if supervisor else "actor": actor,
            "supervisor_modifiers" if supervisor else "worker_modifiers": task_modifiers(
                play, state, actor_id, target
            ),
            "supervisor_ht_modifiers" if supervisor else "worker_ht_modifiers": overtime_modifiers(
                play, state, actor_id
            ),
            "resource_revision": state.resources.revision,
        }
    )


def _begin_work(
    play: PlayService,
    state: PlayState,
    command: BeginTaskWork,
    saved: TaskSnapshot,
    clock: RealPlayClock,
) -> tuple[PlayState, TaskSnapshot, TaskResult]:
    source = binding(state, command.task_id)
    ready(play, state, command.actor_id)
    require_work_context(state, source, command.actor_id)
    started_at = state.resources.game_time
    ensure_long_task_available(
        state.resources, command.actor_id, started_at=started_at, seconds=command.seconds
    )
    participants: tuple[str, ...] = (command.actor_id,)
    if command.supervisor_actor_id is not None:
        if source.supervisor_skill_id is None or command.supervisor_actor_id == command.actor_id:
            raise ValidationError("Supervision requires its separate authored skilled actor")
        ready(play, state, command.supervisor_actor_id)
        require_work_context(state, source, command.supervisor_actor_id, equipment=False)
        ensure_long_task_available(
            state.resources,
            command.supervisor_actor_id,
            started_at=started_at,
            seconds=command.seconds,
        )
        participants += (command.supervisor_actor_id,)
    for actor_id in participants:
        if command.seconds > 28800 and not any(
            actor_id in member.actor_ids for member in state.members if member.role == "player"
        ):
            raise ValidationError("NPC overtime requires its separate prior Influence agreement")
        require_action_time(state, actor_id, command.seconds)
        resources = interrupt_concentration(state.resources, actor_id, command.id)
        state = state.model_copy(
            update={
                "resources": resources.model_copy(
                    update={
                        "recovery_tasks": interrupt_tasks(
                            resources.recovery_tasks, frozenset({actor_id}), resources.game_time
                        )
                    }
                )
            }
        )
    # All bound targets are checked before committing elapsed work.
    activity_actor(play, state, command.actor_id, source.rule.target_id)
    if command.supervisor_actor_id is not None and source.supervisor_skill_id is not None:
        activity_actor(play, state, command.supervisor_actor_id, source.supervisor_skill_id)
    for actor_id in participants:
        state, allowed = exertion(
            play.rules_context,
            state,
            actor_id,
            identity("task-exertion:", command.id + ":" + actor_id),
        )
        state = state.model_copy(
            update={"resources": state.resources.model_copy(update={"revision": state.revision})}
        )
        if not allowed:
            state = play.checkpoint(state)
            return (
                state,
                saved,
                TaskResult(
                    command_id=command.id,
                    actor_id=command.actor_id,
                    status="interrupted",
                    reason="A participant could not continue exertion",
                ),
            )
    state = _advance(play, state, command.actor_id, command.id, command.seconds)
    state = play.checkpoint(state)
    try:
        for actor_id in participants:
            ready(play, state, actor_id)
            require_work_context(state, source, actor_id, equipment=actor_id == command.actor_id)
        actor = activity_actor(play, state, command.actor_id, source.rule.target_id)
        skill = supervision_skill(source)
        supervisor = (
            activity_actor(play, state, command.supervisor_actor_id, skill)
            if command.supervisor_actor_id is not None and skill is not None
            else None
        )
    except ValidationError, ConflictError:
        return (
            state,
            saved,
            TaskResult(
                command_id=command.id,
                actor_id=command.actor_id,
                status="interrupted",
                reason="Worker or supervisor became unavailable during the shift",
            ),
        )
    prepared = prepare_long_task(
        state.resources,
        PerformActivity(
            id=command.id,
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            activity_id=source.rule.id,
            seconds=command.seconds,
        ),
        source.rule,
        actor,
        started_at=started_at,
        supervisor=supervisor,
        supervisor_target_id=skill if supervisor else None,
    )
    prepared = _refresh_work_phase(play, state, prepared)
    pending = _work_pending(prepared, clock, play.rng)
    return (
        state,
        _opened(state, saved, pending),
        TaskResult(
            command_id=command.id,
            actor_id=pending.actor_id,
            status="pending",
            pending_id=pending.id,
            check=pending.original,
        ),
    )


def _choose(
    play: PlayService,
    state: PlayState,
    command: ChooseTaskCheck,
    saved: TaskSnapshot,
    clock: RealPlayClock,
    principal: str,
) -> tuple[PlayState, TaskSnapshot, RealPlayClock, TaskResult]:
    pending = saved.pending
    if pending is None or pending.id != command.pending_id or pending.actor_id != command.actor_id:
        raise ConflictError("Task roll is no longer the immediate pending choice")
    if pending.secret and member_for(state, principal).role != "gm":
        raise AuthorizationError("Secret task decisions require trusted director authority")
    chosen = pending.original
    receipt = None
    luck = saved.luck.model_copy(
        update={"revision": state.revision, "game_time": state.resources.game_time}
    )
    if command.kind == "use-luck":
        build = approved(play, state, command.actor_id)
        definitions = play.engine.reviewer.compiler.definitions
        prior = next(
            (entry for entry in clock.cooldowns if entry.actor_id == command.actor_id), None
        )
        if (
            prior is not None
            and pending.opened_elapsed_microseconds < prior.available_at_microseconds
        ):
            raise ValidationError("Luck was cooling down when this original was rolled")
        clock = spend_real_play_cooldown(
            clock, actor_id=command.actor_id, seconds=luck_cooldown(build, definitions)
        )
        luck, receipt = apply_luck(
            luck,
            LuckCommand(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=state.revision,
                roll_id=pending.id,
            ),
            build,
            definitions,
            real_time=clock.elapsed_seconds,
            rng=play.rng,
            authorized_actor_id=command.actor_id,
            system=True,
        )
        dice = receipt.attempts[receipt.chosen_index]
        chosen = _rescore(pending.original, (dice[0], dice[1], dice[2]))
    else:
        luck = luck.model_copy(
            update={
                "pending_roll_id": None,
                "rolls": tuple(
                    roll.model_copy(
                        update={"chosen_dice": chosen.dice, "chosen_total": chosen.total}
                    )
                    if roll.id == pending.id
                    else roll
                    for roll in luck.rolls
                ),
            }
        )
    saved = saved.model_copy(update={"pending": None, "luck": luck})
    result = TaskResult(
        command_id=command.id,
        actor_id=command.actor_id,
        status="completed",
        check=chosen,
        luck=receipt,
        secret=pending.secret,
    )
    if pending.ordinary is not None:
        ordinary = pending.ordinary.model_copy(
            update={"id": command.id, "expected_revision": state.revision}
        )
        state, events = play.engine.resolve_prepared_task(
            state,
            ordinary,
            PREPARATION.validate_json(pending.preparation_json),
            chosen,
            fatigue_already_paid=True,
        )
        result = result.model_copy(update={"action": action_result(events)})
        if pending.secret:
            # Preserve actual knowledge/journal effects, but not a public roll projection.
            state = state.model_copy(update={"last_result": None})
        state = play.checkpoint(state)
    else:
        prepared = LongTaskPreparation.model_validate_json(pending.preparation_json).model_copy(
            update={"resource_revision": state.resources.revision}
        )
        resources, prepared, outcome = select_long_task_check(
            state.resources, prepared, chosen, rng=play.rng, system=True
        )
        state = state.model_copy(update={"resources": resources})
        state = state.model_copy(
            update={"resources": state.resources.model_copy(update={"revision": state.revision})}
        )
        state = play.checkpoint(state)
        if outcome is not None:
            result = result.model_copy(update={"activity": outcome})
        else:
            try:
                ready(play, state, prepared.check_actor_id)
                prepared = _refresh_work_phase(play, state, prepared)
            except ValidationError, ConflictError:
                result = result.model_copy(
                    update={
                        "status": "interrupted",
                        "reason": "Task actor cannot continue after the accepted prerequisite",
                    }
                )
            else:
                next_pending = _work_pending(prepared, clock, play.rng)
                saved = _opened(state, saved, next_pending)
                result = result.model_copy(
                    update={"status": "pending", "pending_id": next_pending.id}
                )
    return state, saved, clock, result


class TaskService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(self, cid: str, value: object, *, principal_id: str) -> TaskResult:
        try:
            command = ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid private task command") from exc
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        initial = play._load(campaign)
        payload = json.dumps(
            {
                "operation": "task-host",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            if before.revision != command.expected_revision or before.lifecycle != "active":
                raise ConflictError("Task operation requires the current active campaign revision")
            saved = snapshot(before)
            clock = settle_real_play(real_play_clock(before), current_command_instant())
            state = before
            if isinstance(command, SetRealPlayClock):
                clock = settle_real_play(clock, current_command_instant(), running=command.running)
                result = TaskResult(
                    command_id=command.id, actor_id=command.actor_id, status="clock"
                )
            elif isinstance(command, BindLongTask):
                state = bind(play, state, command, principal_id)
                result = TaskResult(
                    command_id=command.id, actor_id=command.actor_id, status="bound"
                )
            elif isinstance(command, BeginTaskCheck):
                state, saved, result = _begin_check(play, state, command, saved, clock)
            elif isinstance(command, BeginTaskWork):
                state, saved, result = _begin_work(play, state, command, saved, clock)
            else:
                state, saved, clock, result = _choose(
                    play, state, command, saved, clock, principal_id
                )
            revision = before.revision + 1
            state = state.model_copy(
                update={
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                }
            )
            saved = saved.model_copy(
                update={
                    "luck": saved.luck.model_copy(
                        update={"revision": revision, "game_time": state.resources.game_time}
                    )
                }
            )
            state = append_record(state, STATE_PREFIX, command.id, command.actor_id, saved)
            state = append_record(state, CLOCK_PREFIX, command.id, command.actor_id, clock)
            state = append_record(state, RESULT_PREFIX, command.id, command.actor_id, result)
            # No checkpoint may roll after a newly opened original.
            play.commit(campaign, state)
            return CommandReceipt(action="resource", outcome="task:" + command.kind)

        async def outcome(campaign: Campaign) -> TaskResult:
            state = play._load(campaign)
            event = next(
                (
                    event
                    for event in state.resources.events
                    if event.id == identity(RESULT_PREFIX, command.id)
                ),
                None,
            )
            if event is None:
                raise ValidationError("Missing committed task result")
            result = TaskResult.model_validate_json(event.kind)
            # This result belongs to the submitted command. A supervisor's next
            # original remains private until that supervisor or GM reads it.
            if result.actor_id != command.actor_id and member_for(state, principal_id).role != "gm":
                return result.model_copy(update={"check": None, "luck": None})
            return _visible(play, state, result, principal_id)

        plan = CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=_controls(play, initial, command, principal_id),
            rng=play.rng,
            pending_task_id=command.pending_id if isinstance(command, ChooseTaskCheck) else None,
            task_clock_only=isinstance(command, SetRealPlayClock),
        )
        return await submit(play, cid, plan, principal_id=principal_id)

    async def pending(self, cid: str, *, principal_id: str) -> TaskResult | None:
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        state = play._load(campaign)
        member_for(state, principal_id)
        pending = snapshot(state).pending
        if pending is None:
            return None
        return _visible(
            play,
            state,
            TaskResult(
                command_id=pending.id,
                actor_id=pending.actor_id,
                status="pending",
                pending_id=pending.id,
                check=pending.original,
                secret=pending.secret,
            ),
            principal_id,
        )
