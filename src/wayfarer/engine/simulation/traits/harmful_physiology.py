"""B130/B161 transitions driven by persisted facts and current approved purchases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.resources import Advance, ResourceEvent, ResourceState
from wayfarer.engine.simulation.traits.harmful_physiology_state import (
    PREFIX,
    AdvancePhysiology,
    DeclarePhysiologyCalendar,
    HarmfulCommand,
    HarmfulCondition,
    HarmfulKind,
    HarmfulReceipt,
    ObservePhysiology,
    ReconcilePhysiology,
    SettlePhysiology,
    calendar,
    conditions,
    living,
)
from wayfarer.engine.simulation.traits.physiology import apply_physiology_interval, harmful_timing
from wayfarer.engine.simulation.traits.physiology_types import (
    PhysiologyCommand,
    PhysiologyInterval,
    PhysiologyOutcome,
)
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


@dataclass(frozen=True)
class HarmfulContext:
    builds: Mapping[str, ValidatedBuild]
    definitions: Mapping[str, RuleDefinition]
    engine: ResourceEngine
    rng: RandomSource
    transformation_actor_ids: frozenset[str] = frozenset()


def require_supported_actor(context: HarmfulContext, actor_id: str) -> None:
    if actor_id in context.transformation_actor_ids:
        raise ValidationError("Harmful physiology and transformations require supported ordering")


def purchase(context: HarmfulContext, actor_id: str, source: HarmfulKind) -> tuple[str, str] | None:
    compiled = context.builds.get(actor_id)
    if compiled is None:
        raise ValidationError("Harmful physiology requires an approved campaign actor")
    item = physiology_traits(compiled, context.definitions).purchase("disadvantage:" + source)
    if item is None:
        return None
    if item.modifiers:
        raise ValidationError("Harmful physiology modifiers require a supported variant")
    frequency = str(dict(item.parameters).get("interval"))
    # Rarity prices the purchase; it does not change its injury cadence and
    # cannot renew an existing exposure merely by changing its point cost.
    digest = hashlib.sha256(
        json.dumps([item.definition_id, frequency, item.modifiers]).encode()
    ).hexdigest()
    return digest, frequency


def interval(state: ResourceState, item: HarmfulCondition) -> PhysiologyInterval:
    return PhysiologyInterval(
        id=item.interval_id,
        actor_id=item.actor_id,
        kind=item.source,
        started=item.started,
        due=item.due or 0,
        dependency_due=item.dependency_due,
        calendar=calendar(state),
    )


def timing(state: ResourceState, item: HarmfulCondition) -> tuple[int, int]:
    return harmful_timing(interval(state, item), item.frequency)


def save(state: ResourceState, receipt: HarmfulReceipt, suffix: str = "") -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX
                    + hashlib.sha256(json.dumps([receipt.command_id, suffix]).encode()).hexdigest(),
                    at=state.game_time,
                    target_id=receipt.conditions[0].actor_id if receipt.conditions else "gm",
                    kind=receipt.model_dump_json(),
                ),
            )
        }
    )


def _owed(state: ResourceState, item: HarmfulCondition) -> None:
    if living(state, item) and item.deadline is not None and item.deadline <= state.game_time:
        raise ConflictError("Settle the owed physiology interval before changing its binding")


def reconcile(
    state: ResourceState, item: HarmfulCondition, context: HarmfulContext, command_id: str
) -> HarmfulCondition:
    if not living(state, item):
        return item.model_copy(update={"retired": True, "due": None, "contact_due": None})
    if item.retired and item.actor_id not in context.builds:
        return item
    current = purchase(context, item.actor_id, item.source)
    if current == (item.purchase_digest, item.frequency) and not item.retired:
        return item
    _owed(state, item)
    if current is None:
        return item.model_copy(update={"retired": True, "due": None, "contact_due": None})
    require_supported_actor(context, item.actor_id)
    # An approved replacement takes effect now, never retroactively. Existing
    # exposure persists, but it must earn the replacement's full interval.
    item = item.model_copy(
        update={
            "purchase_digest": current[0],
            "frequency": current[1],
            "started": state.game_time,
            "generation": command_id,
            "dependency_due": None,
            "due": None,
            "contact_started": None,
            "contact_seconds": 0,
            "contact_due": None,
            "retired": False,
        }
    )
    return _schedule_observation(state, item, fresh=True)


def _schedule_observation(
    state: ResourceState, item: HarmfulCondition, *, fresh: bool
) -> HarmfulCondition:
    if fresh and item.source == "dependency" and item.dependency_mode == "contact":
        item = item.model_copy(update={"dependency_due": state.game_time})
    first, cadence = timing(state, item)
    if item.source == "weakness":
        return item.model_copy(update={"due": first if item.present else None})
    if item.dependency_mode == "contact":
        if item.present:
            return item.model_copy(
                update={
                    "due": first,
                    "contact_started": state.game_time,
                    "contact_due": state.game_time + cadence - item.contact_seconds,
                }
            )
        return item.model_copy(update={"due": first})
    if item.present:
        # Constant supply stays safe until a recorded loss of that supply.
        return item.model_copy(update={"due": None if item.frequency == "minute" else first})
    if item.frequency == "minute":
        return item.model_copy(update={"due": first})
    if fresh:
        return item.model_copy(
            update={"dependency_due": state.game_time, "due": state.game_time + cadence}
        )
    return item


def observe(
    state: ResourceState, command: ObservePhysiology, context: HarmfulContext
) -> HarmfulCondition:
    require_supported_actor(context, command.actor_id)
    if any(
        p.id == "hp:" + command.actor_id and p.injury is not None and p.injury.dead
        for p in state.pools
    ):
        raise ValidationError("Dead actors cannot begin harmful physiology exposure")
    old = next(
        (
            i
            for i in conditions(state)
            if (i.actor_id, i.source) == (command.actor_id, command.source)
        ),
        None,
    )
    if command.source == "weakness" and command.dependency_mode != "dose":
        raise ValidationError("Weakness cannot declare a Dependency contact mode")
    selected = purchase(context, command.actor_id, command.source)
    if selected is None:
        raise ValidationError("Observation requires the currently approved physiology purchase")
    if old is not None:
        _owed(state, old)
        if (old.condition_id, old.dependency_mode) != (
            command.condition_id,
            command.dependency_mode,
        ):
            raise ConflictError("The approved harmful condition binding cannot be aliased")
        old = reconcile(state, old, context, command.id)
    if old is None or old.retired:
        item = HarmfulCondition(
            actor_id=command.actor_id,
            source=command.source,
            condition_id=command.condition_id,
            purchase_digest=selected[0],
            frequency=selected[1],
            dependency_mode=command.dependency_mode,
            present=command.present,
            started=state.game_time,
            generation=command.id,
            dependency_due=state.game_time
            if command.source == "dependency" and not command.present
            else None,
        )
        return _schedule_observation(state, item, fresh=True)
    # Repeated exposure or contact declarations cannot renew their clocks.
    dose = command.source == "dependency" and command.dependency_mode == "dose" and command.present
    if old.present == command.present and not dose:
        return old
    if command.source == "weakness":
        item = old.model_copy(
            update={
                "present": command.present,
                "started": state.game_time,
                "generation": command.id,
            }
        )
        return _schedule_observation(state, item, fresh=False)
    if command.dependency_mode == "contact":
        _, cadence = timing(state, old)
        if command.present:
            return old.model_copy(
                update={
                    "present": True,
                    "contact_started": state.game_time,
                    "contact_due": state.game_time + cadence - old.contact_seconds,
                }
            )
        if old.contact_due is not None:
            return old.model_copy(
                update={
                    "present": False,
                    "contact_seconds": old.contact_seconds
                    + state.game_time
                    - (old.contact_started or 0),
                    "contact_started": None,
                    "contact_due": None,
                }
            )
        # Completed continuous contact remains satisfying until the actor leaves.
        item = old.model_copy(
            update={
                "present": False,
                "started": state.game_time,
                "generation": command.id,
                "dependency_due": None,
                "contact_started": None,
            }
        )
        return _schedule_observation(state, item, fresh=False)
    if command.present or old.frequency == "minute":
        item = old.model_copy(
            update={
                "present": command.present,
                "started": state.game_time,
                "generation": command.id,
                "dependency_due": None,
            }
        )
        return _schedule_observation(state, item, fresh=False)
    return old.model_copy(update={"present": False})


def settle(
    state: ResourceState, item: HarmfulCondition, context: HarmfulContext, command_id: str
) -> tuple[ResourceState, HarmfulCondition, tuple[PhysiologyOutcome, ...]]:
    if item.retired:
        raise ConflictError("Physiology interval was already retired")
    if not living(state, item):
        return (
            state,
            item.model_copy(update={"retired": True, "due": None, "contact_due": None}),
            (),
        )
    if item.deadline is None or item.deadline != state.game_time:
        raise ConflictError("Physiology settlement requires the exact persisted deadline")
    if purchase(context, item.actor_id, item.source) != (item.purchase_digest, item.frequency):
        raise ConflictError(
            "Reconcile the changed approved physiology purchase before its deadline"
        )
    if item.contact_due == state.game_time:
        # B130 requires the full contact duration, without specifying that it
        # must be uninterrupted. Accrued contact completes before a simultaneous
        # damage tick; merely entering or briefly touching never resets the dose.
        return (
            state,
            item.model_copy(
                update={
                    "started": state.game_time,
                    "generation": command_id,
                    "dependency_due": None,
                    "contact_due": None,
                    "contact_seconds": 0,
                    "due": None,
                }
            ),
            (),
        )
    state, outcome = apply_physiology_interval(
        state,
        PhysiologyCommand(
            id=command_id + ":" + item.interval_id,
            actor_id=item.actor_id,
            expected_revision=state.revision,
            interval_id=item.interval_id,
        ),
        interval(state, item),
        context.builds[item.actor_id],
        context.definitions,
        authorized_actor_id=item.actor_id,
        system=True,
        rng=context.rng,
        held_item_ids=tuple(
            gear.id
            for gear in state.items
            if gear.owner_id == item.actor_id
            and gear.ready
            and context.engine.specs[gear.definition_id].slot == "hand"
        ),
    )
    _, cadence = timing(state, item)
    dead = not living(state, item)
    return (
        state,
        item.model_copy(
            update={
                "due": None if dead else state.game_time + cadence,
                "contact_due": None if dead else item.contact_due,
                "retired": dead,
            }
        ),
        (outcome,),
    )


def advance(
    state: ResourceState, command: AdvancePhysiology, context: HarmfulContext
) -> tuple[ResourceState, tuple[PhysiologyOutcome, ...]]:
    injuries: list[PhysiologyOutcome] = []
    if command.to < state.game_time:
        raise ValidationError("Game time cannot move backwards")
    for item in conditions(state):
        updated = reconcile(state, item, context, command.id)
        if updated != item:
            state = save(
                state,
                HarmfulReceipt(
                    command_id=command.id, conditions=(updated,), game_time=state.game_time
                ),
                suffix=":reconcile:" + item.actor_id + ":" + item.source,
            )
    for index in range(10000):
        due = sorted(
            (
                i
                for i in conditions(state)
                if not i.retired and i.deadline is not None and i.deadline <= command.to
            ),
            key=lambda i: (i.deadline or 0, i.actor_id, i.source),
        )
        if not due:
            break
        item = due[0]
        assert item.deadline is not None
        state = context.engine.apply(
            state,
            Advance(
                id=f"physiology-clock:{command.id}:{index}",
                actor_id=command.actor_id,
                expected_revision=state.revision,
                to=item.deadline,
            ),
            system=True,
            rng=context.rng,
        )
        state, updated, outcomes = settle(state, item, context, command.id)
        injuries.extend(outcomes)
        state = save(
            state,
            HarmfulReceipt(command_id=command.id, conditions=(updated,), game_time=state.game_time),
            suffix=f":{index}",
        )
    else:
        raise ValidationError("Physiology advancement exceeds the bounded interval limit")
    state = context.engine.apply(
        state,
        Advance(
            id="physiology-clock:" + command.id + ":final",
            actor_id=command.actor_id,
            expected_revision=state.revision,
            to=command.to,
        ),
        system=True,
        rng=context.rng,
    )
    return state, tuple(injuries)


def apply(
    state: ResourceState, command: HarmfulCommand, context: HarmfulContext
) -> tuple[ResourceState, HarmfulReceipt]:
    if state.revision != command.expected_revision:
        raise ConflictError("Physiology revision changed")
    revision = state.revision + 1
    changed: tuple[HarmfulCondition, ...] = ()
    injuries: tuple[PhysiologyOutcome, ...] = ()
    declared = None
    if isinstance(command, ObservePhysiology):
        changed = (observe(state, command, context),)
    elif isinstance(command, ReconcilePhysiology):
        item = next(
            (
                i
                for i in conditions(state)
                if (i.actor_id, i.source) == (command.actor_id, command.source)
            ),
            None,
        )
        if item is None:
            raise ValidationError("No recorded harmful physiology binding")
        changed = (reconcile(state, item, context, command.id),)
    elif isinstance(command, SettlePhysiology):
        item = next(
            (
                i
                for i in conditions(state)
                if i.actor_id == command.actor_id and i.interval_id == command.interval_id
            ),
            None,
        )
        if item is None:
            raise ConflictError("Physiology interval was consumed or its identity changed")
        state, updated, injuries = settle(state, item, context, command.id)
        changed = (updated,)
    elif isinstance(command, AdvancePhysiology):
        state, injuries = advance(state, command, context)
        changed = conditions(state)
    else:
        assert isinstance(command, DeclarePhysiologyCalendar)
        existing = calendar(state)
        if any(
            not i.retired
            and i.source == "dependency"
            and i.frequency in {"month", "season", "year"}
            for i in conditions(state)
        ) and (
            existing is None
            or command.calendar.month_boundaries[: len(existing.month_boundaries)]
            != existing.month_boundaries
            or command.calendar.months_per_year != existing.months_per_year
            or command.calendar.months_per_season != existing.months_per_season
        ):
            raise ConflictError(
                "An active Dependency calendar can only extend its known boundaries"
            )
        declared = command.calendar
    receipt = HarmfulReceipt(
        command_id=command.id,
        conditions=changed,
        calendar=declared,
        injuries=injuries,
        game_time=state.game_time,
    )
    state = save(state, receipt).model_copy(update={"revision": revision})
    context.engine.validate(state)
    return state, receipt
