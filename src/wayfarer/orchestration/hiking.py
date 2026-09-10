"""B351 daily party pacing from approved builds and authoritative load."""

import json
from decimal import Decimal

from wayfarer.character.statistics import encumbered_move, encumbrance
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical import _build
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import CheckTrace
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.physical import hiking_miles
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.fatigue import fatigue_value
from wayfarer.simulation.injury import impaired_movement
from wayfarer.simulation.party import group_for
from wayfarer.simulation.resources import ResourceEvent


def group_hiking(
    play: PlayService, state: PlayState, actor_id: str, *, terrain: str, bad_weather: bool
) -> tuple[PlayState, Decimal, tuple[CheckTrace, ...], tuple[tuple[str, int, int], ...]]:
    group = group_for(state, actor_id)
    members = tuple(sorted(group.actor_ids))
    leader = _build(play, state, actor_id)
    leadership = int(
        next((v.value for v in leader.sheet.values if v.target == "skill:leadership"), 0)
    )
    skills: list[int] = []
    moves: list[int] = []
    costs: list[tuple[str, int, int]] = []
    for member in members:
        from wayfarer.orchestration.recovery import guard

        guard(state, member, "hike")
        actor = next(a for a in state.actors if a.actor_id == member)
        if actor.available_at > state.resources.game_time or any(
            e.status == "active" and member in e.turn_order for e in state.encounters
        ):
            raise ValidationError("Every hiker must be available outside combat")
        build = _build(play, state, member)
        stats = build.statistics
        assert stats is not None
        hp = next(p for p in state.resources.pools if p.id == "hp:" + member)
        fp = next(p for p in state.resources.pools if p.id == "fp:" + member)
        if hp.current <= 0 or fp.current <= 0:
            raise ValidationError(
                "Group hiking requires stabilizing exhausted or badly injured members"
            )
        if (
            hp.injury is None
            or hp.injury.incapacitated
            or hp.injury.stunned
            or (fp.fatigue is None or fp.fatigue.unconscious or fp.fatigue.heart_attack)
        ):
            raise ValidationError("Every hiker must be capable")
        from wayfarer.rules.location_types import disabled_locations

        if disabled_locations(
            hp.injury.lasting_injuries,
            now=state.resources.game_time,
            full_hp=hp.current >= hp.maximum,
        ) & {"left-leg", "right-leg", "left-foot", "right-foot"}:
            raise ValidationError("Group hiking requires supported mobility")
        load = encumbrance(
            stats.profile_id,
            stats.basic_lift,
            Decimal(play.engine.resources.carried_weight(state.resources, member)) / 1000,
        )
        if load is None:
            raise ValidationError("Group member is overloaded")
        skills.append(
            int(
                next(
                    (v.value for v in build.sheet.values if v.target == "skill:hiking"),
                    stats.ht - 5,
                )
            )
        )
        moves.append(
            fatigue_value(
                fp, impaired_movement(hp, encumbered_move(stats.profile_id, stats.basic_move, load))
            )
        )
        costs.append((member, stats.ht, int(load)))
    # Share the same per-actor/day records as individual hiking, so regrouping or
    # changing route IDs cannot reroll the daily bonus.
    day = state.resources.game_time // 86400
    records = [
        next((e for e in state.resources.events if e.id == f"hiking-day:{m}:{day}"), None)
        for m in members
    ]
    checks: list[CheckTrace] = []
    successes = [bool(json.loads(e.kind)["succeeded"]) if e else False for e in records]
    if leadership >= 12 and not any(records):
        check = success_roll(
            "gurps-basic-set-4e-2004", max(1, sum(skills) // len(skills)), rng=play.rng
        )
        checks.append(check)
        successes = [check.outcome.succeeded] * len(members)
    else:
        for i, member in enumerate(members):
            if records[i] is None:
                check = success_roll(
                    "gurps-basic-set-4e-2004",
                    max(1, skills[i]),
                    check_modifiers(state.resources, member, "ht"),
                    rng=play.rng,
                )
                checks.append(check)
                successes[i] = check.outcome.succeeded
    additions = tuple(
        ResourceEvent(
            id=f"hiking-day:{m}:{day}",
            at=state.resources.game_time,
            target_id=m,
            kind=json.dumps({"succeeded": successes[i]}),
        )
        for i, m in enumerate(members)
        if records[i] is None
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"events": state.resources.events + additions}
            )
        }
    )
    miles = min(
        hiking_miles(move, success=successes[i], terrain=terrain, bad_weather=bad_weather)
        for i, move in enumerate(moves)
    )
    return state, miles, tuple(checks), tuple(costs)
