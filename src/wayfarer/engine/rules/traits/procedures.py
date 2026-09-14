"""Typed cross-catalog trait procedures from Characters B33-34, B46, B61, B120-121.

States and receipts are frozen Records. Transitions are pure and random procedures
require an explicit RandomSource, making persisted results replayable without rerolls.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import Final, Literal, cast

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import Modifier, RandomSource
from wayfarer.engine.rules.social.gurps_social import self_control_roll
from wayfarer.engine.rules.traits.base import TraitOptions, TraitRules, cost
from wayfarer.errors import ValidationError
from wayfarer.models import Record

AdvantageClass = Literal["mundane", "exotic", "supernatural"]
CANONICAL_ORIGINS: Final = frozenset(
    {"biological", "chi", "cosmic", "divine", "high-tech", "magic", "psionic", "spirit"}
)


class AdvantageOrigin(Record):
    advantage_id: str = Field(min_length=1, max_length=200)
    classification: AdvantageClass
    origin: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def required_for_unusual_traits(self) -> AdvantageOrigin:
        if self.classification in ("exotic", "supernatural") and self.origin is None:
            raise ValueError("Exotic and supernatural advantages require an in-game origin")
        return self

    def affected_by(self, origin: str) -> bool:
        return self.origin == origin


class CampaignOrigins(Record):
    allowed: tuple[str, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_nonempty_ids(self) -> CampaignOrigins:
        if len(set(self.allowed)) != len(self.allowed) or any(not value for value in self.allowed):
            raise ValueError("Campaign origins require unique nonempty IDs")
        return self


def choose_advantage_origin(
    advantage_id: str,
    classification: AdvantageClass,
    origin: str | None,
    campaign: CampaignOrigins,
) -> AdvantageOrigin:
    if origin is not None and origin not in campaign.allowed:
        raise ValidationError("Advantage origin is unavailable in this campaign")
    return AdvantageOrigin(advantage_id=advantage_id, classification=classification, origin=origin)


PotentialKind = Literal["heir", "schrodinger", "secret"]


class PotentialAdvantage(Record):
    advantage_id: str = Field(min_length=1, max_length=200)
    kind: PotentialKind
    condition_id: str = Field(min_length=1, max_length=200)
    full_cost: int = Field(ge=1, le=10000)
    paid_points: int = Field(ge=1, le=10000)
    status: Literal["reserved", "payment-due", "complete"] = "reserved"

    @model_validator(mode="after")
    def valid_accounting(self) -> PotentialAdvantage:
        expected = self.full_cost if self.status == "complete" else _ceil_div(self.full_cost, 2)
        if self.paid_points != expected:
            raise ValueError("Potential-advantage payments do not match its state")
        return self

    @property
    def points_due(self) -> int:
        return self.full_cost - self.paid_points

    @property
    def benefit(self) -> Literal["partial", "none", "uncontrolled", "full"]:
        if self.status == "complete":
            return "full"
        if self.kind == "heir":
            return "partial"
        return "none" if self.kind == "schrodinger" else "uncontrolled"


class PotentialPayment(Record):
    state: PotentialAdvantage
    points_spent: int = Field(ge=0)
    points_remaining: int = Field(ge=0)


def reserve_potential(
    advantage_id: str, kind: PotentialKind, condition_id: str, full_cost: int
) -> PotentialAdvantage:
    if type(full_cost) is not int or not 1 <= full_cost <= 10000:
        raise ValidationError("Potential advantage requires a positive catalog cost")
    return PotentialAdvantage(
        advantage_id=advantage_id,
        kind=kind,
        condition_id=condition_id,
        full_cost=full_cost,
        paid_points=_ceil_div(full_cost, 2),
    )


def meet_potential_condition(state: PotentialAdvantage, condition_id: str) -> PotentialAdvantage:
    if state.status != "reserved" or condition_id != state.condition_id:
        raise ValidationError("Potential-advantage condition is absent or already resolved")
    return state.model_copy(update={"status": "payment-due"})


def pay_potential(state: PotentialAdvantage, available_points: int) -> PotentialPayment:
    if state.status != "payment-due":
        raise ValidationError("Potential advantage is not ready for final payment")
    if type(available_points) is not int or available_points < state.points_due:
        raise ValidationError("Potential advantage must be paid in full when acquired")
    spent = state.points_due
    return PotentialPayment(
        state=state.model_copy(update={"paid_points": state.full_cost, "status": "complete"}),
        points_spent=spent,
        points_remaining=available_points - spent,
    )


ActivationMode = Literal["always-on", "switchable", "attack-only"]


class AdvantageActivation(Record):
    advantage_id: str = Field(min_length=1, max_length=200)
    mode: ActivationMode
    active: bool

    @model_validator(mode="after")
    def exact_state(self) -> AdvantageActivation:
        if self.mode == "always-on" and not self.active:
            raise ValueError("An always-on advantage cannot be inactive")
        if self.mode == "attack-only" and self.active:
            raise ValueError("An attack advantage cannot remain continuously active")
        return self


class ActivationReceipt(Record):
    state: AdvantageActivation
    maneuver: Literal["ready", "attack"]
    seconds: Literal[1] = 1
    used_for_attack: bool = False


def default_activation(advantage_id: str, mode: ActivationMode) -> AdvantageActivation:
    return AdvantageActivation(advantage_id=advantage_id, mode=mode, active=mode != "attack-only")


def change_activation(
    state: AdvantageActivation,
    action: Literal["turn-on", "turn-off", "attack"],
    maneuver: Literal["ready", "attack"],
) -> ActivationReceipt:
    if state.mode == "always-on":
        raise ValidationError("Always-on advantages cannot be switched")
    if state.mode == "switchable":
        if action == "attack" or maneuver != "ready":
            raise ValidationError("Switching an advantage requires a one-second Ready maneuver")
        return ActivationReceipt(
            state=state.model_copy(update={"active": action == "turn-on"}), maneuver=maneuver
        )
    if action != "attack" or maneuver != "attack":
        raise ValidationError("Attack advantages are on only during a one-second Attack maneuver")
    return ActivationReceipt(state=state, maneuver=maneuver, used_for_attack=True)


DefenseRarity = Literal["very-common", "common", "occasional", "rare"]
LIMITED_DEFENSE_PERCENT: Final = {
    "very-common": -20,
    "common": -40,
    "occasional": -60,
    "rare": -80,
}


class LimitedDefense(Record):
    category_id: str = Field(min_length=1, max_length=200)
    rarity: DefenseRarity

    @property
    def percent(self) -> int:
        return LIMITED_DEFENSE_PERCENT[self.rarity]

    def protects(self, *, direct_damage: bool, categories: tuple[str, ...]) -> bool:
        return direct_damage and self.category_id in categories

    def limited_cost(self, unrestricted_cost: int) -> int:
        if type(unrestricted_cost) is not int or unrestricted_cost < 1:
            raise ValidationError("Limited defense requires a positive unrestricted cost")
        return int(
            (Decimal(unrestricted_cost) * Decimal(100 + self.percent) / 100).to_integral_value(
                rounding=ROUND_CEILING
            )
        )


AttackKind = Literal["affliction", "binding", "innate-attack", "striker"]


class AlternativeAttack(Record):
    attack_id: str = Field(min_length=1, max_length=200)
    kind: AttackKind
    point_cost: int = Field(ge=1, le=100000)
    has_link: bool = False
    striker_approved: bool = False

    @model_validator(mode="after")
    def permitted_member(self) -> AlternativeAttack:
        if self.has_link:
            raise ValueError("Alternative attacks cannot use Link")
        if self.kind == "striker" and not self.striker_approved:
            raise ValueError("An alternative Striker requires explicit GM approval")
        return self


class AlternativeAttackGroup(Record):
    members: tuple[AlternativeAttack, ...] = Field(min_length=2, max_length=100)
    disabled_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent_group(self) -> AlternativeAttackGroup:
        ids = tuple(member.attack_id for member in self.members)
        if len(set(ids)) != len(ids):
            raise ValueError("Alternative attacks require unique IDs")
        if len(set(self.disabled_ids)) != len(self.disabled_ids) or not set(
            self.disabled_ids
        ) <= set(ids):
            raise ValueError("Alternative-attack disabled IDs must name group members")
        return self

    @property
    def primary_id(self) -> str:
        return max(self.members, key=lambda member: member.point_cost).attack_id

    @property
    def purchase_cost(self) -> int:
        primary = self.primary_id
        return sum(
            member.point_cost if member.attack_id == primary else _ceil_div(member.point_cost, 5)
            for member in self.members
        )

    def available(self, attack_id: str) -> bool:
        ids = {member.attack_id for member in self.members}
        return attack_id in ids and attack_id not in set(self.disabled_ids)


def select_alternative(group: AlternativeAttackGroup, attack_ids: tuple[str, ...]) -> str:
    if len(attack_ids) != 1 or not group.available(attack_ids[0]):
        raise ValidationError("Exactly one available alternative attack must be selected")
    return attack_ids[0]


def disable_alternative(
    group: AlternativeAttackGroup,
    attack_id: str,
    cause: Literal["critical-failure", "malfunction", "drained", "neutralized"],
) -> AlternativeAttackGroup:
    ids = tuple(member.attack_id for member in group.members)
    if attack_id not in ids:
        raise ValidationError("Unknown alternative attack")
    disabled = (
        ids
        if cause in ("critical-failure", "malfunction") or attack_id == group.primary_id
        else tuple(dict.fromkeys((*group.disabled_ids, attack_id)))
    )
    return group.model_copy(update={"disabled_ids": disabled})


class SecretDisadvantage(Record):
    secret_id: str = Field(min_length=1, max_length=200)
    definition_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    underlying_cost: int = Field(le=-1, ge=-10000)
    status: Literal["hidden", "revealed", "settled"] = "hidden"
    buyoff_due: int = Field(default=0, ge=0, le=5)

    @model_validator(mode="after")
    def valid_debt(self) -> SecretDisadvantage:
        if (self.status == "revealed") != (self.buyoff_due == 5):
            raise ValueError("Secret-disadvantage reveal debt does not match its state")
        if len(set(self.definition_ids)) != len(self.definition_ids):
            raise ValueError("Secret disadvantage contains duplicate traits")
        return self

    @property
    def credited_cost(self) -> int:
        return self.underlying_cost if self.status == "settled" else self.underlying_cost - 5


def reveal_secret(state: SecretDisadvantage) -> SecretDisadvantage:
    if state.status != "hidden":
        raise ValidationError("Secret disadvantage is already revealed")
    return state.model_copy(update={"status": "revealed", "buyoff_due": 5})


class SecretBuyoff(Record):
    state: SecretDisadvantage
    points_spent: Literal[5] = 5
    points_remaining: int = Field(ge=0)


def settle_secret(state: SecretDisadvantage, available_points: int) -> SecretBuyoff:
    if state.status != "revealed" or type(available_points) is not int or available_points < 5:
        raise ValidationError("Revealed secret disadvantage requires five available points")
    return SecretBuyoff(
        state=state.model_copy(update={"status": "settled", "buyoff_due": 0}),
        points_remaining=available_points - 5,
    )


class SelfControlResolution(Record):
    decision: Literal["give-in", "roll", "buy-success"]
    resisted: bool
    dice: tuple[int, int, int] | None = None
    points_spent: int = Field(ge=0, le=1)
    points_remaining: int = Field(ge=0)


def resolve_self_control(
    profile_id: str,
    base_cost: int,
    levels: int,
    options: TraitOptions,
    rules: TraitRules,
    decision: Literal["give-in", "roll", "buy-success"],
    *,
    available_points: int,
    gm_allows_paid_success: bool,
    rng: RandomSource,
    modifiers: tuple[Modifier, ...] = (),
) -> SelfControlResolution:
    if type(available_points) is not int or available_points < 0:
        raise ValidationError("Self-control point balance must be nonnegative")
    if rules.profile_id != profile_id or not rules.self_control or options.self_control is None:
        raise ValidationError("Requires a compiled self-control disadvantage in this profile")
    cost(base_cost, levels, options, rules)
    if decision == "give-in":
        return SelfControlResolution(
            decision=decision, resisted=False, points_spent=0, points_remaining=available_points
        )
    if decision == "buy-success":
        if not gm_allows_paid_success or available_points < 1:
            raise ValidationError("Paid self-control success is unavailable")
        return SelfControlResolution(
            decision=decision, resisted=True, points_spent=1, points_remaining=available_points - 1
        )
    trace = self_control_roll(
        profile_id, base_cost, levels, options, rules, rng=rng, modifiers=modifiers
    )
    return SelfControlResolution(
        decision=decision,
        resisted=trace.outcome.succeeded,
        dice=trace.dice,
        points_spent=0,
        points_remaining=available_points,
    )


SELF_IMPOSED_IDS: Final = frozenset(
    {
        "trait:code-of-honor",
        "trait:disciplines-of-faith",
        "trait:fanaticism",
        "trait:honesty",
        "trait:intolerance",
        "trait:sense-of-duty",
        "trait:trademark",
        "trait:vow",
    }
)
ImpositionMethod = Literal[
    "chosen",
    "magic",
    "mind-control",
    "prolonged-brainwashing",
    "affliction",
    "drug",
    "brain-surgery",
]


class SelfImposedDisadvantage(Record):
    definition_id: str
    method: Literal["chosen", "magic", "mind-control", "prolonged-brainwashing"]
    pact_condition: bool = False


def impose_self_imposed(
    definition_id: str, method: ImpositionMethod, *, pact_condition: bool = False
) -> SelfImposedDisadvantage:
    if definition_id not in SELF_IMPOSED_IDS:
        raise ValidationError("Trait is not a Basic Set self-imposed mental disadvantage")
    if method in ("affliction", "drug", "brain-surgery"):
        raise ValidationError("Quick behavior alteration cannot impose a self-imposed disadvantage")
    accepted = cast(Literal["chosen", "magic", "mind-control", "prolonged-brainwashing"], method)
    return SelfImposedDisadvantage(
        definition_id=definition_id, method=accepted, pact_condition=pact_condition
    )


class BuyoffReceipt(Record):
    old_cost: int = Field(le=-1)
    new_cost: int = Field(le=0)
    points_spent: int = Field(ge=1)
    points_remaining: int = Field(ge=0)


def buy_off_disadvantage(
    base_cost: int,
    before_levels: int,
    before_options: TraitOptions,
    after_levels: int,
    after_options: TraitOptions | None,
    rules: TraitRules,
    *,
    available_points: int,
    gm_permits: bool,
) -> BuyoffReceipt:
    if not gm_permits or base_cost >= 0:
        raise ValidationError("This disadvantage buyoff is not permitted")
    if type(available_points) is not int or available_points < 0:
        raise ValidationError("Disadvantage buyoff point balance must be nonnegative")
    old_cost = cost(base_cost, before_levels, before_options, rules)
    if after_levels == 0:
        if after_options is not None:
            raise ValidationError("A fully removed disadvantage cannot retain options")
        new_cost = 0
    else:
        if after_options is None:
            raise ValidationError("A retained disadvantage requires its typed options")
        new_cost = cost(base_cost, after_levels, after_options, rules)
        one_level = after_levels == before_levels - 1 and after_options == before_options
        ratings = (6, 9, 12, 15)
        before_rating, after_rating = before_options.self_control, after_options.self_control
        one_control_step = (
            after_levels == before_levels
            and before_rating in ratings[:-1]
            and after_rating == ratings[ratings.index(before_rating) + 1]
            and after_options.model_copy(update={"self_control": before_rating}) == before_options
        )
        if not one_level and not one_control_step:
            raise ValidationError(
                "A partial buyoff must remove one level or raise self-control one step"
            )
    points = new_cost - old_cost
    if points <= 0:
        raise ValidationError("A buyoff must reduce the disadvantage's point value")
    if points > available_points:
        raise ValidationError("Disadvantage buyoff overspends available bonus points")
    return BuyoffReceipt(
        old_cost=old_cost,
        new_cost=new_cost,
        points_spent=points,
        points_remaining=available_points - points,
    )


def _ceil_div(value: int, divisor: int) -> int:
    return -(-value // divisor)
