"""B349 living-or-sapient scope from approved IQ and trusted anatomy."""

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def target_cap_scope(
    runtime: RulesContext, state: PlayState, target: ValidatedBuild, target_id: str
) -> bool:
    # B23: sapience is based on approved IQ, not temporary symptom penalties.
    assert target.statistics is not None
    if target.statistics.iq >= 6:
        return True
    anatomy = next(actor.body for actor in state.actors if actor.actor_id == target_id)
    purchased = attack_defense_traits(
        target, runtime.reviewer.compiler.definitions
    ).injury_tolerance_profile()
    # A purchased No Blood/etc. profile uses "living" as its damage default;
    # that default cannot establish biology or erase trusted unliving anatomy.
    if purchased is not None and purchased.structure == "unliving":
        return False
    if anatomy is not None:
        tolerance = anatomy.tolerance
        if tolerance is None or tolerance.structure == "living":
            return True
        if tolerance.structure == "unliving":
            return False
    raise ValidationError("Rule of 16 requires known living or sapient target scope")


def require_injury_anatomy(
    runtime: RulesContext, state: PlayState, target: ValidatedBuild, target_id: str
) -> None:
    """A damaging host needs the anatomy consumed by its injury projection."""
    body = next(actor.body for actor in state.actors if actor.actor_id == target_id)
    if (
        body is None
        and attack_defense_traits(
            target, runtime.reviewer.compiler.definitions
        ).injury_tolerance_profile()
        is not None
    ):
        raise ValidationError("Malediction Injury Tolerance requires explicit target anatomy")
