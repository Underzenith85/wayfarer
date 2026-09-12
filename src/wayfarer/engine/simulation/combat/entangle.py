"""Entangling weapon resolution (#354): a hit binds, and the victim struggles free.

Bolas (B181) and Net (B211) are the two listed ranged combat skills whose whole
entry is not "roll to hit and reduce hit points". A hit leaves a durable binding
on the target that restricts its rolls and, where the binding pins the legs, its
movement. Breaking free is an authoritative contest, not a ruling.

Every number comes from the pinned catalog spec on the weapon mode. This module
supplies the procedure and the invariants only.
"""

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.gurps_checks import Contestant, QuickContestTrace, quick_contest
from wayfarer.engine.rules.types.entangle import Entanglement, EntangleSpec
from wayfarer.engine.simulation.combat.combat import Combatant
from wayfarer.errors import ValidationError

ESCAPE_RULE = "gurps.combat.entangling_attack"


def bind(
    target: Combatant,
    spec: EntangleSpec,
    *,
    source_actor_id: str,
    weapon_definition_id: str,
    mode_id: str,
) -> Combatant:
    """Apply a binding to a combatant, keeping the strongest one already held.

    A second net does not stack into a doubled penalty; the tighter binding is
    the one that matters, and the loose one is absorbed by it.
    """
    if source_actor_id == target.actor_id:
        raise ValidationError("An entangling weapon cannot bind its own thrower")
    fresh = Entanglement.bind(
        spec,
        source_actor_id=source_actor_id,
        weapon_definition_id=weapon_definition_id,
        mode_id=mode_id,
    )
    held = target.entangled
    if held is not None and (held.binding_st, -held.attack_penalty) >= (
        fresh.binding_st,
        -fresh.attack_penalty,
    ):
        return target
    return target.model_copy(update={"entangled": fresh})


def attack_penalty(participant: Combatant) -> int:
    return participant.entangled.attack_penalty if participant.entangled else 0


def defense_penalty(participant: Combatant) -> int:
    return participant.entangled.defense_penalty if participant.entangled else 0


def immobilized(participant: Combatant) -> bool:
    return participant.entangled is not None and participant.entangled.immobilizes


def escape(
    profile_id: str,
    participant: Combatant,
    *,
    strength: int,
    skill: int | None,
    rng: RandomSource,
) -> tuple[Combatant, QuickContestTrace]:
    """Contest the binding's ST; a win frees the victim, a loss is recorded.

    `skill` is the victim's level in the binding's pinned escape skill when the
    catalog names one and the victim has a level in it. Without that, the
    contest is the victim's own ST, never an inferred substitute.
    """
    binding = participant.entangled
    if binding is None:
        raise ValidationError("Nothing is binding this actor")
    trace = quick_contest(
        profile_id,
        Contestant(participant.actor_id, strength if skill is None else max(strength, skill)),
        Contestant(f"binding:{participant.actor_id}", binding.binding_st),
        rng=rng,
    )
    if trace.winner == participant.actor_id:
        return participant.model_copy(update={"entangled": None}), trace
    return (
        participant.model_copy(
            update={"entangled": binding.model_copy(update={"attempts": binding.attempts + 1})}
        ),
        trace,
    )
