"""B393 surprise resolution for the start of combat."""

import json
from collections.abc import Callable
from dataclasses import dataclass

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.rules.traits.physical import SurpriseState
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.physical_traits import physical_traits
from wayfarer.engine.simulation.resources import Command, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


class SurpriseCommand(Command):
    encounter_id: str
    trigger_id: str


@dataclass(frozen=True)
class SurpriseSides:
    first: tuple[str, ...]
    second: tuple[str, ...]
    first_leader: str | None
    second_leader: str | None
    total: bool = False  # The first side has surprised the second.


Resolver = Callable[[RulesContext, PlayState, SurpriseCommand], SurpriseSides]


def apply_surprise(
    state: PlayState,
    command: SurpriseCommand,
    runtime: RulesContext,
    resolver: Resolver,
    gm_id: str,
) -> PlayState:
    if gm_id not in runtime.reviewer.gm_ids or not any(
        m.principal_id == gm_id and m.role == "gm" for m in state.members
    ):
        raise ValidationError("Surprise requires director authority")
    encounter = next((e for e in state.encounters if e.id == command.encounter_id), None)
    if (
        encounter is None
        or encounter.status != "active"
        or encounter.round != 1
        or encounter.turn_index != 0
        or any(p.last_maneuver is not None for p in encounter.participants)
    ):
        raise ValidationError("Surprise must be resolved before the first combat turn")
    event_id = "surprise:" + encounter.id
    if any(e.id == event_id for e in state.resources.events):
        raise ValidationError("Encounter surprise already resolved")
    sides = resolver(runtime, state, command)
    first, second = set(sides.first), set(sides.second)
    if (
        not first
        or not second
        or first & second
        or len(first) != len(sides.first)
        or len(second) != len(sides.second)
        or first | second != set(encounter.turn_order)
    ):
        raise ValidationError("Surprise requires two disjoint complete sides")
    leaders = (sides.first_leader, sides.second_leader)
    groups = (sides.first, sides.second)
    compiled = {a: build(runtime, state, a) for a in encounter.turn_order}
    stats = {a: b.statistics for a, b in compiled.items()}
    if any(s is None or s.profile_id != "gurps-basic-set-4e-2004" for s in stats.values()):
        raise ValidationError("Surprise requires the exact Basic Set profile")
    traits = {a: physical_traits(state.resources, a) for a in encounter.turn_order}
    for pool in state.resources.pools:
        if (
            pool.injury is not None
            and pool.id.removeprefix("hp:") in first | second
            and (pool.injury.stunned or pool.injury.incapacitated)
        ):
            raise ValidationError("Surprise requires conscious, unstunned combatants")
    scores = []
    iq = []
    for group, leader in zip(groups, leaders, strict=True):
        if leader is not None and leader not in group:
            raise ValidationError("Leader must belong to the side")
        leader_stats = stats[leader] if leader else None
        iq.append(leader_stats.iq if leader_stats else 0)
    for index, (group, leader) in enumerate(zip(groups, leaders, strict=True)):
        bonus = (
            2
            if leader and traits[leader].combat_reflexes
            else int(any(traits[a].combat_reflexes for a in group))
        )
        bonus += int(iq[index] > iq[1 - index])
        if leader:
            tactics = next(
                (v for v in compiled[leader].sheet.values if v.target == "skill:tactics"),
                None,
            )
            learned = any(p.definition_id == "skill:tactics" for p in compiled[leader].purchases)
            bonus += (2 if tactics is not None and tactics.value >= 20 else 1) if learned else 0
        elif any(s is not None and s.iq > 5 for a in group if (s := stats[a]) is not None):
            bonus -= 2
        if not sides.total or any(traits[a].combat_reflexes for a in second):
            scores.append(draw_dice(runtime.rng, 1)[0] + bonus)
        else:
            scores.append(1 if index == 0 else 0)
    loser = first if scores[0] < scores[1] else second if scores[1] < scores[0] else set()
    freeze = (
        draw_dice(runtime.rng, 1)[0]
        if sides.total and any(not traits[a].combat_reflexes for a in second)
        else 0
    )
    pools = []
    for pool in state.resources.pools:
        actor = pool.id.removeprefix("hp:")
        if pool.injury is not None and actor in first | second:
            if pool.injury.stunned or pool.injury.incapacitated:
                raise ValidationError("Surprise requires conscious, unstunned combatants")
            affected = (
                (actor in second and (not traits[actor].combat_reflexes or actor in loser))
                if sides.total
                else actor in loser
            )
            if affected:
                partial = not sides.total or traits[actor].combat_reflexes
                surprise = SurpriseState(partial=partial, freeze_turns=0 if partial else freeze)
                pool = pool.model_copy(
                    update={
                        "injury": pool.injury.model_copy(
                            update={"stunned": True, "surprise": surprise}
                        )
                    }
                )
        pools.append(pool)
    leading = (
        first
        if sides.total or scores[0] > scores[1]
        else second
        if scores[1] > scores[0]
        else set()
    )
    order = tuple(a for a in encounter.turn_order if a in leading) + tuple(
        a for a in encounter.turn_order if a not in leading
    )
    encounter = encounter.model_copy(update={"turn_order": order})
    resources = state.resources.model_copy(
        update={
            "pools": tuple(pools),
            "revision": state.revision + 1,
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=state.resources.game_time,
                    target_id=command.actor_id,
                    kind=json.dumps({"initiative": scores, "freeze": freeze}),
                ),
            ),
        }
    )
    return state.model_copy(
        update={
            "resources": resources,
            "revision": resources.revision,
            "encounters": tuple(encounter if e.id == encounter.id else e for e in state.encounters),
        }
    )
