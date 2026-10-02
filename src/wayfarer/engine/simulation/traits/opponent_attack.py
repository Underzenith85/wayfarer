"""B66 attack original before B374 defense, with no retroactive world rewrite."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.attack_roll import (
    AttackRollChoice,
    AttackRollSpec,
)
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter, PendingDefense
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.melee.resolution import resolve_melee
from wayfarer.engine.simulation.combat.ranged.resolution import resolve as resolve_ranged
from wayfarer.engine.simulation.combat.shield_rush import resolve as resolve_rush
from wayfarer.engine.simulation.combat.unarmed.records import PendingUnarmed
from wayfarer.engine.simulation.combat.unarmed.resolution import defend as prepare_unarmed
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.magic.missiles import resolve as resolve_missile
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.composed_host import (
    CurrentAttack,
    attack_target,
    preflight_pending,
)
from wayfarer.engine.simulation.traits.composed_phases import ComposedAttackChoice
from wayfarer.engine.simulation.traits.composed_sources import PROFILE, pending_binding, raw_build
from wayfarer.engine.simulation.traits.malediction_checks import (
    MaledictionPreparation,
    prepare_malediction,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record


class OpponentAttackPreparation(Record):
    campaign_id: Id
    encounter_id: Id
    attack_id: Id
    attacker_id: Id
    owner_id: Id
    current: CurrentAttack | None = None
    captured_attacker: ValidatedBuild | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    route: Literal[
        "composed", "melee", "ranged", "unarmed", "shield-rush", "missile", "malediction"
    ] = "composed"
    ranged: bool = True
    inventory_pending: PendingDefense | None = None
    unarmed_pending: PendingUnarmed | None = None
    original: CheckTrace | None
    spec: AttackRollSpec
    secret: bool = False
    malediction: MaledictionPreparation | None = None

    @model_validator(mode="after")
    def canonical_original(self) -> OpponentAttackPreparation:
        if (self.original is None) != self.secret:
            raise ValueError("Opponent visibility does not match its original-roll state")
        if self.original is not None and self.spec.score(self.original.dice) != self.original:
            raise ValueError("Opponent original differs from its captured canonical target")
        return self


def _context(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    owner_id: str,
    resist: bool | None,
) -> CurrentAttack:
    pending = encounter.pending_defense
    if pending is None or pending.composed_attack_id is None:
        raise ValidationError("Opponent Luck currently requires an approved composed attack")
    if pending.defender_id != owner_id or pending.attacker_id == owner_id:
        raise ValidationError("Opponent Luck requires an attack against its owner")
    if pending.attack_roll is not None or encounter.blocked_reason is not None:
        raise ConflictError("A committed attack roll cannot be reopened for Luck")
    binding = pending_binding(state.resources, encounter, pending)
    if binding.stage == "resistance":
        if resist is None:
            raise ValidationError("Malediction requires its separately declared resistance choice")
        actor = next(p for p in encounter.participants if p.actor_id == binding.attacker_id)
        if not actor.maneuver_state.concentrating:
            raise ConflictError("Malediction concentration ended before its caster roll")
    elif resist is not None:
        raise ValidationError("An ordinary attack cannot declare a Malediction resistance")
    return preflight_pending(runtime, state, encounter)


def prepare_opponent_attack(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    owner_id: str,
    attack_id: str,
    secret: bool = False,
    resist: bool | None = None,
) -> OpponentAttackPreparation:
    if runtime.reviewer.compiler.statistics_profile != PROFILE:
        raise ValidationError("Opponent Luck requires the exact Basic Set profile")
    if encounter.pending_unarmed is not None:
        if resist is not None:
            raise ValidationError("Unarmed attack is not a Malediction resistance")
        return _prepare_unarmed(
            runtime, state, encounter, owner_id=owner_id, attack_id=attack_id, secret=secret
        )
    pending = encounter.pending_defense
    if pending is None or pending.defender_id != owner_id or pending.attacker_id == owner_id:
        raise ValidationError("Opponent Luck requires an attack against its owner")
    if pending.id != attack_id:
        raise ConflictError("Opponent attack identity changed")
    if pending.attack_roll is not None or encounter.blocked_reason is not None:
        raise ConflictError("A committed attack roll cannot be reopened for Luck")
    if pending.composed_attack_id is not None:
        current = _context(runtime, state, encounter, owner_id, resist)
        if resist is not None:
            prepared = prepare_malediction(
                state.resources,
                current.source.actor_id,
                build(runtime, state, current.source.actor_id),
                build(runtime, state, owner_id, defensive=True),
                current.source.profile,
                current.context.model_copy(update={"resist": resist}),
                runtime.reviewer.compiler.definitions,
                current_conditions=True,
            )
            return OpponentAttackPreparation(
                campaign_id=state.campaign_id,
                encounter_id=encounter.id,
                attack_id=pending.id,
                attacker_id=pending.attacker_id,
                owner_id=owner_id,
                current=current,
                route="malediction",
                ranged=False,
                spec=prepared.spec,
                original=None if secret else prepared.spec.roll(runtime.rng),
                secret=secret,
                malediction=prepared,
            )
        spec = AttackRollSpec(
            profile_id=PROFILE,
            target=attack_target(runtime, state, encounter, current),
            modifiers=check_modifiers(state.resources, current.source.actor_id, "dx"),
            ranged=True,
        )
        return OpponentAttackPreparation(
            campaign_id=state.campaign_id,
            encounter_id=encounter.id,
            attack_id=pending.id,
            attacker_id=pending.attacker_id,
            owner_id=owner_id,
            current=current,
            original=None if secret else spec.roll(runtime.rng),
            spec=spec,
            secret=secret,
        )
    if resist is not None:
        raise ValidationError("Resistance selection requires an approved Malediction source")
    route, spec = _inventory_spec(runtime, state, encounter)
    return OpponentAttackPreparation(
        campaign_id=state.campaign_id,
        encounter_id=encounter.id,
        attack_id=pending.id,
        attacker_id=pending.attacker_id,
        owner_id=owner_id,
        route=route,
        ranged=spec.ranged,
        inventory_pending=pending,
        captured_attacker=raw_build(runtime, state, pending.attacker_id),
        original=None if secret else spec.roll(runtime.rng),
        spec=spec,
        secret=secret,
    )


def _prepare_unarmed(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    owner_id: str,
    attack_id: str,
    secret: bool,
) -> OpponentAttackPreparation:
    pending = encounter.pending_unarmed
    assert pending is not None
    if pending.id != attack_id or encounter.blocked_reason is not None:
        raise ConflictError("Unarmed attack is no longer the selected unresolved attack")
    if pending.target_id != owner_id or pending.actor_id == owner_id:
        raise ValidationError("Opponent Luck requires an attack against its owner")
    spec = prepare_unarmed(
        runtime,
        state,
        encounter,
        ChooseDefense(
            id=pending.id,
            actor_id=owner_id,
            expected_revision=state.revision,
            encounter_id=encounter.id,
            defense="none",
        ),
        prepare_only=True,
    )
    return OpponentAttackPreparation(
        campaign_id=state.campaign_id,
        encounter_id=encounter.id,
        attack_id=pending.id,
        attacker_id=pending.actor_id,
        owner_id=owner_id,
        route="unarmed",
        ranged=False,
        unarmed_pending=pending,
        captured_attacker=raw_build(runtime, state, pending.actor_id),
        original=None if secret else spec.roll(runtime.rng),
        spec=spec,
        secret=secret,
    )


def _inventory_spec(
    runtime: RulesContext, state: PlayState, encounter: Encounter
) -> tuple[Literal["melee", "ranged", "shield-rush", "missile"], AttackRollSpec]:
    pending = encounter.pending_defense
    assert pending is not None
    if pending.protected_defender_id:
        raise ValidationError("This attack requires its separate source-bound Luck adapter")
    if pending.spell_cast_id is not None:
        return "missile", resolve_missile(
            runtime, state, encounter, "none", None, None, None, prepare_only=True
        )
    if pending.shield_rush:
        return "shield-rush", resolve_rush(
            runtime, state, encounter, "none", None, prepare_only=True
        )
    weapon = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if isinstance(weapon, RangedMode):
        return "ranged", resolve_ranged(
            runtime,
            state,
            encounter,
            weapon,
            "none",
            None,
            second_defense=None,
            second_item_id=None,
            prepare_only=True,
        )
    return "melee", resolve_melee(runtime, state, encounter, "none", None, prepare_only=True)


def validate_opponent_attack(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    prepared: OpponentAttackPreparation,
) -> None:
    if prepared.campaign_id != state.campaign_id or prepared.encounter_id != encounter.id:
        raise ConflictError("Opponent attack belongs to another campaign or encounter")
    if prepared.secret:
        current = prepare_opponent_attack(
            runtime,
            state,
            encounter,
            owner_id=prepared.owner_id,
            attack_id=prepared.attack_id,
            secret=True,
            resist=prepared.malediction.resist if prepared.malediction is not None else None,
        )
        if current != prepared:
            raise ConflictError("Secret opponent attack context changed before its dice")
        return
    assert prepared.original is not None
    if prepared.route == "unarmed":
        if (
            encounter.pending_unarmed != prepared.unarmed_pending
            or encounter.blocked_reason is not None
        ):
            raise ConflictError("Captured unarmed attack declaration changed")
        if rescore_opponent_attack(prepared, prepared.original.dice) != prepared.original:
            raise ValidationError("Unarmed original is not a canonical attack check")
        return
    pending = encounter.pending_defense
    if pending is None or pending.attack_roll is not None or encounter.blocked_reason is not None:
        raise ConflictError("Opponent attack is already committed or no longer pending")
    if pending.id != prepared.attack_id or pending.attacker_id != prepared.attacker_id:
        raise ConflictError("Opponent attack identity changed")
    if pending.defender_id != prepared.owner_id:
        raise ConflictError("Opponent attack victim changed")
    if prepared.current is not None:
        binding = pending_binding(state.resources, encounter, pending)
        if (binding.target_id, binding.source) != (prepared.owner_id, prepared.current.source):
            raise ConflictError("Opponent attack binding changed")
    elif prepared.inventory_pending != pending:
        raise ConflictError("Captured inventory attack declaration changed")
    if rescore_opponent_attack(prepared, prepared.original.dice) != prepared.original:
        raise ValidationError("Opponent original is not a canonical attack check")


def rescore_opponent_attack(
    prepared: OpponentAttackPreparation, dice: tuple[int, ...]
) -> CheckTrace:
    if len(dice) != 3:
        raise ValidationError("An ordinary opponent attack requires exactly three dice")
    return prepared.spec.score((dice[0], dice[1], dice[2]))


def captured_choice(
    prepared: OpponentAttackPreparation, selected: CheckTrace, *, original: CheckTrace | None = None
) -> AttackRollChoice:
    if rescore_opponent_attack(prepared, selected.dice) != selected:
        raise ValidationError("Selected attack differs from its captured original context")
    original = original or prepared.original
    if original is None:
        raise ValidationError("Captured attack choice requires its actual first attempt")
    if prepared.current is not None:
        return ComposedAttackChoice(
            current=prepared.current,
            original=original,
            selected=selected,
            ranged=prepared.ranged,
            malediction=prepared.malediction,
        )
    return AttackRollChoice(
        original=original,
        selected=selected,
        ranged=prepared.ranged,
        attacker_id=prepared.attacker_id if prepared.captured_attacker is not None else None,
        captured_attacker=prepared.captured_attacker,
    )
