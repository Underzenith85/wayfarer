"""Advance new Cyclic and enchanting records against current full campaign state.

The legacy resource-only path remains byte-stable. New host-bound occurrences
checkpoint the current body between deadlines, including automatic reversion,
so later damage never reuses an earlier form's defenses (B83/B103/B421).
Enrolled work observes actual mage-loss deadlines (B482), and marked B235 refunds
arrive before later consequences. Unmarked histories retain their old boundaries.
"""

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.objects.locations import settle_crippling
from wayfarer.engine.simulation.combat.settlement import reconcile_equipment, settle_encounter
from wayfarer.engine.simulation.combat.unarmed.choke import finish_choke_turns, retire_chokes
from wayfarer.engine.simulation.combat.unarmed.fighters import settle_control
from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand, apply_world_ground
from wayfarer.engine.simulation.health.cyclic_context import resolver
from wayfarer.engine.simulation.health.cyclic_host_state import binding
from wayfarer.engine.simulation.health.fright import advance as advance_resource_clock
from wayfarer.engine.simulation.health.fright import effects as fright_effects
from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    needs_clock_checkpoints as needs_analysis_checkpoints,
)
from wayfarer.engine.simulation.magic.backfires import chronological_refund_deadlines
from wayfarer.engine.simulation.magic.detect_magic_state import (
    needs_clock_checkpoints as needs_detection_checkpoints,
)
from wayfarer.engine.simulation.magic.enchanting_lifecycle import needs_clock_checkpoints
from wayfarer.engine.simulation.magic.great_haste_effects import deadlines as great_haste_deadlines
from wayfarer.engine.simulation.magic.limb_spell_state import (
    needs_clock_checkpoints as needs_limb_checkpoints,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    needs_clock_checkpoints as needs_melee_checkpoints,
)
from wayfarer.engine.simulation.magic.rooted_feet_state import effects as rooted_effects
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.harmful_physiology_play import settle_actor
from wayfarer.engine.simulation.traits.harmful_physiology_state import conditions
from wayfarer.engine.simulation.traits.innate_criticals import drop_held_items
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.harmful_physiology_clock import reconcile_all
from wayfarer.orchestration.npcs import due_times

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def _needs_checkpoints(state: PlayState, to: int) -> bool:
    resources = state.resources
    return (
        needs_analysis_checkpoints(resources)
        or needs_detection_checkpoints(resources)
        or needs_melee_checkpoints(resources)
        or needs_limb_checkpoints(resources)
        or any(
            e.status == "active" and e.expires_at <= to for e in rooted_effects(resources).values()
        )
        or needs_clock_checkpoints(resources)
        or any(t <= to for t in chronological_refund_deadlines(resources))
        or any(t <= to for t in great_haste_deadlines(resources))
        or any(
            a.active and a.due <= to and binding(resources, a.id) is not None
            for a in resources.cyclic_attacks
        )
        or any(
            e.stage == "exposure" and e.due <= to and binding(resources, e.source.id) is not None
            for e in resources.cyclic_exposures
        )
    )


def _due_occurrence(state: PlayState, at: int) -> bool:
    resources = state.resources
    return (
        any(a.active and a.due <= at for a in resources.cyclic_attacks)
        or any(e.stage == "exposure" and e.due <= at for e in resources.cyclic_exposures)
        or any(i.active and i.due is not None and i.due <= at for i in fright_effects(resources))
    )


def _next_deadline(play: PlayService, state: PlayState, to: int, run_npcs: bool) -> int:
    now = state.resources.game_time
    resources = state.resources
    deadlines = [a.due for a in resources.cyclic_attacks if a.active]
    deadlines += [e.due for e in resources.cyclic_exposures if e.stage == "exposure"]
    deadlines += [s.due for s in resources.scheduled if s.id not in resources.fired]
    deadlines += [
        h.due for h in resources.hazards if h.active and h.spec.id.startswith("spell-fire:")
    ]
    deadlines += [i.due for i in fright_effects(resources) if i.active and i.due is not None]
    deadlines += [
        i.deadline for i in conditions(resources) if not i.retired and i.deadline is not None
    ]
    deadlines += list(chronological_refund_deadlines(resources))
    deadlines += list(great_haste_deadlines(resources))
    deadlines += [e.expires_at for e in rooted_effects(resources).values() if e.status == "active"]
    if needs_clock_checkpoints(resources):
        living = {
            p.id.removeprefix("hp:")
            for p in resources.pools
            if p.injury is not None and not p.injury.dead
        }
        deadlines += [
            p.fatigue.heart_attack_deadline
            for p in resources.pools
            if p.id.removeprefix("fp:") in living
            and p.fatigue is not None
            and p.fatigue.heart_attack
            and p.fatigue.heart_attack_deadline is not None
        ]
    if run_npcs:
        deadlines += list(due_times(play, state, to))
    return min((max(now, t) for t in deadlines if t <= to), default=to)


def _settle_drops(runtime: RulesContext, state: PlayState, before: PlayState) -> PlayState:
    previous = {e.id for e in before.resources.events}
    for event in state.resources.events:
        if event.id in previous or not event.id.startswith("injury:"):
            continue
        dropped = InjuryResult.model_validate_json(event.kind).dropped_ready_items
        if not dropped:
            continue
        encounter = next(
            (
                e
                for e in state.encounters
                if e.status == "active" and event.target_id in e.turn_order
            ),
            None,
        )
        if encounter is not None:
            entity = next(e for e in state.world.entities if e.id == event.target_id)
            resources, encounter, _ = drop_held_items(
                state.resources,
                encounter,
                event.target_id,
                dropped,
                location_id=entity.location_id or "",
            )
            state = state.model_copy(
                update={
                    "resources": resources,
                    "encounters": tuple(
                        encounter if e.id == encounter.id else e for e in state.encounters
                    ),
                }
            )
        else:
            resources = state.resources
            for item_id in dropped:
                identity = hashlib.sha256(f"{event.id}:{item_id}".encode()).hexdigest()
                resources, _ = apply_world_ground(
                    resources,
                    state.world,
                    runtime.resources,
                    WorldGroundCommand(
                        id="cyclic-drop:" + identity,
                        kind="drop",
                        actor_id=event.target_id,
                        expected_revision=resources.revision,
                        item_id=item_id,
                    ),
                    authorized_actor_id=event.target_id,
                    system=True,
                )
            state = state.model_copy(update={"resources": resources})
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(
                        update={
                            "held_item_hands": tuple(
                                (i, h) for i, h in a.held_item_hands if i not in dropped
                            )
                        }
                    )
                    if a.actor_id == event.target_id
                    else a
                    for a in state.actors
                )
            }
        )
    return state


def _settle_fighters(
    play: PlayService, state: PlayState, before: PlayState, command_id: str
) -> PlayState:
    active = {e.id for e in before.encounters if e.status == "active"}
    for original in state.encounters:
        if original.status != "active" and original.id not in active:
            continue
        encounter = settle_encounter(play.rules_context, state, original)
        if play.engine.rules.combat and play.engine.rules.combat.gurps_equipment:
            encounter = settle_control(state, encounter)
            state = retire_chokes(
                play.rules_context, state, original.grips, encounter.grips, command_id
            )
            state = finish_choke_turns(state, encounter)
            if encounter.status == "completed":
                state = settle_crippling(play.rules_context, state, encounter, command_id)
            state, encounter = reconcile_equipment(state, encounter)
        state = state.model_copy(
            update={
                "encounters": tuple(
                    encounter if e.id == encounter.id else e for e in state.encounters
                )
            }
        )
    return state


def advance(
    play: PlayService,
    state: PlayState,
    command: Advance,
    rng: RandomSource,
    *,
    run_npcs: bool = True,
) -> PlayState:
    if not _needs_checkpoints(state, command.to):
        resources = play.engine.resources.for_world(state.world).apply(
            state.resources, command, system=True, rng=rng
        )
        return state.model_copy(update={"resources": resources})
    if command.expected_revision != state.resources.revision:
        raise ConflictError("Resource revision changed")
    if command.to < state.resources.game_time:
        raise ValidationError("Game time cannot move backwards")
    original_revision = state.revision
    resource_revision = state.resources.revision + 1
    state = reconcile_all(play, state, command.id)
    for index in range(10000):
        due = _next_deadline(play, state, command.to, run_npcs)
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"revision": command.expected_revision}
                )
            }
        )
        before = state
        internal = hashlib.sha256(f"{command.id}:{index}:{due}".encode()).hexdigest()
        resources = advance_resource_clock(
            play.engine.resources.for_world(state.world),
            state.resources,
            command.model_copy(
                update={
                    "id": "composed-clock:" + internal,
                    "expected_revision": state.resources.revision,
                    "to": due,
                }
            ),
            rng=rng,
            cyclic_context=resolver(play.rules_context, state),
            single_occurrence=True,
        )
        state = state.model_copy(update={"resources": resources, "revision": original_revision + 1})
        for actor_id in sorted({i.actor_id for i in conditions(resources) if not i.retired}):
            state = settle_actor(play.rules_context, state, actor_id, command.id)
        state = play.checkpoint(
            state, before=before, run_npcs=run_npcs and not _due_occurrence(state, due)
        )
        state = reconcile_all(play, state, command.id)
        state = _settle_drops(play.rules_context, state, before)
        state = _settle_fighters(play, state, before, command.id)
        if due == command.to and not _due_occurrence(state, due):
            break
    else:
        raise ValidationError("Cyclic host advancement exceeds the bounded interval limit")
    resources = (
        play.engine.resources.for_world(state.world)
        .apply(
            state.resources.model_copy(update={"revision": command.expected_revision}),
            command,
            system=True,
            rng=rng,
            cyclic_context=resolver(play.rules_context, state),
        )
        .model_copy(update={"revision": resource_revision})
    )
    play.engine.resources.validate(resources)
    return state.model_copy(update={"resources": resources, "revision": original_revision})
