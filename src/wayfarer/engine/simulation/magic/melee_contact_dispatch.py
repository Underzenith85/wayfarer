"""Route actual held contact without changing historical generation-one records."""

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.location import Hand, HitLocation, HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.generations import paralyze_buckler_drop_enabled
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.magic.limb_spell_state import (
    ParalyzeLimbContact,
    ParalyzeLimbContactResult,
)
from wayfarer.engine.simulation.magic.limb_spell_state import (
    attach_contact as attach_limb,
)
from wayfarer.engine.simulation.magic.limb_spell_state import (
    read_contact as read_limb,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    MeleeSpellContact,
    MeleeSpellContactResult,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    attach_contact as attach_melee,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    read_contact as read_melee,
)
from wayfarer.engine.simulation.magic.wither_spell_state import (
    WitherLimbContact,
    WitherLimbContactResult,
)
from wayfarer.engine.simulation.magic.wither_spell_state import (
    attach_contact as attach_wither,
)
from wayfarer.engine.simulation.magic.wither_spell_state import (
    read_contact as read_wither,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError

Contact = MeleeSpellContact | ParalyzeLimbContact | WitherLimbContact
ContactResult = MeleeSpellContactResult | ParalyzeLimbContactResult | WitherLimbContactResult


def is_limb_contact(contact: Contact | None) -> bool:
    return isinstance(contact, (ParalyzeLimbContact, WitherLimbContact))


def _unique_contact(contacts: tuple[Contact | None, ...], message: str) -> Contact | None:
    present = tuple(contact for contact in contacts if contact is not None)
    if len(present) > 1:
        raise ConflictError(message)
    return present[0] if present else None


def read_contact(resources: ResourceState, pending_id: str) -> Contact | None:
    return _unique_contact(
        (
            read_melee(resources, pending_id),
            read_limb(resources, pending_id),
            read_wither(resources, pending_id),
        ),
        "One actual contact cannot carry multiple held spells",
    )


def attach_contact(resources: ResourceState, pending_id: str, contact: Contact) -> ResourceState:
    if read_contact(resources, pending_id) is not None:
        raise ConflictError("Actual contact identity is already bound")
    if isinstance(contact, ParalyzeLimbContact):
        return attach_limb(resources, pending_id, contact)
    if isinstance(contact, WitherLimbContact):
        return attach_wither(resources, pending_id, contact)
    return attach_melee(resources, pending_id, contact)


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
    requested_location: HitLocation | None = None,
) -> Contact | None:
    # deferred: admission uses canonical modes sharing the combat context cycle.
    from wayfarer.engine.simulation.magic.limb_spell_admission import prepare_contact as limb

    # deferred: admission uses canonical modes sharing the combat context cycle.
    from wayfarer.engine.simulation.magic.melee_spell_admission import prepare_contact as old

    # deferred: admission uses canonical modes sharing the combat context cycle.
    from wayfarer.engine.simulation.magic.wither_spell_admission import prepare_contact as wither

    previous = old(
        runtime,
        state,
        command_id=command_id,
        pending_id=pending_id,
        encounter_id=encounter_id,
        attacker_id=attacker_id,
        defender_id=defender_id,
        carrier_item_id=carrier_item_id,
        mode_id=mode_id,
    )
    newer = limb(
        runtime,
        state,
        command_id=command_id,
        pending_id=pending_id,
        encounter_id=encounter_id,
        attacker_id=attacker_id,
        defender_id=defender_id,
        carrier_item_id=carrier_item_id,
        mode_id=mode_id,
        requested_location=requested_location,
    )
    if newer is not None and paralyze_buckler_drop_enabled():
        newer = newer.model_copy(update={"generation": 4})
    withering = wither(
        runtime,
        state,
        command_id=command_id,
        pending_id=pending_id,
        encounter_id=encounter_id,
        attacker_id=attacker_id,
        defender_id=defender_id,
        carrier_item_id=carrier_item_id,
        mode_id=mode_id,
        requested_location=requested_location,
    )
    return _unique_contact(
        (previous, newer, withering), "One actual attack cannot carry multiple held spells"
    )


def validate_contact(runtime: RulesContext, state: PlayState, contact: Contact) -> None:
    if isinstance(contact, WitherLimbContact):
        # deferred: admission uses canonical modes sharing the combat context cycle.
        from wayfarer.engine.simulation.magic.wither_spell_admission import (
            validate_contact as wither,
        )

        wither(runtime, state, contact)
        return
    if isinstance(contact, ParalyzeLimbContact):
        # deferred: admission uses canonical modes sharing the combat context cycle.
        from wayfarer.engine.simulation.magic.limb_spell_admission import validate_contact as limb

        limb(runtime, state, contact)
    else:
        # deferred: admission uses canonical modes sharing the combat context cycle.
        from wayfarer.engine.simulation.magic.melee_spell_admission import validate_contact as old

        old(runtime, state, contact)


def finish_contact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    contact: Contact,
    *,
    ordinary_hit: bool,
    actual_defense: Defense,
    defense_check: CheckTrace | None,
    defense_implement_id: str | None,
    critical_row: int | None = None,
    resolved_location: HumanLocation | None = None,
    defense_hand: Hand | None = None,
) -> tuple[PlayState, Encounter, ContactResult]:
    if isinstance(contact, WitherLimbContact):
        # deferred: effect settlement shares the canonical combat context cycle.
        from wayfarer.engine.simulation.magic.wither_spell_transitions import (
            finish_contact as wither,
        )

        return wither(
            runtime,
            state,
            encounter,
            contact,
            ordinary_hit=ordinary_hit,
            actual_defense=actual_defense,
            defense_check=defense_check,
            defense_implement_id=defense_implement_id,
            critical_row=critical_row,
            resolved_location=resolved_location,
            defense_hand=defense_hand,
        )
    if isinstance(contact, ParalyzeLimbContact):
        # deferred: effect settlement shares the canonical combat context cycle.
        from wayfarer.engine.simulation.magic.limb_spell_transitions import finish_contact as limb

        return limb(
            runtime,
            state,
            encounter,
            contact,
            ordinary_hit=ordinary_hit,
            actual_defense=actual_defense,
            defense_check=defense_check,
            defense_implement_id=defense_implement_id,
            critical_row=critical_row,
            resolved_location=resolved_location,
            defense_hand=defense_hand,
        )
    # deferred: effect settlement shares the canonical combat context cycle.
    from wayfarer.engine.simulation.magic.melee_spell_transitions import finish_contact as old

    return old(
        runtime,
        state,
        encounter,
        contact,
        ordinary_hit=ordinary_hit,
        actual_defense=actual_defense,
        defense_check=defense_check,
        defense_implement_id=defense_implement_id,
        critical_row=critical_row,
    )
