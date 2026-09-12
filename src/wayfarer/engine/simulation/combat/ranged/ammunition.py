"""Loading, unloading and spending rounds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.firearms import spend_rounds
from wayfarer.engine.simulation.combat.ranged.readiness import reload, unload
from wayfarer.engine.simulation.combat.ranged.strength import validate_rated_strength
from wayfarer.engine.simulation.combat.thrown.items import landed
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.resources import AmmunitionLoad, ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def unload_weapon(
    runtime: RulesContext, state: PlayState, command: TakeCombatTurn
) -> ResourceState:
    """Release a removable magazine's reservation; no rounds are minted or spent."""

    equipment = catalog(runtime)
    if equipment.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Unload requires the exact Basic Set profile")
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id:
        raise ValidationError("Unload requires an owned weapon")
    if item.firearm_failure is not None:
        raise ValidationError("Service the firearm failure before unloading")
    loaded = next(
        (load for load in state.resources.ammunition_loads if load.weapon_id == item.id), None
    )
    if loaded is None or command.mode_id not in (None, loaded.mode_id):
        raise ValidationError("Unload requires the loaded weapon mode")
    entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
    weapon = next((m for m in entry.modes if m.id == loaded.mode_id), None)
    if isinstance(weapon, RangedMode) and weapon.readiness is not None:
        return unload(state, command, weapon)
    if not isinstance(weapon, RangedMode) or weapon.reload_protocol != "magazine":
        raise ValidationError("Individual-round unloading requires its own timing protocol")
    result = state.resources.model_copy(
        update={
            "ammunition_loads": tuple(
                load for load in state.resources.ammunition_loads if load.weapon_id != item.id
            )
        }
    )
    runtime.resources.validate(result)
    return result


def reload_weapon(
    runtime: RulesContext, state: PlayState, command: TakeCombatTurn, *, validate_only: bool = False
) -> ResourceState:

    equipment = catalog(runtime)
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    ammo = next((i for i in state.resources.items if i.id == command.reload_ammunition_id), None)
    if (
        item is None
        or ammo is None
        or item.id == ammo.id
        or (item.owner_id != command.actor_id or ammo.owner_id != command.actor_id)
    ):
        raise ValidationError("Reload requires owned weapon and ammunition")
    entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
    modes = [
        m
        for m in entry.modes
        if isinstance(m, RangedMode) and (command.mode_id is None or m.id == command.mode_id)
    ]
    if len(modes) != 1 or modes[0].thrown:
        raise ValidationError("Reload requires one projectile mode")
    weapon = modes[0]
    if item.firearm_failure is not None:
        raise ValidationError("Service the firearm failure before reloading")
    if weapon.readiness is not None:
        return reload(runtime, state, command, weapon, validate_only=validate_only)
    if command.fast_draw or command.cocking_aid_id is not None:
        raise ValidationError("This weapon has no explicit readiness protocol")
    reload_seconds = weapon.reload_seconds
    if weapon.rated_strength is not None:
        stats = build(runtime, state, command.actor_id).statistics
        assert stats is not None
        fp = next(p for p in state.resources.pools if p.id == f"fp:{command.actor_id}")
        st = fatigue_value(fp, stats.st)
        validate_rated_strength(equipment.profile_id, weapon, st)
        if weapon.rated_strength.kind == "crossbow":
            difference = weapon.rated_strength.st - st
            if difference >= 5:
                raise ValidationError("Crossbow ST is too high to reload")
            if difference >= 3:
                raise ValidationError("Crossbow reload requires an explicit cocking-aid protocol")
            reload_seconds = 8 if difference > 0 else 4
    if weapon.reload_protocol == "per-round" and equipment.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Per-round reload requires the exact Basic Set profile")
    if ammo.definition_id != weapon.ammunition_id or item.quantity != 1:
        raise ValidationError("Reload ammunition does not match this individual weapon")
    old = next(
        (loaded for loaded in state.resources.ammunition_loads if loaded.weapon_id == item.id), None
    )
    if old and (old.mode_id != weapon.id or old.ammunition_item_id != ammo.id):
        raise ValidationError("Unload the existing ammunition before changing reload source")
    rounds = old.rounds if old else 0
    if rounds >= weapon.shots:
        raise ValidationError("Weapon is already fully loaded")
    reserved = sum(
        loaded.rounds
        for loaded in state.resources.ammunition_loads
        if loaded.ammunition_item_id == ammo.id
    )
    available_units = ammo.quantity if ammo.charges is None else ammo.charges
    if available_units <= reserved:
        raise ValidationError("No unreserved ammunition remains")
    progress = (old.reload_progress if old else 0) + 1
    if progress >= max(1, reload_seconds):
        rounds += min(
            1 if weapon.reload_protocol == "per-round" else weapon.shots - rounds,
            available_units - reserved,
        )
        progress = 0
    load = AmmunitionLoad(
        weapon_id=item.id,
        mode_id=weapon.id,
        ammunition_item_id=ammo.id,
        rounds=rounds,
        reload_progress=progress,
    )
    result = state.resources.model_copy(
        update={
            "ammunition_loads": tuple(
                loaded for loaded in state.resources.ammunition_loads if loaded.weapon_id != item.id
            )
            + (load,)
        }
    )
    runtime.resources.validate(result)
    return result


def expend(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    shots: int | None = None,
    hit: bool = False,
    catcher_id: str | None = None,
    hand: str | None = None,
) -> tuple[PlayState, Encounter]:

    pending = encounter.pending_defense
    assert pending is not None
    resources = state.resources
    if weapon.thrown:
        item = next(i for i in resources.items if i.id == pending.weapon_id)
        if catalog(runtime).profile_id == "gurps-basic-set-4e-2004":
            resources, encounter = landed(
                runtime, state, encounter, item, hit=hit, catcher_id=catcher_id, hand=hand
            )
        else:
            resources = resources.model_copy(
                update={
                    "items": tuple(i for i in resources.items if i.id != item.id),
                    "expended_items": resources.expended_items
                    + (
                        item.model_copy(
                            update={"equipped": False, "ready": False, "container_id": None}
                        ),
                    ),
                }
            )
        actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        encounter = CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "ready_item_ids": tuple(
                        i for i in actor.ready_item_ids if i != pending.weapon_id
                    ),
                    "hand_bindings": tuple(
                        (i, h) for i, h in actor.hand_bindings if i != pending.weapon_id
                    ),
                }
            ),
        )
    else:
        resources = spend_rounds(
            resources, pending.weapon_id, pending.shots if shots is None else shots
        )
    runtime.resources.validate(resources)
    return state.model_copy(update={"resources": resources}), encounter
