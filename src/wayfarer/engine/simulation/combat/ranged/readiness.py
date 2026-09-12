"""B195/B270/B382 readiness inside CombatService's command transaction.

Bow holding adds no FP cost. Per-round unload timing is authored, not certified.
"""

import hashlib
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.rules.checks import Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.readiness import ProjectileProgress
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.combat.ranged.strength import validate_rated_strength
from wayfarer.engine.simulation.combat.thrown.flight import position
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.health.condition_checks import definition_modifiers
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.physical_traits import physical_traits
from wayfarer.engine.simulation.resources import AmmunitionLoad, ResourceEvent, ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.combat.encounter import Encounter
    from wayfarer.engine.simulation.rules_context import RulesContext


def save_load(
    resources: ResourceState, load: AmmunitionLoad | None, weapon_id: str
) -> ResourceState:
    return resources.model_copy(
        update={
            "ammunition_loads": tuple(
                v for v in resources.ammunition_loads if v.weapon_id != weapon_id
            )
            + ((load,) if load is not None else ())
        }
    )


def reload(
    runtime: RulesContext,
    state: PlayState,
    command: TakeCombatTurn,
    weapon: RangedMode,
    *,
    validate_only: bool,
) -> ResourceState:

    spec = weapon.readiness
    assert spec is not None
    if catalog(runtime).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Readiness requires the exact Basic Set profile")
    resources = state.resources
    item = next(i for i in resources.items if i.id == command.item_id)
    ammo = next(i for i in resources.items if i.id == command.reload_ammunition_id)
    if item.ground or ammo.ground or ammo.container_id:
        raise ValidationError("Readiness requires accessible weapon and loose ammunition source")
    if item.condition and item.condition.disabled:
        raise ValidationError("A broken weapon cannot be loaded")
    encounter = next(e for e in state.encounters if e.id == command.encounter_id)
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    old = next((v for v in resources.ammunition_loads if v.weapon_id == item.id), None)
    if old and (old.mode_id != weapon.id or old.ammunition_item_id != ammo.id):
        raise ValidationError("Unload before switching ammunition sources")
    if old and old.readiness is None:
        raise ValidationError("Existing loads require explicit unloading before protocol migration")
    rounds = old.rounds if old else 0
    if rounds >= weapon.shots or ammo.definition_id != weapon.ammunition_id:
        raise ValidationError("Weapon is full or ammunition does not match")
    reserved = sum(v.rounds for v in resources.ammunition_loads if v.ammunition_item_id == ammo.id)
    available = ammo.quantity - reserved
    if available <= 0:
        raise ValidationError("No unreserved ammunition remains")
    progress = old.readiness if old and old.readiness else ProjectileProgress()
    if progress.stage == "unload":
        raise ValidationError("Finish unloading before loading")
    compiled = build(runtime, state, command.actor_id)
    assert compiled.statistics is not None
    fp = next(p for p in resources.pools if p.id == f"fp:{command.actor_id}")
    st = fatigue_value(fp, compiled.statistics.st)
    validate_rated_strength(catalog(runtime).profile_id, weapon, st)
    required = max(1, weapon.reload_seconds)
    aid_id = command.cocking_aid_id or progress.cocking_aid_id
    if spec.kind == "crossbow":
        assert weapon.rated_strength is not None
        difference = weapon.rated_strength.st - st
        if difference >= 5:
            raise ValidationError("Crossbow ST is too high to reload")
        required = 8 if difference > 0 else 4
        if difference >= 3:
            aid = next((i for i in resources.items if i.id == aid_id), None)
            if (
                aid is None
                or aid.owner_id != command.actor_id
                or aid.ground
                or aid.container_id
                or aid.definition_id != spec.cocking_aid_definition_id
                or (aid.condition is not None and aid.condition.disabled)
                or actor.posture != "standing"
            ):
                raise ValidationError(
                    "High-ST crossbow requires an accessible cocking aid and standing"
                )
            required = 20  # Explicit B270 protocol; B410's cocking-only text remains source audit.
    elif aid_id is not None:
        raise ValidationError("Cocking aid requires a crossbow")
    if command.fast_draw:
        if (
            progress.fast_draw_used
            or progress.elapsed
            or progress.stage not in ("prepare", "loaded")
        ):
            raise ValidationError("Fast-Draw is available once at the beginning of preparation")
        skill = spec.fast_draw_skill_id
        expected = (
            f"skill:fast-draw-ammo-tl{weapon.firearm.technology_level}"
            if weapon.firearm
            else "skill:fast-draw-arrow"
        )
        if skill != expected or not any(p.definition_id == skill for p in compiled.purchases):
            raise ValidationError(
                "Fast-Draw requires the exact trained specialty and technology level"
            )
        learned = next((v for v in compiled.sheet.values if v.target == skill), None)
        if learned is None:
            raise ValidationError("Fast-Draw specialty has no compiled level")
    else:
        learned = None
    if validate_only:
        return resources
    if progress.stage == "loaded":
        progress = ProjectileProgress()
    elapsed = progress.elapsed
    fast_used = progress.fast_draw_used
    if command.fast_draw:
        assert learned is not None and spec.fast_draw_skill_id is not None

        traits = physical_traits(resources, command.actor_id)
        trace = success_roll(
            catalog(runtime).profile_id,
            int(learned.value) + int(traits.combat_reflexes),
            definition_modifiers(
                resources,
                command.actor_id,
                spec.fast_draw_skill_id,
                runtime.reviewer.compiler.definitions,
            ),
            rng=runtime.rng,
        )
        import json
        from dataclasses import asdict

        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id="fast-draw:" + hashlib.sha256(command.id.encode()).hexdigest(),
                        at=resources.game_time,
                        target_id=command.actor_id,
                        kind=json.dumps(
                            {
                                "skill": spec.fast_draw_skill_id,
                                "weapon": item.id,
                                "ammunition": ammo.id,
                                "trace": asdict(trace),
                            }
                        ),
                    ),
                )
            }
        )
        if not trace.outcome.succeeded:
            count = available if trace.outcome is Outcome.CRITICAL_FAILURE else 1
            drop_id = "dropped-ammo:" + hashlib.sha256(command.id.encode()).hexdigest()
            dropped = ammo.model_copy(
                update={
                    "id": drop_id,
                    "quantity": count,
                    "ground": position(encounter, actor),
                    "container_id": None,
                    "ready": False,
                    "equipped": False,
                }
            )
            resources = resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"quantity": i.quantity - count})
                        if i.id == ammo.id
                        else i
                        for i in resources.items
                        if i.id != ammo.id or i.quantity > count
                    )
                    + (dropped,)
                }
            )
            # The attempt took this Ready, and cannot reserve dropped rounds.
            return save_load(resources, old if old and old.rounds else None, item.id)
        elapsed += min(spec.fast_draw_seconds, required - 1)
        fast_used = True
    elapsed += 1
    if elapsed >= required:
        rounds += min(
            1 if weapon.reload_protocol == "per-round" else weapon.shots - rounds, available
        )
        progress = ProjectileProgress(stage="loaded")
    else:
        stage: Literal["draw", "cock", "load"] = (
            "draw"
            if spec.kind == "bow"
            else "cock"
            if spec.kind == "crossbow" and elapsed < required - 1
            else "load"
        )
        progress = ProjectileProgress(
            stage=stage,
            elapsed=elapsed,
            required=required,
            fast_draw_used=fast_used,
            cocking_aid_id=aid_id,
        )
    return save_load(
        resources,
        AmmunitionLoad(
            weapon_id=item.id,
            mode_id=weapon.id,
            ammunition_item_id=ammo.id,
            rounds=rounds,
            readiness=progress,
        ),
        item.id,
    )


def unload(state: PlayState, command: TakeCombatTurn, weapon: RangedMode) -> ResourceState:
    spec = weapon.readiness
    assert spec is not None
    load = next(v for v in state.resources.ammunition_loads if v.weapon_id == command.item_id)
    if load.rounds == 0:
        return save_load(state.resources, None, load.weapon_id)
    if spec.unload_seconds_per_round is None:
        raise ValidationError("Individual unloading needs an explicitly authored timing")
    progress = load.readiness
    elapsed = (progress.elapsed if progress and progress.stage == "unload" else 0) + 1
    rounds = load.rounds
    if elapsed >= spec.unload_seconds_per_round:
        rounds -= 1
        elapsed = 0
    updated = load.model_copy(
        update={
            "rounds": rounds,
            "reload_progress": 0,
            "readiness": ProjectileProgress(
                stage="unload", elapsed=elapsed, required=spec.unload_seconds_per_round
            ),
        }
    )
    return save_load(state.resources, updated if rounds else None, load.weapon_id)


def let_down(state: PlayState, command: TakeCombatTurn, weapon: RangedMode) -> ResourceState:
    if weapon.readiness is None or weapon.readiness.kind != "bow":
        raise ValidationError("Let-down requires an explicit bow readiness protocol")
    load = next(
        (v for v in state.resources.ammunition_loads if v.weapon_id == command.item_id), None
    )
    if (
        load is None
        or load.rounds != 1
        or load.readiness is None
        or load.readiness.stage != "loaded"
    ):
        raise ValidationError("Bow must be drawn before let-down")
    # The arrow is retained at the preparation stage, not destroyed or duplicated.
    return save_load(
        state.resources,
        load.model_copy(
            update={
                "rounds": 0,
                "readiness": ProjectileProgress(stage="draw", elapsed=1, required=2),
            }
        ),
        load.weapon_id,
    )


def interrupted_draws(
    runtime: RulesContext,
    before: PlayState,
    resources: ResourceState,
    encounter_id: str,
    encounter: Encounter,
) -> ResourceState:
    """B382: a dropped, stunned, unbalanced or fallen bow must be drawn again."""

    rules = runtime.rules.combat
    if rules is None or rules.gurps_equipment is None:
        return resources
    equipment = catalog(runtime)
    previous = next((e for e in before.encounters if e.id == encounter_id), None)
    for load in resources.ammunition_loads:
        if load.readiness is None or load.readiness.stage != "loaded":
            continue
        item = next(i for i in resources.items if i.id == load.weapon_id)
        entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
        mode = next((m for m in entry.modes if m.id == load.mode_id), None)
        if (
            not isinstance(mode, RangedMode)
            or mode.readiness is None
            or mode.readiness.kind != "bow"
        ):
            continue
        actor = next((p for p in encounter.participants if p.actor_id == item.owner_id), None)
        old = (
            next((p for p in previous.participants if p.actor_id == item.owner_id), None)
            if previous
            else None
        )
        hp = next(p for p in resources.pools if p.id == f"hp:{item.owner_id}")
        if (
            not item.ready
            or (hp.injury and (hp.injury.stunned or hp.injury.incapacitated))
            or (
                actor
                and old
                and (
                    (actor.posture == "prone" and old.posture != "prone")
                    or actor.defense_penalty < old.defense_penalty
                )
            )
        ):
            resources = save_load(
                resources,
                load.model_copy(
                    update={
                        "rounds": 0,
                        "readiness": ProjectileProgress(stage="draw", elapsed=1, required=2),
                    }
                ),
                item.id,
            )
    return resources
