"""Source-current B348 attempts without inventing an unattempted resistance.

The historical two-roll API and receipt remain in gurps_checks. Current
producers explicitly select this API and retain their independent origin.
"""

from dataclasses import dataclass
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, RandomSource, draw_dice
from wayfarer.engine.rules.conformance import require_capabilities
from wayfarer.engine.rules.gurps_checks import (
    QUICK_CONTEST_CAPABILITY,
    RESISTANCE_CAPABILITY,
    RULE_OF_16_PROFILES,
    SUCCESS_CAPABILITIES,
    Contestant,
    ResistanceTrace,
    _decide_resistance,
    _score,
    replay_resistance,
    rule_of_16_modifier,
)
from wayfarer.errors import ValidationError


@dataclass(frozen=True, slots=True)
class ResistancePreparation:
    profile_id: str
    attacker: Contestant
    resister: Contestant
    rule_of_16: bool
    cap_modifiers: tuple[Modifier, ...]

    @property
    def effective_target(self) -> int:
        return self.attacker.base_target + sum(
            m.value for m in self.attacker.modifiers + self.cap_modifiers
        )


def prepare_resistance_attempt(
    profile_id: str,
    attacker: Contestant,
    resister: Contestant,
    *,
    rule_of_16: bool,
) -> ResistancePreparation:
    """Pure voluntary-attacker admission; no floor applies to the subject."""
    if profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Current resistance attempts require exact Basic Set rules")
    require_capabilities(
        profile_id, (*SUCCESS_CAPABILITIES, QUICK_CONTEST_CAPABILITY, RESISTANCE_CAPABILITY)
    )
    if type(rule_of_16) is not bool or rule_of_16 and profile_id not in RULE_OF_16_PROFILES:
        raise ValidationError("Current resistance has invalid Rule of 16 scope")
    if (
        not isinstance(attacker.id, str)
        or not isinstance(resister.id, str)
        or not attacker.id
        or not resister.id
        or attacker.id == resister.id
        or type(attacker.base_target) is not int
        or type(resister.base_target) is not int
    ):
        raise ValidationError("Current resistance requires distinct, declared contestants")
    if any(type(m.value) is not int for c in (attacker, resister) for m in c.modifiers):
        raise ValidationError("Current resistance requires declared integer modifiers")
    if any(m.kind is ModifierKind.RULE_OF_16 for c in (attacker, resister) for m in c.modifiers):
        raise ValidationError("Current resistance computes its own Rule of 16 modifier")
    prepared = ResistancePreparation(
        profile_id,
        attacker,
        resister,
        rule_of_16,
        rule_of_16_modifier(profile_id, attacker, resister) if rule_of_16 else (),
    )
    if prepared.effective_target < 3:
        raise ValidationError("Voluntary resisted attack requires effective skill at least 3")
    return prepared


def _require_dice(dice: tuple[int, int, int]) -> None:
    if (
        type(dice) is not tuple
        or len(dice) != 3
        or any(type(die) is not int or not 1 <= die <= 6 for die in dice)
    ):
        raise ValidationError("Current resistance requires exactly three actual integer d6")


def _attack(prepared: ResistancePreparation, dice: tuple[int, int, int]) -> CheckTrace:
    _require_dice(dice)
    return _score(
        prepared.profile_id,
        RESISTANCE_CAPABILITY,
        prepared.attacker,
        dice,
        prepared.cap_modifiers,
    )


@dataclass(frozen=True, slots=True)
class FailedResistanceAttempt:
    profile_id: str
    declared_attacker: Contestant
    declared_resister: Contestant
    attacker: CheckTrace
    rule_of_16: bool
    kind: Literal["attacker-failed"] = "attacker-failed"
    affected: Literal[False] = False

    def __post_init__(self) -> None:
        prepared = prepare_resistance_attempt(
            self.profile_id,
            self.declared_attacker,
            self.declared_resister,
            rule_of_16=self.rule_of_16,
        )
        if (
            self.kind != "attacker-failed"
            or self.affected is not False
            or self.attacker.outcome.succeeded
            or _attack(prepared, self.attacker.dice) != self.attacker
        ):
            raise ValidationError("Invalid failed resistance attempt")


ResistanceAttempt = ResistanceTrace | FailedResistanceAttempt


def _failed(prepared: ResistancePreparation, attack: CheckTrace) -> FailedResistanceAttempt:
    return FailedResistanceAttempt(
        prepared.profile_id, prepared.attacker, prepared.resister, attack, prepared.rule_of_16
    )


def resolve_resistance_attempt(
    prepared: ResistancePreparation,
    *,
    attacker_dice: tuple[int, int, int],
    resister_dice: tuple[int, int, int] | None = None,
) -> ResistanceAttempt:
    """Adapt actual supplied dice; an optional actual defender roll is legal."""
    checked = prepare_resistance_attempt(
        prepared.profile_id,
        prepared.attacker,
        prepared.resister,
        rule_of_16=prepared.rule_of_16,
    )
    if checked != prepared:
        raise ValidationError("Current resistance preparation changed")
    attack = _attack(prepared, attacker_dice)
    if resister_dice is None:
        if attack.outcome.succeeded:
            raise ValidationError("Successful resisted attack requires actual subject dice")
        return _failed(prepared, attack)
    _require_dice(resister_dice)
    return _decide_resistance(
        prepared.profile_id,
        prepared.attacker,
        prepared.resister,
        attacker_dice,
        resister_dice,
        prepared.rule_of_16,
    )


def resistance_attempt(prepared: ResistancePreparation, *, rng: RandomSource) -> ResistanceAttempt:
    """Draw the subject only if the actual voluntary attack succeeded."""
    checked = prepare_resistance_attempt(
        prepared.profile_id,
        prepared.attacker,
        prepared.resister,
        rule_of_16=prepared.rule_of_16,
    )
    if checked != prepared:
        raise ValidationError("Current resistance preparation changed")
    attacker_dice = draw_dice(rng)
    attack = _attack(prepared, attacker_dice)
    if not attack.outcome.succeeded:
        return _failed(prepared, attack)
    return resolve_resistance_attempt(
        prepared, attacker_dice=attacker_dice, resister_dice=draw_dice(rng)
    )


def replay_resistance_attempt(trace: ResistanceAttempt) -> ResistanceAttempt:
    if isinstance(trace, ResistanceTrace):
        _require_dice(trace.attacker.dice)
        _require_dice(trace.resister.dice)
        historical = replay_resistance(trace)
        if historical != trace:
            raise ValidationError("Recorded resistance attempt changed")
        return historical
    replayed = _failed(
        prepare_resistance_attempt(
            trace.profile_id,
            trace.declared_attacker,
            trace.declared_resister,
            rule_of_16=trace.rule_of_16,
        ),
        trace.attacker,
    )
    if replayed != trace:
        raise ValidationError("Recorded failed resistance attempt changed")
    return replayed


def resistance_attempt_checks(trace: ResistanceAttempt) -> tuple[CheckTrace, ...]:
    return (
        (trace.attacker, trace.resister)
        if isinstance(trace, ResistanceTrace)
        else (trace.attacker,)
    )


def validate_current_resistance_attempt(
    prepared: ResistancePreparation,
    trace: ResistanceAttempt,
    *,
    allow_optional_subject: bool = False,
) -> ResistanceAttempt:
    """Bind current receipts to captured producer facts, never inferred old dice."""
    if type(allow_optional_subject) is not bool:
        raise ValidationError("Current optional resistance policy must be a boolean")
    if type(trace.rule_of_16) is not bool or type(trace.affected) is not bool:
        raise ValidationError("Current resistance receipt requires actual boolean facts")
    if (
        isinstance(trace, ResistanceTrace)
        and not trace.attacker.outcome.succeeded
        and not allow_optional_subject
    ):
        raise ValidationError("Current RNG resistance cannot invent an optional subject attempt")
    actual = resolve_resistance_attempt(
        prepared,
        attacker_dice=trace.attacker.dice,
        resister_dice=trace.resister.dice if isinstance(trace, ResistanceTrace) else None,
    )
    if actual != trace:
        raise ValidationError("Current resistance receipt differs from its captured preparation")
    return actual
