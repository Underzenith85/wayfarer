"""Private B381-382/B556-557 consequences for a captured ranged Innate Attack.

Characters third B61-62/B201 makes these ranged attacks, not unarmed attacks.
Inventory-free natural sources have no universal printed break/drop/ready mapping.
Those rows retain the committed roll and require an explicitly GM-authored policy;
its executable consequence is recorded separately from selected-printing results.
The enclosing host owns membership, CAS, turn settlement and command replay.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, model_validator

from wayfarer.engine.character.traits.attack_defense import AttackDefenseTraits
from wayfarer.engine.rules.checks import (
    CheckTrace,
    Modifier,
    ModifierKind,
    Outcome,
    RandomSource,
    draw_dice,
)
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.traits.modifiers import AttackProfile
from wayfarer.engine.rules.types.cyclic import CyclicAttack, ZeroDamageCyclicAttack
from wayfarer.engine.rules.types.location import Hand, HumanLocation
from wayfarer.engine.simulation.combat.critical import Digest, TableRoll
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.cyclic import save as save_cyclic
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.hit_locations import effective_dr, missing_location
from wayfarer.engine.simulation.health.injury import (
    DisableLocation,
    InjuryCheck,
    Wound,
    apply_injury,
    apply_location_effect,
)
from wayfarer.engine.simulation.health.symptoms import register as register_symptoms
from wayfarer.engine.simulation.resources import Item, Pool, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
Limb = Literal["left-arm", "right-arm", "left-leg", "right-leg"]
_LIMBS = frozenset({"left-arm", "right-arm", "left-leg", "right-leg"})
_BREAK = frozenset({3, 4, 17, 18})


def classify_ranged_check(check: CheckTrace) -> CheckTrace:
    """B382's ranged exception supersedes the generic failure-by-ten rule."""
    if check.outcome is Outcome.CRITICAL_FAILURE and check.total < 17:
        return replace(check, outcome=Outcome.FAILURE)
    return check


class InnateHitEffects(Record):
    basic_multiplier: Literal[1, 2, 3] = 1
    maximum_damage: bool = False
    halve_dr: Literal["down"] | None = None
    force_major_wound: bool = False
    double_shock: bool = False
    drop_all_held: bool = False


def body_hit_effects(table: TableRoll) -> InnateHitEffects:
    """B556 ordinary body table; the host supports torso, not head/limb targeting."""
    if len(table) != 3 or any(type(d) is not int or not 1 <= d <= 6 for d in table):
        raise ValidationError("Critical table requires three captured six-sided dice")
    row = sum(table)
    return InnateHitEffects(
        basic_multiplier=3 if row in (3, 18) else 2 if row in (5, 16) else 1,
        maximum_damage=row in (6, 15),
        halve_dr="down" if row in (4, 17) else None,
        force_major_wound=row in (7, 13, 14),
        double_shock=row == 8,
        drop_all_held=row == 12,
    )


def _fighter(encounter: Encounter, actor_id: str) -> Combatant:
    actor = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Innate critical subject is not an encounter participant")
    return actor


def _hp(resources: ResourceState, actor_id: str) -> Pool:
    pool = next((p for p in resources.pools if p.id == "hp:" + actor_id), None)
    if pool is None or pool.injury is None or pool.injury.profile_id != PROFILE:
        raise ValidationError("Innate critical requires the selected Basic Set HP state")
    return pool


def drop_held_items(
    resources: ResourceState,
    encounter: Encounter,
    actor_id: str,
    item_ids: tuple[str, ...],
    *,
    location_id: str,
) -> tuple[ResourceState, Encounter, tuple[str, ...]]:
    """Move actual held objects to actual ground and clear their encounter grips."""
    # deferred: ground placement enters the thrown-flight verb only while reducing a drop.
    from wayfarer.engine.simulation.combat.thrown.flight import position

    actor = _fighter(encounter, actor_id)
    if len(set(item_ids)) != len(item_ids) or not set(item_ids) <= {
        i.id for i in resources.items if i.owner_id == actor_id
    }:
        raise ValidationError("Critical drops require actual subject-owned objects")
    if not item_ids:
        return resources, encounter, ()
    if not location_id:
        raise ValidationError("Critical drop requires the actual world location")
    # Basic combat has no coordinates. Its existing world-ground field is honest
    # custody; never synthesize a grid square to satisfy an equipment adapter.
    ground = position(encounter, actor) if actor.runtime_position is not None else None
    resources = resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(
                    update={
                        "ready": False,
                        "equipped": False,
                        "container_id": None,
                        "ground": ground,
                        "world_ground_location_id": location_id if ground is None else None,
                    }
                )
                if item.id in item_ids
                else item
                for item in resources.items
            )
        }
    )
    actor = actor.model_copy(
        update={
            "ready_item_ids": tuple(i for i in actor.ready_item_ids if i not in item_ids),
            "hand_bindings": tuple((i, h) for i, h in actor.hand_bindings if i not in item_ids),
        }
    )
    return resources, CombatEngine._replace(encounter, actor), item_ids


def drop_all_held(
    resources: ResourceState, encounter: Encounter, actor_id: str, *, location_id: str
) -> tuple[ResourceState, Encounter, tuple[str, ...]]:
    """B556 row12 is unconditional, even at zero basic damage or penetration."""
    actor = _fighter(encounter, actor_id)
    # Persisted hand bindings are actual held objects, including a held torch.
    # Merely equipped armor is not held and is never included.
    held = tuple(dict.fromkeys(i for i, _ in actor.hand_bindings))
    return drop_held_items(resources, encounter, actor_id, held, location_id=location_id)


def critical_dodge_failure(
    resources: ResourceState, encounter: Encounter, actor_id: str, check: CheckTrace
) -> tuple[ResourceState, Encounter]:
    """B382: a critical Dodge failure falls prone, with no second miss-table roll."""
    if check.outcome is not Outcome.CRITICAL_FAILURE:
        return resources, encounter
    pool = _hp(resources, actor_id)
    assert pool.injury is not None
    resources = resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"injury": pool.injury.model_copy(update={"prone": True})})
                if p.id == pool.id
                else p
                for p in resources.pools
            )
        }
    )
    return resources, CombatEngine._replace(
        encounter, _fighter(encounter, actor_id).model_copy(update={"posture": "prone"})
    )


class InnateCriticalSource(Record):
    source_id: Id
    build_revision: Id
    description_digest: Digest
    specialty: Literal["beam", "breath", "gaze", "projectile"]
    damage_dice: int = Field(ge=1)
    damage_type: DamageType
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    modifier_profile: AttackProfile | None = None
    contagion_vector: Literal["blood", "contact", "digestive", "respiratory"] | None = None
    incubation_seconds: int = Field(default=86400, ge=1, le=31536000)
    source_kind: Literal["natural", "built-in-device", "held-item"] = "natural"
    emitter_arm: Literal["left-arm", "right-arm"] | None = None
    item_id: Id | None = None
    breakage: Literal["ordinary", "resistant", "cheap"] | None = None

    @model_validator(mode="after")
    def real_referents(self) -> InnateCriticalSource:
        if (
            self.modifier_profile is not None
            and self.modifier_profile.armor_divisor != self.armor_divisor
        ):
            raise ValueError("Captured critical penetration must match the approved profile")
        profile = self.modifier_profile
        if profile and (not profile.is_ranged or profile.malediction_range != "none"):
            raise ValueError("This critical source is an ordinary ranged Innate Attack")
        if (
            profile
            and profile.cyclic_interval_seconds is not None
            and (
                profile.cyclic_cycles < 2
                or profile.cyclic_stop_condition is None
                or self.damage_type not in ("burn", "cor", "fat", "tox")
            )
        ):
            raise ValueError(
                "Cyclic critical source requires its approved cycles and stop condition"
            )
        if (
            profile
            and profile.contagious != "none"
            and (self.damage_type != "tox" or self.contagion_vector is None)
        ):
            raise ValueError("Contagious self-hit requires the approved toxic illness vector")
        if self.source_kind == "natural" and (self.item_id or self.breakage):
            raise ValueError("A natural power cannot invent an item or weapon quality")
        if self.source_kind == "held-item" and self.item_id is None:
            raise ValueError("A held source requires its actual item identity")
        if self.breakage is not None and self.item_id is None:
            raise ValueError("Weapon quality requires a real bound source object")
        if self.specialty in ("breath", "gaze") and self.emitter_arm is not None:
            raise ValueError("Breath and Gaze do not establish a weapon arm")
        return self


class InnateCriticalContext(Record):
    id: Id
    campaign_id: Id
    encounter_id: Id
    pending_id: Id
    attacker_id: Id
    target_id: Id
    location_id: Id
    source: InnateCriticalSource
    target_build_revision: Id
    created_at: int = Field(ge=0)
    attacker_ht: int = Field(ge=1)
    attacker_resistance_ht: int | None = Field(default=None, ge=1)
    attacker_dx: int = Field(ge=1)
    attacker_traits: AttackDefenseTraits = AttackDefenseTraits()
    limb_dr: tuple[tuple[Limb, Annotated[int, Field(ge=0)]], ...] = ()
    held_item_ids: tuple[Id, ...] = ()
    hand_bindings: tuple[tuple[Id, Hand], ...] = ()

    @model_validator(mode="after")
    def unique_facts(self) -> InnateCriticalContext:
        if len(dict(self.limb_dr)) != len(self.limb_dr):
            raise ValueError("Critical limb protection must have unique locations")
        if len(set(self.held_item_ids)) != len(self.held_item_ids):
            raise ValueError("Critical held objects must be unique")
        if not {i for i, _ in self.hand_bindings} <= set(self.held_item_ids):
            raise ValueError("Critical hands must bind actual held objects")
        return self


class InnateCriticalCapture(Record):
    kind: Literal["innate-critical-capture-v1"] = "innate-critical-capture-v1"
    context: InnateCriticalContext
    attack: CheckTrace
    table_rolls: tuple[TableRoll, ...] = Field(min_length=1, max_length=3)
    effective_row: int = Field(ge=3, le=18)
    selected_printing: Literal["characters-third/campaigns-fourth"] = (
        "characters-third/campaigns-fourth"
    )
    encounter_round: int = Field(ge=1)
    encounter_turn: int = Field(ge=0)
    subject_hp: Pool
    subject_fp: Pool | None = None
    items: tuple[Item, ...] = ()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class InnateAdjudication(Record):
    """Explicit campaign policy, never represented as an automatic B556 result."""

    principal_id: Id
    policy_id: Id
    reason: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    effect: Literal["lose-balance", "disable-source"]
    duration_seconds: int | None = Field(default=None, ge=1, le=31536000)

    @model_validator(mode="after")
    def bounded_effect(self) -> InnateAdjudication:
        if (self.effect == "disable-source") != (self.duration_seconds is not None):
            raise ValueError("Only a timed source disable requires duration_seconds")
        return self


class InnateCriticalOutcome(Record):
    kind: Literal["innate-critical-outcome-v1"] = "innate-critical-outcome-v1"
    critical_id: Id
    context_digest: Digest
    status: Literal["awaiting-adjudication", "resolved"]
    basis: Literal["selected-printing", "gm-adjudication"] = "selected-printing"
    reason: str | None = None
    effect: (
        Literal[
            "lose-balance",
            "self-hit",
            "strain-arm",
            "unready-item",
            "drop-item",
            "break-item",
            "disable-source",
        ]
        | None
    ) = None
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    damage_dice: tuple[int, ...] = ()
    basic_damage: int = Field(default=0, ge=0)
    resistance_check: CheckTrace | None = None
    cyclic_attack_id: Id | None = None
    injury: int = 0
    fp_lost: int = 0
    dropped_item_ids: tuple[Id, ...] = ()
    injury_checks: tuple[InjuryCheck, ...] = ()
    subject_turn: int = Field(ge=0)
    disabled_until: int | None = Field(default=None, ge=1)
    adjudication: InnateAdjudication | None = None
    continuation_command_id: Id | None = None


def _append(
    resources: ResourceState, event_id: str, target_id: str, record: Record
) -> ResourceState:
    prior = next((e for e in resources.events if e.id == event_id), None)
    if prior is not None:
        if prior.kind != record.model_dump_json() or prior.target_id != target_id:
            raise ConflictError("Committed innate critical evidence cannot be replaced")
        return resources
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=resources.game_time,
                    target_id=target_id,
                    kind=record.model_dump_json(),
                ),
            )
        }
    )


def load_innate_critical(
    resources: ResourceState, critical_id: str
) -> InnateCriticalCapture | None:
    event = next((e for e in resources.events if e.id == "innate-critical:" + critical_id), None)
    if event is None:
        return None
    result = InnateCriticalCapture.model_validate_json(event.kind)
    if result.context.id != critical_id or result.context.attacker_id != event.target_id:
        raise ValidationError("Innate critical event has inconsistent identity")
    return result


def innate_critical_outcome(resources: ResourceState, critical_id: str) -> InnateCriticalOutcome:
    for event in reversed(resources.events):
        if event.id.startswith("innate-critical-outcome:") and event.target_id == critical_id:
            return InnateCriticalOutcome.model_validate_json(event.kind)
    raise ValidationError("Captured innate critical has no resolution stage")


def _bindings(encounter: Encounter, context: InnateCriticalContext) -> None:
    pending = encounter.pending_defense
    if (
        encounter.id != context.encounter_id
        or pending is None
        or (pending.id, pending.attacker_id, pending.defender_id, pending.weapon_id)
        != (context.pending_id, context.attacker_id, context.target_id, context.source.source_id)
    ):
        raise ConflictError("Innate critical does not match the exact pending attack")


def _balance(encounter: Encounter, actor_id: str) -> Encounter:
    actor = _fighter(encounter, actor_id)
    return CombatEngine._replace(
        encounter, actor.model_copy(update={"defense_penalty": min(-2, actor.defense_penalty)})
    )


def require_innate_action(
    resources: ResourceState, encounter: Encounter, actor_id: str, source_id: str | None = None
) -> None:
    """Host admission for ALL actions/free actions; defenses remain permitted at -2.

    The canonical injury turn increments at own-turn start, including Wait. The
    existing combat reset clears defense_penalty at that same boundary.
    """
    _require_innate_action(resources, (encounter,), actor_id, source_id)


def require_innate_actor_action(
    state: PlayState, actor_id: str, source_id: str | None = None
) -> None:
    """Shared voluntary-action guard, including the second after combat ends."""
    _require_innate_action(state.resources, state.encounters, actor_id, source_id)


def _require_innate_action(
    resources: ResourceState,
    encounters: tuple[Encounter, ...],
    actor_id: str,
    source_id: str | None,
) -> None:
    for event in reversed(resources.events):
        if not event.id.startswith("innate-critical-outcome:"):
            continue
        effect = InnateCriticalOutcome.model_validate_json(event.kind)
        if effect.status != "resolved":
            continue
        capture = load_innate_critical(resources, effect.critical_id)
        if capture is None or capture.context.attacker_id != actor_id:
            continue
        pool = _hp(resources, actor_id)
        assert pool.injury is not None
        if effect.effect == "lose-balance":
            encounter = next((e for e in encounters if e.id == capture.context.encounter_id), None)
            if encounter is None or encounter.status == "completed":
                # B363 combat turns are one second. Once the encounter ends,
                # elapsed time supplies the next-turn boundary rather than an
                # injury-turn counter that noncombat actions will never advance.
                blocked = resources.game_time < event.at + 1
            else:
                next_own_turn = encounter.current_actor_id == actor_id and (
                    encounter.round,
                    encounter.turn_index,
                ) != (capture.encounter_round, capture.encounter_turn)
                blocked = pool.injury.turn <= effect.subject_turn and not next_own_turn
            if blocked:
                raise ValidationError("Lost balance prevents even free actions until next turn")
        if (
            effect.effect == "disable-source"
            and capture.context.source.source_id == source_id
            and effect.disabled_until is not None
            and resources.game_time < effect.disabled_until
        ):
            raise ValidationError("The GM-adjudicated innate source is still disabled")
        if effect.effect == "strain-arm" and capture.context.source.source_id == source_id:
            if any(
                w.id == "innate-strain:" + effect.critical_id
                and w.active(now=resources.game_time, full_hp=pool.current == pool.maximum)
                for w in pool.injury.lasting_injuries
            ):
                raise ValidationError("The innate attack's actual emitter arm is disabled")


def _immune_self_hit(capture: InnateCriticalCapture) -> bool:
    injury = capture.subject_hp.injury
    return (
        injury is not None
        and injury.machine
        and capture.context.source.damage_type in ("fat", "tox")
    )


def _self_hit(
    resources: ResourceState,
    encounter: Encounter,
    capture: InnateCriticalCapture,
    result: InnateCriticalOutcome,
    rng: RandomSource,
) -> tuple[ResourceState, Encounter, InnateCriticalOutcome]:
    # deferred: the composed consequence adapter also consumes
    # the flags in this module. These canonical helpers own survival/tolerance.
    from wayfarer.engine.simulation.traits.attack_defense import (
        _apply_survival_traits,
        _bind_target_tolerance,
    )

    context = capture.context
    source = context.source
    if _immune_self_hit(capture):
        return (
            resources,
            encounter,
            result.model_copy(
                update={"effect": "self-hit", "reason": "machine-immune-to-ordinary-damage-type"}
            ),
        )
    body, side = draw_dice(rng, 2)
    location: Limb = (
        ("right-arm" if side <= 3 else "left-arm")
        if body <= 3
        else ("right-leg" if side <= 3 else "left-leg")
    )
    resistance_check = _self_resistance(resources, capture, rng)
    if resistance_check is not None and resistance_check.outcome.succeeded:
        return (
            resources,
            encounter,
            result.model_copy(
                update={
                    "effect": "self-hit",
                    "location": location,
                    "location_dice": (body, side),
                    "resistance_check": resistance_check,
                }
            ),
        )
    dice = draw_dice(rng, source.damage_dice)
    basic = sum(dice) // (2 if capture.effective_row == 6 else 1)
    damage_id = "innate-self-hit:" + context.id
    checks: tuple[InjuryCheck, ...]
    dropped: tuple[str, ...]
    if source.damage_type == "fat":
        assert capture.subject_hp.injury is not None
        penetration = max(
            0,
            basic
            - effective_dr(
                dict(context.limb_dr)[location],
                source.armor_divisor,
                location=location,
                damage_type=source.damage_type,
            ),
        )
        resources, fatigue = apply_fatigue(
            resources,
            FatigueCost(
                id=damage_id,
                actor_id=context.attacker_id,
                expected_revision=resources.revision,
                amount=penetration * context.attacker_traits.injury_multiplier("natural-attacks"),
                attack_damage=True,
            ),
            ht=context.attacker_ht,
            rng=rng,
            system=True,
        )
        injury, fp = fatigue.hp_lost, fatigue.fp_lost
        checks = fatigue.injury.checks if fatigue.injury else ()
        dropped = fatigue.injury.dropped_ready_items if fatigue.injury else ()
        if any(c.reason == "major-wound" and not c.check.outcome.succeeded for c in checks):
            dropped = context.held_item_ids
    else:
        resources, wound = apply_injury(
            _bind_target_tolerance(resources, context.attacker_id, context.attacker_traits),
            Wound(
                id=damage_id,
                actor_id=context.attacker_id,
                expected_revision=resources.revision,
                basic_damage=basic,
                resistance=dict(context.limb_dr)[location],
                damage_type=source.damage_type,
                armor_divisor=source.armor_divisor,
                location=location,
                vulnerability_multiplier=Decimal(
                    context.attacker_traits.injury_multiplier("natural-attacks")
                ),
            ),
            ht=context.attacker_ht,
            dx=context.attacker_dx,
            rng=rng,
            system=True,
            held_item_ids=context.held_item_ids,
            held_item_locations=context.hand_bindings,
        )
        injury, fp, checks, dropped = wound.injury, 0, wound.checks, wound.dropped_ready_items
    resources = _apply_survival_traits(resources, context.attacker_id, context.attacker_traits)
    resources, encounter, dropped = drop_held_items(
        resources, encounter, context.attacker_id, dropped, location_id=context.location_id
    )
    hp = _hp(resources, context.attacker_id)
    if hp.injury and hp.injury.prone:
        encounter = CombatEngine._replace(
            encounter,
            _fighter(encounter, context.attacker_id).model_copy(update={"posture": "prone"}),
        )
    result = result.model_copy(
        update={
            "effect": "self-hit",
            "location": location,
            "location_dice": (body, side),
            "damage_dice": dice,
            "basic_damage": basic,
            "resistance_check": resistance_check,
            "injury": injury,
            "fp_lost": fp,
            "injury_checks": checks,
            "dropped_item_ids": dropped,
        }
    )
    resources, result = _self_hit_effects(resources, capture, result, damage_id)
    return resources, encounter, result


def _self_resistance(
    resources: ResourceState, capture: InnateCriticalCapture, rng: RandomSource
) -> CheckTrace | None:
    context, profile = capture.context, capture.context.source.modifier_profile
    if profile is None or profile.resistance_modifier is None:
        return None
    assert capture.subject_hp.injury is not None
    fitness = capture.subject_hp.injury.physical_traits.fitness
    modifiers = check_modifiers(resources, context.attacker_id, "ht", defensive=True)
    if fitness:
        modifiers += (Modifier(fitness, "Fitness", "B55", "characters-third", ModifierKind.TRAIT),)
    return success_roll(
        PROFILE,
        (context.attacker_resistance_ht or context.attacker_ht) + profile.resistance_modifier,
        modifiers=modifiers,
        rng=rng,
    )


def _self_hit_effects(
    resources: ResourceState,
    capture: InnateCriticalCapture,
    result: InnateCriticalOutcome,
    damage_id: str,
) -> tuple[ResourceState, InnateCriticalOutcome]:
    """Register actual self-damage causes; no fictitious success roll or second wound.

    The persisted host binds a returned occurrence to result.location, so current
    limb protection and actual wounding remain available on each due cycle.
    """
    context = capture.context
    source = context.source
    profile = source.modifier_profile
    if profile is None:
        return resources, result
    symptom_source_id = context.attacker_id + ":" + source.source_id
    cyclic_id = None
    if profile.cyclic_interval_seconds is not None:
        assert profile.cyclic_stop_condition is not None and result.location is not None
        cyclic_id = "innate-self-cyclic:" + hashlib.sha256(context.id.encode()).hexdigest()
        attack_type = CyclicAttack if result.basic_damage else ZeroDamageCyclicAttack
        resources = save_cyclic(
            resources,
            attack_type.model_validate(
                {
                    "id": cyclic_id,
                    "attacker_id": context.attacker_id,
                    "actor_id": context.attacker_id,
                    "attack_id": source.source_id,
                    "basic_damage": result.basic_damage,
                    "damage_dice": source.damage_dice,
                    "damage_type": source.damage_type,
                    "resistance": next(
                        dr for limb, dr in context.limb_dr if limb == result.location
                    ),
                    "armor_divisor": source.armor_divisor,
                    "vulnerability_multiplier": context.attacker_traits.injury_multiplier(
                        "natural-attacks"
                    ),
                    "ht": context.attacker_ht,
                    "resistance_modifier": profile.resistance_modifier,
                    "interval": profile.cyclic_interval_seconds,
                    "remaining": profile.cyclic_cycles - 1,
                    "due": context.created_at + profile.cyclic_interval_seconds,
                    "stop_condition": profile.cyclic_stop_condition,
                    "hp_debt": result.injury,
                    "fp_debt": result.fp_lost,
                    "symptom_spec": profile.symptom_spec,
                    "symptom_source_id": symptom_source_id,
                    "contagious": profile.contagious,
                    "contagion_vector": source.contagion_vector,
                    "incubation_seconds": source.incubation_seconds,
                }
            ),
        )
    amount = result.fp_lost if source.damage_type == "fat" else result.injury
    if profile.symptom_spec is not None and amount:
        resources = register_symptoms(
            resources,
            actor_id=context.attacker_id,
            source_id=symptom_source_id,
            injury_id=damage_id,
            amount=amount,
            pool_id=("fp:" if source.damage_type == "fat" else "hp:") + context.attacker_id,
            spec=profile.symptom_spec,
            restriction_id=cyclic_id,
        )
    return resources, result.model_copy(update={"cyclic_attack_id": cyclic_id})


def resolve_innate_miss(
    resources: ResourceState,
    encounter: Encounter,
    context: InnateCriticalContext,
    attack: CheckTrace,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, Encounter, InnateCriticalOutcome]:
    if not system:
        raise ValidationError("Innate critical resolution requires server authority")
    context = InnateCriticalContext.model_validate(context)
    saved = load_innate_critical(resources, context.id)
    if saved is not None:
        if saved.context != context or saved.attack != attack:
            raise ConflictError("Committed innate critical context cannot change")
        return resources, encounter, innate_critical_outcome(resources, context.id)
    _bindings(encounter, context)
    if context.created_at != resources.game_time:
        raise ConflictError("Innate critical context must capture current chronology")
    if classify_ranged_check(attack).outcome is not Outcome.CRITICAL_FAILURE:
        raise ValidationError("Innate miss table requires a ranged critical failure")
    hp = _hp(resources, context.attacker_id)
    assert hp.injury is not None
    actor = _fighter(encounter, context.attacker_id)
    if set(context.held_item_ids) != {i for i, _ in actor.hand_bindings} or (
        context.hand_bindings != actor.hand_bindings
    ):
        raise ConflictError("Critical context must capture current actual held objects")
    item_ids = set(context.held_item_ids) | (
        {context.source.item_id} if context.source.item_id else set()
    )
    items = tuple(i for i in resources.items if i.id in item_ids)
    if {i.id for i in items} != item_ids or any(i.owner_id != context.attacker_id for i in items):
        raise ValidationError("Critical source/held item identity has no actual owned object")
    table: tuple[TableRoll, ...] = (draw_dice(rng),)
    if sum(table[-1]) in (5, 6):
        table += (draw_dice(rng),)  # Every ranged source rerolls this once, including Breath/Gaze.
    row = sum(table[-1])
    if row in _BREAK and context.source.breakage == "resistant":
        table += (draw_dice(rng),)
        row = sum(table[-1]) if sum(table[-1]) in _BREAK else 9
    capture = InnateCriticalCapture(
        context=context,
        attack=attack,
        table_rolls=table,
        effective_row=row,
        encounter_round=encounter.round,
        encounter_turn=encounter.turn_index,
        subject_hp=hp,
        subject_fp=next((p for p in resources.pools if p.id == "fp:" + context.attacker_id), None),
        items=items,
    )
    resources = _append(resources, "innate-critical:" + context.id, context.attacker_id, capture)
    result = InnateCriticalOutcome(
        critical_id=context.id,
        context_digest=capture.digest,
        status="resolved",
        subject_turn=hp.injury.turn,
    )
    reason = None
    source = context.source
    if row in (7, 13, 16):
        encounter = _balance(encounter, context.attacker_id)
        result = result.model_copy(update={"effect": "lose-balance"})
    elif row in (5, 6):
        if not _immune_self_hit(capture) and (
            hp.injury.anatomy != "human"
            or set(dict(context.limb_dr)) != _LIMBS
            or any(
                missing_location(hp.injury, limb)
                for limb in ("left-arm", "right-arm", "left-leg", "right-leg")
            )
        ):
            reason = "self-hit-requires-original-anatomy-and-protection"
        else:
            resources, encounter, result = _self_hit(resources, encounter, capture, result, rng)
    elif row == 15:
        if (
            source.emitter_arm is None
            or hp.injury.anatomy != "human"
            or missing_location(hp.injury, source.emitter_arm)
        ):
            reason = "natural-source-has-no-established-weapon-arm"
        else:
            resources, _ = apply_location_effect(
                resources,
                DisableLocation(
                    id="innate-strain:" + context.id,
                    actor_id=context.attacker_id,
                    expected_revision=resources.revision,
                    location=source.emitter_arm,
                    duration_seconds=1800,
                ),
                system=True,
            )
            result = result.model_copy(
                update={"effect": "strain-arm", "location": source.emitter_arm}
            )
    else:
        resources, encounter, result, reason = _source_object_miss(
            resources, encounter, capture, result
        )
    if reason:
        result = result.model_copy(update={"status": "awaiting-adjudication", "reason": reason})
        encounter = encounter.model_copy(update={"blocked_reason": "innate-critical:" + context.id})
    resources = _append(resources, "innate-critical-outcome:" + context.id, context.id, result)
    return resources, encounter, result


def _source_object_miss(
    resources: ResourceState,
    encounter: Encounter,
    capture: InnateCriticalCapture,
    result: InnateCriticalOutcome,
) -> tuple[ResourceState, Encounter, InnateCriticalOutcome, str | None]:
    """Only actual source objects can consume the table's break/drop/Ready rows."""
    context, row = capture.context, capture.effective_row
    source = context.source
    item = next((i for i in capture.items if i.id == source.item_id), None)
    broken = row in _BREAK or (row in (9, 10, 11, 14) and source.breakage == "cheap")
    held = source.source_kind == "held-item" and source.item_id in context.held_item_ids
    if (
        item is None
        or (not held and not broken)
        or (broken and (source.breakage is None or item.condition is None))
    ):
        return (
            resources,
            encounter,
            result,
            "natural-source-has-no-printed-break-drop-ready-mapping",
        )
    updates: dict[str, object] = {"ready": False}
    if broken:
        assert item.condition is not None
        updates["condition"] = item.condition.model_copy(update={"disabled": True})
    resources = resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update=updates) if i.id == item.id else i for i in resources.items
            )
        }
    )
    dropped: tuple[str, ...] = ()
    if held and (broken or row in (9, 10, 11, 14)):
        resources, encounter, dropped = drop_held_items(
            resources, encounter, context.attacker_id, (item.id,), location_id=context.location_id
        )
    else:
        actor = _fighter(encounter, context.attacker_id)
        encounter = CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "ready_item_ids": tuple(i for i in actor.ready_item_ids if i != item.id),
                }
            ),
        )
    result = result.model_copy(
        update={
            "effect": "break-item" if broken else "drop-item" if dropped else "unready-item",
            "dropped_item_ids": dropped,
        }
    )
    return resources, encounter, result, None


def continue_innate_miss(
    resources: ResourceState,
    encounter: Encounter,
    *,
    critical_id: str,
    command_id: str,
    context_digest: str,
    adjudication: InnateAdjudication,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, Encounter, InnateCriticalOutcome]:
    """The host authorizes the current seated/trusted GM and commits this atomically.

    No choice replaces dice, selects a different table or supplies an injury amount.
    Losing a purchase after the original roll does not erase the captured attack.
    Captured pools and objects are evidence, not a snapshot to restore or a lock
    against compulsory intervening damage. These choices affect current state.
    """
    if not system:
        raise ValidationError("Innate critical continuation requires GM host authority")
    adjudication = InnateAdjudication.model_validate(adjudication)
    saved = load_innate_critical(resources, critical_id)
    if saved is None or saved.digest != context_digest:
        raise ConflictError("Innate critical continuation context identity changed")
    prior = innate_critical_outcome(resources, critical_id)
    if prior.status == "resolved":
        if prior.continuation_command_id == command_id and prior.adjudication == adjudication:
            return resources, encounter, prior
        raise ConflictError("Innate critical already has its one committed consequence")
    context = saved.context
    _bindings(encounter, context)
    if encounter.blocked_reason != "innate-critical:" + critical_id:
        raise ConflictError("Innate critical is not the current pending continuation")
    if resources.game_time < context.created_at:
        raise ConflictError("Innate critical continuation cannot precede its captured chronology")
    effect = adjudication.effect
    subject_turn = prior.subject_turn
    if effect == "lose-balance":
        current_hp = _hp(resources, context.attacker_id)
        assert current_hp.injury is not None
        subject_turn = current_hp.injury.turn
        encounter = _balance(encounter, context.attacker_id)
    result = prior.model_copy(
        update={
            "status": "resolved",
            "basis": "gm-adjudication",
            "effect": effect,
            "subject_turn": subject_turn,
            "adjudication": adjudication,
            "continuation_command_id": command_id,
            "disabled_until": resources.game_time + adjudication.duration_seconds
            if adjudication.duration_seconds is not None
            else None,
        }
    )
    resources = _append(
        resources, "innate-critical-outcome:continued:" + command_id, critical_id, result
    )
    return resources, encounter.model_copy(update={"blocked_reason": None}), result


class FatigueCriticalKnockdown(Record):
    kind: Literal["fatigue-critical-knockdown-v1"] = "fatigue-critical-knockdown-v1"
    command_id: Id
    actor_id: Id
    penetration: int = Field(ge=0)
    ht: int = Field(ge=1)
    already_checked: bool = False
    basis: Literal["B556-literal-positive-penetration"] = "B556-literal-positive-penetration"
    check: CheckTrace | None = None
    dropped_item_ids: tuple[Id, ...] = ()


def apply_fatigue_major_wound(
    resources: ResourceState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    *,
    penetration: int,
    ht: int,
    rng: RandomSource,
    location_id: str,
    already_checked: bool = False,
    prior_check: CheckTrace | None = None,
    system: bool = False,
) -> tuple[ResourceState, Encounter, FatigueCriticalKnockdown]:
    """B556 rows7/13/14 say positive penetration, without an HP-only exception.

    Apply that literal knockdown requirement to FP damage without inventing HP
    injury, shock or a wound. HP overflow may already have made the same check.
    This explicit interpretation has independent resulting-state tests.
    """
    if not system:
        raise ValidationError("Fatigue critical consequence requires server authority")
    if already_checked != (prior_check is not None):
        raise ValidationError("An overflow major-wound check must retain its actual check evidence")
    event_id = "fatigue-critical-knockdown:" + command_id
    previous = next((e for e in resources.events if e.id == event_id), None)
    if previous:
        saved = FatigueCriticalKnockdown.model_validate_json(previous.kind)
        if (saved.actor_id, saved.penetration, saved.ht, saved.already_checked) != (
            actor_id,
            penetration,
            ht,
            already_checked,
        ) or (already_checked and saved.check != prior_check):
            raise ConflictError("Fatigue critical context cannot change")
        return resources, encounter, saved
    result = FatigueCriticalKnockdown(
        command_id=command_id,
        actor_id=actor_id,
        penetration=penetration,
        ht=ht,
        already_checked=already_checked,
    )
    pool = _hp(resources, actor_id)
    assert pool.injury is not None
    status = pool.injury
    if penetration and not status.machine and (prior_check is not None or not status.incapacitated):
        check = prior_check or success_roll(
            PROFILE,
            ht + status.physical_traits.injury_bonus("major-wound"),
            check_modifiers(resources, actor_id, "ht"),
            rng=rng,
        )
        result = result.model_copy(update={"check": check})
        if not check.outcome.succeeded:
            status = status.model_copy(
                update={
                    "stunned": True,
                    "prone": True,
                    "unconscious": check.margin <= -5 or check.outcome is Outcome.CRITICAL_FAILURE,
                }
            )
            resources = resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"injury": status}) if p.id == pool.id else p
                        for p in resources.pools
                    )
                }
            )
            resources, encounter, dropped = drop_all_held(
                resources, encounter, actor_id, location_id=location_id
            )
            encounter = CombatEngine._replace(
                encounter, _fighter(encounter, actor_id).model_copy(update={"posture": "prone"})
            )
            result = result.model_copy(update={"dropped_item_ids": dropped})
    return _append(resources, event_id, actor_id, result), encounter, result
