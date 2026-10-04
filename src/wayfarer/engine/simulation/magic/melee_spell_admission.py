"""Current source-approved personal Deathtouch and actual combat contact admission."""

import hashlib

from wayfarer.engine.rules.magic.body_control import package as body_package
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.backfires import forgotten, require_settled
from wayfarer.engine.simulation.magic.concentration import (
    require_idle_concentration,
    require_no_held_melee,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    PREFIX,
    CastDeathtouch,
    MeleeSpellContact,
    StaffCarrier,
    cast_event,
    casts,
    held_actor_ids,
)
from wayfarer.engine.simulation.magic.melee_staff_carrier import carrier_digest
from wayfarer.engine.simulation.magic.rituals import require_ordinary_ritual
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.size_forms import require_native_size
from wayfarer.errors import ConflictError, ValidationError


def ready(runtime: RulesContext, state: PlayState, command: CastDeathtouch) -> tuple[int, str, str]:
    if runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
        raise ValidationError("Deathtouch requires the Basic Set profile")
    if not isinstance(command.carrier, StaffCarrier):
        raise ConflictError("Bounded Deathtouch currently requires a manufactured Staff carrier")
    guard(state, command.actor_id, "melee_spell_cast")
    synchronous(state, command.actor_id)
    if len(state.party.groups) > 1 or any(e.status == "active" for e in state.encounters):
        raise ConflictError("Bounded Deathtouch casting requires a sole noncombat party")
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    if (
        actor.available_at > state.resources.game_time
        or actor.conditions
        or not fatigue_ready(state, command.actor_id)
    ):
        raise ConflictError("Deathtouch caster is unavailable")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    if hp.injury is None or hp.injury.incapacitated or hp.injury.stunned or hp.injury.shock:
        raise ConflictError("Deathtouch requires a healthy conscious caster")
    require_settled(state.resources, command.actor_id)
    if forgotten(state.resources, command.actor_id, "deathtouch"):
        raise ConflictError("Deathtouch is currently forgotten")
    compiled = build(runtime, state, command.actor_id)
    purchase = {p.definition_id: p.amount for p in compiled.purchases}
    chain = (
        "itch",
        "spasm",
        "pain",
        "clumsiness",
        "hinder",
        "paralyze-limb",
        "wither-limb",
        "deathtouch",
    )
    expected = {d.id: d for d in body_package().definitions}
    if any(
        runtime.reviewer.compiler.definitions.get("spell:" + key) != expected["spell:" + key]
        for key in chain
    ):
        raise ValidationError("Deathtouch requires exact source-bound Body Control learning")
    if not all(purchase.get("spell:" + s, 0) > 0 for s in chain):
        raise ValidationError("Bounded Deathtouch requires its genuinely purchased source chain")
    skill = next(
        (int(v.value) for v in compiled.sheet.values if v.target == "spell:deathtouch"), None
    )
    if skill is None or not 10 <= skill <= 24:
        raise ValidationError("Bounded Deathtouch requires purchased skill10 through24")
    if not any(
        e.id.startswith(PREFIX + "mana:")
        and e.target_id
        == next(v.location_id for v in state.world.entities if v.id == command.actor_id)
        for e in state.resources.events
    ):
        raise ConflictError("Deathtouch requires authenticated normal mana")
    if command.operation == "start":
        require_idle_concentration(state.resources, command.actor_id)
        require_no_held_melee(state.resources, command.actor_id)
        if command.actor_id in held_actor_ids(state.resources) or any(
            e.actor_id == command.actor_id and e.spell_id == "fireball" and e.phase == "active"
            for e in latest(state.resources).values()
        ):
            raise ConflictError("Cannot cast while holding a Melee or Missile spell")
    if any(
        e.phase == "active" and command.actor_id in (e.actor_id, e.target_id)
        for e in latest(state.resources).values()
    ):
        raise ConflictError("Bounded Deathtouch does not admit other active spells")
    require_ordinary_ritual(runtime, state, command.actor_id, compiled, skill)
    return (
        skill,
        actor.approval.build_revision if actor.approval else "",
        carrier_digest(state, command.actor_id, command.carrier, require_action=True),
    )


def admit_target(
    runtime: RulesContext, state: PlayState, actor_id: str, *, after_physical_contact: bool = False
) -> None:
    require_native_size(state.resources, actor_id)
    compiled = build(runtime, state, actor_id)
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    if (
        actor.body is None
        or actor.body.anatomy != "human"
        or actor.body.tolerance is not None
        or hp.injury is None
        or hp.injury.anatomy != "human"
        or hp.injury.tolerance is not None
        or hp.injury.machine
    ):
        raise ConflictError("Bounded Deathtouch requires an ordinary human victim")
    if hp.injury.dead and not after_physical_contact:
        raise ConflictError("Bounded Deathtouch does not admit an initially dead victim")
    if any(
        any(
            token in p.definition_id
            for token in (
                "injury-tolerance",
                "diffuse",
                "homogenous",
                "homogeneous",
                "unliving",
                "insubstantial",
                "alternate-form",
                "morph",
                "damage-reduction",
                "vulnerability",
            )
        )
        for p in compiled.purchases
    ):
        raise ConflictError("Deathtouch victim has an unsupported injury transformation")
    if any(
        r.actor_id == actor_id and r.status in ("active", "reverting")
        for r in state.transformations.records
    ):
        raise ConflictError("Bounded Deathtouch does not admit transformed victims")


def prepare_contact(
    runtime: RulesContext,
    state: PlayState,
    *,
    command_id: str,
    pending_id: str,
    encounter_id: str,
    attacker_id: str,
    defender_id: str,
    carrier_item_id: str | None,
    mode_id: str | None,
) -> MeleeSpellContact | None:
    charge = next(
        (
            c
            for c in casts(state.resources).values()
            if c.actor_id == attacker_id
            and c.status == "held"
            and (c.carrier.item_id if isinstance(c.carrier, StaffCarrier) else None)
            == carrier_item_id
        ),
        None,
    )
    if charge is None:
        return None
    if charge.completed_at is None or state.resources.game_time <= charge.completed_at:
        raise ConflictError("Melee spell attack must follow its casting turn")
    if (
        carrier_digest(state, attacker_id, charge.carrier, require_action=True)
        != charge.carrier_digest
    ):
        raise ConflictError("Held Melee spell carrier changed")
    admit_target(runtime, state, defender_id)
    event = cast_event(state.resources, charge.cast_id)
    return MeleeSpellContact(
        pending_id=pending_id,
        command_id=command_id,
        encounter_id=encounter_id,
        attacker_id=attacker_id,
        defender_id=defender_id,
        cast_id=charge.cast_id,
        charge_event_id=event.id,
        charge_digest=hashlib.sha256(event.kind.encode()).hexdigest(),
        carrier_item_id=carrier_item_id,
        mode_id=mode_id,
        energy=charge.energy,
    )


def validate_contact(runtime: RulesContext, state: PlayState, contact: MeleeSpellContact) -> None:
    charge = casts(state.resources).get(contact.cast_id)
    if charge is None or charge.status != "held" or charge.actor_id != contact.attacker_id:
        raise ConflictError("Melee contact charge is no longer held")
    event = cast_event(state.resources, charge.cast_id)
    if (
        event.id != contact.charge_event_id
        or hashlib.sha256(event.kind.encode()).hexdigest() != contact.charge_digest
    ):
        raise ConflictError("Melee contact source identity changed")
    if (
        carrier_digest(state, charge.actor_id, charge.carrier, require_action=True)
        != charge.carrier_digest
    ):
        raise ConflictError("Melee contact carrier changed")
    admit_target(runtime, state, contact.defender_id)
