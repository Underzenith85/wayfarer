"""Characters third printing B248 healing effects on canonical patient HP."""

from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.magic.backfires import Backfire, save
from wayfarer.engine.simulation.magic.spell_state import PREFIX, SpellEffect, SpellEvent
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError

HEALING = frozenset({"minor-healing", "major-healing", "great-healing"})
DAY = 86400


def attempts(state: ResourceState, spell_id: str, actor_id: str, target_id: str) -> int:
    """Count completed attempts once per cast, retaining separate Minor/Major tallies."""
    found = set()
    for event in state.events:
        if not event.id.startswith(PREFIX) or event.at // DAY != state.game_time // DAY:
            continue
        item = SpellEvent.model_validate_json(event.kind)
        if (
            item.effect.spell_id == spell_id
            and item.effect.target_id == target_id
            and (spell_id == "great-healing" or item.effect.actor_id == actor_id)
            and item.result.outcome in ("active", "failed", "critical-failure")
        ):
            found.add(item.effect.cast_id)
    return len(found)


def patient(state: ResourceState, target_id: str) -> Pool:
    hp = next((p for p in state.pools if p.id == "hp:" + target_id), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Healing requires canonical Basic Set patient HP")
    if hp.injury.dead:
        raise ValidationError("Healing cannot revive a dead patient")
    return hp


def healing_scale(spell_id: str, energy: int, magery: int) -> None:
    if spell_id not in HEALING:
        return
    cap = max(3 if spell_id == "minor-healing" else 4, magery)
    if spell_id == "great-healing":
        cap = 1
    if energy > cap:
        raise ValidationError("Selected healing energy exceeds the source/Magery limit")


def healing_penalty(state: ResourceState, spell_id: str, actor_id: str, target_id: str) -> int:
    if spell_id not in HEALING:
        return 0
    hp = patient(state, target_id)
    repeated = attempts(state, spell_id, actor_id, target_id)
    if spell_id == "great-healing" and repeated:
        raise ConflictError("Patient already had a Great Healing attempt today")
    return 3 * repeated + (max(0, hp.maximum - hp.current) if actor_id == target_id else 0)


def mitigates_failure(state: ResourceState, effect: SpellEffect, physician_skill: int) -> bool:
    return (
        effect.spell_id in HEALING
        and physician_skill >= 15
        and (
            effect.spell_id == "great-healing"
            or not attempts(state, effect.spell_id, effect.actor_id, effect.target_id)
        )
    )


def heal(state: ResourceState, effect: SpellEffect) -> tuple[ResourceState, int]:
    # Recheck the daily benefit at completion, including other casters' intervening casts.
    healing_penalty(state, effect.spell_id, effect.actor_id, effect.target_id)
    hp = patient(state, effect.target_id)
    amount = (
        hp.maximum - hp.current
        if effect.spell_id == "great-healing"
        else effect.energy * (2 if effect.spell_id == "major-healing" else 1)
    )
    restored, amount = restore_hp(state, hp, max(0, amount), kind="magic")
    return state.model_copy(
        update={"pools": tuple(restored if p.id == hp.id else p for p in state.pools)}
    ), amount


def healing_critical(state: ResourceState, effect: SpellEffect, command_id: str) -> ResourceState:
    # B248 gives contextual patient harm, not a numeric damage formula or B236 caster table.
    return save(
        state,
        Backfire(
            id=command_id + ":patient",
            cast_id=effect.cast_id,
            actor_id=effect.actor_id,
            spell_id=effect.spell_id,
            at=state.game_time,
            pending=True,
            target_id=effect.target_id,
        ),
        command_id,
    )
