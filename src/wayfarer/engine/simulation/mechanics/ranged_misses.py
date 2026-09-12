"""Basic B556-557 critical misses, before projectile expenditure in the same CAS."""

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat import CombatEngine, Encounter
from wayfarer.engine.simulation.mechanics.critical_limbs import CriticalLimbResult, resolve_limb
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def resolve_miss(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    table: tuple[int, ...],
    *,
    parry_item: str | None = None,
    parry_mode_id: str | None = None,
) -> tuple[PlayState, Encounter, CriticalLimbResult, str | None]:
    from wayfarer.engine.simulation.mechanics.gurps_melee import catalog

    if catalog(runtime).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Ranged critical misses require the exact Basic Set profile")
    pending = encounter.pending_defense
    assert pending is not None
    parrying = parry_item is not None
    subject_id = pending.defender_id if parrying else pending.attacker_id
    item_id = parry_item if parrying else pending.weapon_id
    blocker = "ranged-critical-parry" if parrying else "ranged-critical-table"
    state, encounter, result = resolve_limb(
        runtime,
        state,
        encounter,
        table=table,
        defender_item=parry_item,
        defender_mode_id=parry_mode_id,
        blocker="basic-critical-miss:" + ("defender" if parrying else "attacker"),
    )
    if result.resolved:
        return state, encounter, result, None
    number = sum(result.table_rolls[-1])
    subject = next(p for p in encounter.participants if p.actor_id == subject_id)
    item = next(i for i in state.resources.items if i.id == item_id)
    entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    broken = number in (3, 4, 17, 18) or (
        number in (9, 10, 11, 14) and entry.critical_breakage == "cheap"
    )
    if broken:
        # Do not infer quality or firearm construction from damage type or skill name.
        if entry.critical_breakage is None or item.condition is None:
            return state, encounter, result, blocker
        if entry.critical_breakage == "resistant":
            roll = draw_dice(runtime.rng, 3)
            result = result.model_copy(update={"table_rolls": result.table_rolls + (roll,)})
            broken = sum(roll) in (3, 4, 17, 18)
            if not broken:
                number = 9
    if number in (7, 13) or (number == 16 and not parrying):
        subject = subject.model_copy(update={"defense_penalty": -2})
    elif number == 16:
        subject = subject.model_copy(update={"posture": "prone"})
    elif broken or number in (8, 9, 10, 11, 12, 14):
        updates: dict[str, object] = {"ready": False}
        if broken:
            assert item.condition is not None
            updates["condition"] = item.condition.model_copy(update={"disabled": True})
        if broken or number in (9, 10, 11, 14):
            from wayfarer.engine.simulation.mechanics.weapon_flight import position

            updates["equipped"] = False
            updates["ground"] = position(encounter, subject)
            updates["container_id"] = None
        item = item.model_copy(update=updates)
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            item if i.id == item.id else i for i in state.resources.items
                        ),
                    }
                )
            }
        )
        subject = subject.model_copy(
            update={
                "ready_item_ids": tuple(i for i in subject.ready_item_ids if i != item_id),
                "hand_bindings": tuple((i, h) for i, h in subject.hand_bindings if i != item_id),
            }
        )
    else:
        return state, encounter, result, blocker
    return (
        state,
        CombatEngine._replace(encounter, subject),
        result.model_copy(update={"resolved": True}),
        None,
    )
