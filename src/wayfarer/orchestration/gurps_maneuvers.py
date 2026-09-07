"""Authoritative maneuver checks: Basic Set B364-366 (numeric 2004 baseline)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.errors import ValidationError
from wayfarer.orchestration.gurps_melee import build, catalog, level, mode
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import CombatEngine, Encounter
from wayfarer.simulation.gurps_equipment import MeleeMode, RangedMode
from wayfarer.simulation.maneuvers import attack_modifier

if TYPE_CHECKING:
    from wayfarer.orchestration.combat import TakeCombatTurn
    from wayfarer.orchestration.play import PlayService


def observe(
    play: PlayService, state: PlayState, encounter: Encounter, command: TakeCombatTurn
) -> Encounter:
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    target = next(p for p in encounter.participants if p.actor_id == command.target_id)
    if command.maneuver == "aim":
        item = next((i for i in state.resources.items if i.id == command.item_id), None)
        if item is None or item.owner_id != actor.actor_id or not item.ready or not item.equipped:
            raise ValidationError("Aim requires an owned ready weapon")
        entry = next(e for e in catalog(play).entries if e.definition_id == item.definition_id)
        modes = [
            m
            for m in entry.modes
            if isinstance(m, RangedMode) and (command.mode_id is None or m.id == command.mode_id)
        ]
        if len(modes) != 1:
            raise ValidationError("Aim requires one selected ranged mode")
        return CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "maneuver_state": actor.maneuver_state.model_copy(
                        update={"aim_accuracy": modes[0].accuracy, "aim_mode_id": modes[0].id}
                    )
                }
            ),
        )
    weapon = mode(play, state, actor.actor_id, command.item_id or "", command.mode_id)
    if not isinstance(weapon, MeleeMode):
        raise ValidationError("Feint requires a melee mode")
    attacker = build(play, state, actor.actor_id)
    defender = build(play, state, target.actor_id)
    assert defender.statistics is not None
    defense = defender.statistics.dx
    for item in state.resources.items:
        if item.owner_id != target.actor_id or not item.ready or not item.equipped:
            continue
        entry = next(e for e in catalog(play).entries if e.definition_id == item.definition_id)
        skills = [m.skill_id for m in entry.modes if isinstance(m, MeleeMode)]
        if entry.shield:
            skills.append(entry.shield.skill_id)
        for skill in skills:
            try:
                defense = max(defense, int(level(defender, skill).value))
            except ValidationError:
                continue
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    value = attack_modifier(
        actor.maneuver_state, target.actor_id, int(level(attacker, weapon.skill_id).value)
    )
    value -= hp.injury.shock if hp.injury else 0
    first = success_roll(catalog(play).profile_id, value, rng=play.rng)
    second = success_roll(catalog(play).profile_id, defense, rng=play.rng)
    penalty = (
        max(0, value - sum(first.dice) - max(0, defense - sum(second.dice)))
        if first.outcome.succeeded
        else 0
    )
    return CombatEngine._replace(
        encounter,
        actor.model_copy(
            update={
                "maneuver_state": actor.maneuver_state.model_copy(
                    update={
                        "feint_target_id": target.actor_id,
                        "feint_penalty": penalty,
                        "feint_rolls": (first, second),
                        "evaluate_bonus": 0,
                        "evaluate_target_id": None,
                    }
                )
            }
        ),
    )


def distracted(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    *,
    defended: bool,
    injured: bool,
) -> Encounter:
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    commitment = actor.maneuver_state
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    if commitment.aim_seconds and (
        defended
        or (
            injured
            and not success_roll(
                catalog(play).profile_id, compiled.statistics.will, rng=play.rng
            ).outcome.succeeded
        )
    ):
        commitment = commitment.model_copy(
            update={"aim_seconds": 0, "aim_item_id": None, "aim_target_id": None}
        )
    if commitment.concentrating and (defended or injured):
        if not success_roll(
            catalog(play).profile_id, compiled.statistics.will - 3, rng=play.rng
        ).outcome.succeeded:
            commitment = commitment.model_copy(
                update={"concentrating": False, "concentration_seconds": 0}
            )
    return CombatEngine._replace(encounter, actor.model_copy(update={"maneuver_state": commitment}))
