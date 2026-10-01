"""Internal sensory adjudications, with server-owned Hearing−2 mechanics (B394)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.rules.checks import Modifier, ModifierKind
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.location import deafened
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.sensory_state import (
    CombatSensoryEvidence,
    Explanation,
    NonvisualBasis,
    NonvisualObservation,
    event_id,
    evidence,
    history,
    invalidate,
    scope_digest,
)
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.physical_traits import physical_traits
from wayfarer.engine.simulation.resources import Command, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id


class DeclareCombatSense(Command):
    operation: Literal["declare"] = "declare"
    encounter_id: Id
    target_id: Id
    observation: NonvisualObservation


class HearCombatTarget(Command):
    operation: Literal["hearing"] = "hearing"
    encounter_id: Id
    target_id: Id
    # The current GM attests that this target produces a usable nonvisual cue.
    cue: Explanation
    attack_awareness: Explanation | None = None


class RevokeCombatSense(Command):
    operation: Literal["revoke"] = "revoke"
    encounter_id: Id
    target_id: Id
    explanation: Explanation


CombatSenseCommand = Annotated[
    DeclareCombatSense | HearCombatTarget | RevokeCombatSense, Field(discriminator="operation")
]
ADAPTER: TypeAdapter[CombatSenseCommand] = TypeAdapter(CombatSenseCommand)


def resolve(
    runtime: RulesContext,
    state: PlayState,
    command: CombatSenseCommand,
    *,
    principal_id: str,
) -> tuple[PlayState, CombatSensoryEvidence | None]:
    """Reduce an already authorized host operation; never used as a public endpoint."""
    if state.revision != command.expected_revision:
        raise ConflictError("Sensory adjudication revision is stale")
    encounter = next((e for e in state.encounters if e.id == command.encounter_id), None)
    if encounter is None:
        raise ValidationError("Sensory evidence requires a current encounter and directed pair")
    scope = scope_digest(state, encounter, command.actor_id, command.target_id)
    compiled = build(runtime, state, command.actor_id)
    build(runtime, state, command.target_id)
    stats = compiled.statistics
    if stats is None or stats.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Combat sensory evidence requires the exact Basic Set profile")
    previous = evidence(state, encounter, command.actor_id, command.target_id)
    resources = state.resources
    revision = state.revision + 1
    for prior in history(resources):
        if (prior.encounter_id, prior.observer_id, prior.target_id) == (
            command.encounter_id,
            command.actor_id,
            command.target_id,
        ):
            resources = invalidate(
                resources,
                prior,
                revision,
                "gm-revoked" if isinstance(command, RevokeCombatSense) else "superseded",
            )
    result = None
    if not isinstance(command, RevokeCombatSense):
        hearing = None
        awareness_source_id = None
        basis: NonvisualBasis
        if isinstance(command, HearCombatTarget):
            require_hazard_capacity(state.resources, command.actor_id, "hearing")
            traits = {
                effect.definition_id: effect.levels
                for effect in mundane_trait_effects(compiled, runtime.reviewer.compiler.definitions)
                if effect.family == "senses"
            }
            hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
            if (
                "trait:disadvantage:deafness" in traits
                or hp.injury is not None
                and deafened(
                    hp.injury.lasting_injuries,
                    now=state.resources.game_time,
                    full_hp=hp.current >= hp.maximum,
                )
            ):
                raise ValidationError("Deafness prevents locating a combat target by hearing")
            legacy_acute = physical_traits(state.resources, command.actor_id).sense_bonus("hearing")
            complete_acute = traits.get("trait:advantage:acute-hearing", 0)
            # These catalog identities describe the same B35 advantage, not two
            # independent bonuses. Consume the approved projections once.
            acute = max(legacy_acute, complete_acute)
            acute_id = (
                "trait:advantage:acute-hearing"
                if complete_acute >= legacy_acute
                else "trait:acute-hearing"
            )
            modifiers = (
                Modifier(-2, "Locate unseen foe by hearing", "B394", "campaigns-fourth"),
                *(
                    (
                        Modifier(
                            acute,
                            "Acute Hearing",
                            acute_id,
                            "characters-third",
                            ModifierKind.TRAIT,
                        ),
                    )
                    if acute
                    else ()
                ),
                *(
                    (
                        Modifier(
                            -4,
                            "Hard of Hearing",
                            "trait:disadvantage:hard-of-hearing",
                            "characters-third:B138",
                            ModifierKind.TRAIT,
                        ),
                    )
                    if "trait:disadvantage:hard-of-hearing" in traits
                    else ()
                ),
                *check_modifiers(state.resources, command.actor_id, "per"),
            )
            hearing = success_roll(stats.profile_id, stats.per, modifiers, rng=runtime.rng)
            located = hearing.outcome.succeeded
            # Failure to locate never erases independently justified awareness.
            awareness = command.attack_awareness
            if awareness is None and previous is not None and previous.aware_of_attack:
                awareness = previous.attack_awareness
                awareness_source_id = previous.id
            basis, explanation, exact = "sound", command.cue, None
        else:
            observation = command.observation
            located, awareness = observation.located, observation.attack_awareness
            basis, explanation, exact = (
                observation.basis,
                observation.explanation,
                observation.exact_location,
            )
        result = CombatSensoryEvidence(
            id=event_id(command.id),
            command_id=command.id,
            campaign_id=state.campaign_id,
            encounter_id=encounter.id,
            observer_id=command.actor_id,
            target_id=command.target_id,
            basis=basis,
            explanation=explanation,
            located=located,
            aware_of_attack=awareness is not None,
            attack_awareness=awareness,
            exact_location=exact,
            hearing=hearing,
            awareness_source_id=awareness_source_id,
            declared_by=principal_id,
            declared_revision=revision,
            scope_digest=scope,
        )
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=result.id,
                        at=resources.game_time,
                        target_id=command.actor_id,
                        kind=result.model_dump_json(),
                    ),
                )
            }
        )
    resources = resources.model_copy(update={"revision": revision})
    return state.model_copy(update={"revision": revision, "resources": resources}), result
