"""B519 rescue reactions through the real campaign economics reducer.

Numeric expectations come from B519 and the B560-561 reaction bands, not from
the scorer under test. These are domain/receipt tests, not a Luck host claim.
"""

import hashlib
from dataclasses import replace
from typing import Literal

import pytest
from test_economics import configured, state

from wayfarer.engine.rules.checks import Modifier, ModifierKind, RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.economics import (
    CheckLoyalty,
    EconomicsOutcome,
    FindHireling,
    HirelingContract,
    LoyaltyCheck,
    LoyaltyCircumstance,
)
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ConflictError, ValidationError


def rescue_modifier(value: int = 3) -> Modifier:
    return Modifier(value, "Risked the mission to rescue the guide", "rescue:guide", "B519")


def rescue_campaign(
    old_loyalty: int = 14,
    *,
    bonus: int = 3,
    offered_pay: int = 120,
) -> tuple[ActionEngine, PlayState, CheckLoyalty]:
    base = configured()
    assert base.rules.economics is not None
    rules = base.rules.economics.model_copy(
        update={
            "hirelings": (
                base.rules.economics.hirelings[0].model_copy(update={"offered_pay": offered_pay}),
            ),
            "loyalty": base.rules.economics.loyalty
            + (
                LoyaltyCircumstance(
                    id="rescue",
                    hireling_rule_id="guide",
                    kind="rescue",
                    modifiers=(rescue_modifier(bonus),),
                ),
            ),
        }
    )
    reducer = ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"economics": rules})
    )
    before = state(reducer)
    contract = HirelingContract(
        id="hireling:guide",
        rule_id="guide",
        employer_id="a",
        hireling_id="b",
        competence=12,
        loyalty=old_loyalty,
        private_motive="Private guide motivation",
    )
    before = before.model_copy(
        update={"economics": before.economics.model_copy(update={"hirelings": (contract,)})}
    )
    reducer.validate(before)
    return (
        reducer,
        before,
        CheckLoyalty(
            id="rescued-guide",
            actor_id="a",
            expected_revision=0,
            contract_id=contract.id,
            circumstance_id="rescue",
        ),
    )


@pytest.mark.parametrize(
    ("old", "faces", "bonus", "new", "grateful"),
    [
        (14, (5, 5, 5), 3, 18, True),
        (14, (3, 3, 3), 3, 14, False),
        (6, (3, 3, 3), 3, 6, False),  # Neutral never sets loyalty, even when higher.
        (12, (3, 3, 4), 3, 13, True),  # First Good result.
        (16, (3, 3, 4), 3, 16, True),  # A grateful hireling never loses loyalty.
        (14, (4, 4, 4), 3, 15, True),
        (14, (4, 4, 5), 3, 16, True),
        (14, (5, 5, 6), 3, 19, True),
        (14, (5, 6, 6), 3, 20, True),  # 17 is not a success-check failure here.
        (20, (6, 6, 6), 3, 21, True),  # 20+ still rolls, with no loyalty cap.
        (25, (6, 6, 6), 3, 25, True),
        (20, (1, 1, 1), 3, 20, False),  # No automatic pass from high loyalty.
        (14, (6, 6, 6), 5, 23, True),  # The GM can author more than +3.
    ],
)
def test_rescue_reaction_changes_the_actual_contract(
    old: int, faces: tuple[int, ...], bonus: int, new: int, grateful: bool
) -> None:
    reducer, before, command = rescue_campaign(old, bonus=bonus)
    dice = RecordedDice(faces)
    updated, outcome = reducer.campaign.apply(before, command, rng=dice, system=True)
    assert dice.exhausted()
    assert updated.economics.hirelings[0] == before.economics.hirelings[0].model_copy(
        update={"loyalty": new}
    )
    assert updated.economics.loyalty_checks == (
        LoyaltyCheck(
            id=command.id,
            contract_id=command.contract_id,
            circumstance_id="rescue",
            passed=grateful,
            loyalty_after=new,
        ),
    )
    assert outcome == EconomicsOutcome(status="grateful" if grateful else "loyalty-unchanged")
    assert updated.revision == updated.resources.revision == before.revision + 1
    assert updated.resources.game_time == before.resources.game_time
    assert updated.world == before.world
    assert updated.economics.accounts == before.economics.accounts
    assert updated.economics.entries == before.economics.entries
    assert updated.actors == before.actors
    reducer.validate(updated)
    event = updated.resources.events[-1]
    assert event.kind == outcome.model_dump_json()
    assert "Private guide motivation" not in event.kind
    assert "dice" not in event.kind and "loyalty_after" not in event.kind


@pytest.mark.parametrize(
    "old,faces,expected",
    [
        (14, (5, 5, 5), 18),
        (14, (3, 3, 3), 14),
        (20, (6, 6, 6), 21),
    ],
)
def test_default_rescue_uses_the_printed_plus_three(
    old: int,
    faces: tuple[int, ...],
    expected: int,
) -> None:
    reducer, before, command = rescue_campaign(old)
    assert reducer.campaign.economics is not None
    rules = reducer.campaign.economics
    reducer.campaign.economics = rules.model_copy(
        update={
            "loyalty": tuple(
                value.model_copy(update={"modifiers": ()}) if value.id == "rescue" else value
                for value in rules.loyalty
            )
        }
    )
    dice = RecordedDice(faces)
    after, _ = reducer.campaign.apply(before, command, rng=dice, system=True)
    assert dice.exhausted()
    assert after.economics.hirelings[0].loyalty == expected


@pytest.mark.parametrize("offered_pay", [100, 120, 200, 1000])
def test_rescue_uses_no_ordinary_pay_target_bonus(offered_pay: int) -> None:
    reducer, before, command = rescue_campaign(offered_pay=offered_pay)
    updated, _ = reducer.campaign.apply(before, command, rng=RecordedDice((5, 5, 5)), system=True)
    assert updated.economics.hirelings[0].loyalty == 18


def test_authored_rescue_modifier_components_are_added_once() -> None:
    reducer, before, command = rescue_campaign()
    assert reducer.campaign.economics is not None
    circumstance = reducer.campaign.economics.loyalty[-1]
    reducer.campaign.economics = reducer.campaign.economics.model_copy(
        update={
            "loyalty": (
                circumstance.model_copy(
                    update={
                        "modifiers": (
                            rescue_modifier(3),
                            Modifier(2, "Risked both rescuer and mission", "rescue:extra", "B519"),
                        )
                    }
                ),
            )
        }
    )
    updated, _ = reducer.campaign.apply(before, command, rng=RecordedDice((4, 4, 4)), system=True)
    assert updated.economics.hirelings[0].loyalty == 17


def test_rescue_retry_after_checkpoint_restore_consumes_nothing() -> None:
    reducer, before, command = rescue_campaign()
    updated, outcome = reducer.campaign.apply(
        before, command, rng=RecordedDice((5, 5, 5)), system=True
    )
    restored = PlayState.model_validate_json(updated.model_dump_json())
    retried, retry_outcome = reducer.campaign.apply(
        restored, command, rng=RecordedDice(()), system=True
    )
    assert retried is restored
    assert retried.model_dump_json() == updated.model_dump_json()
    assert retry_outcome == outcome
    with pytest.raises(ConflictError, match="command ID reused"):
        reducer.campaign.apply(
            restored,
            command.model_copy(update={"circumstance_id": "danger"}),
            rng=RecordedDice(()),
            system=True,
        )


def test_historical_additive_rescue_receipt_is_not_reinterpreted() -> None:
    reducer, before, command = rescue_campaign()
    assert reducer.campaign.economics is not None
    historical_rule = LoyaltyCircumstance(
        id="rescue", hireling_rule_id="guide", kind="rescue", loyalty_change_on_success=1
    )
    reducer.campaign.economics = reducer.campaign.economics.model_copy(
        update={"loyalty": (historical_rule,)}
    )
    # Literal checkpoint produced by the old successful additive branch. It is
    # intentionally invalid for a new rescue; retry must return its committed bytes.
    historical_outcome = '{"status":"loyal","amount":0,"consequence":"","private":[]}'
    resources = before.resources.model_copy(
        update={
            "revision": 1,
            "receipts": (
                Receipt(
                    command_id=command.id,
                    digest=hashlib.sha256(command.model_dump_json().encode()).hexdigest(),
                ),
            ),
            "events": (
                ResourceEvent(
                    id="economics:" + command.id, at=0, kind=historical_outcome, target_id="a"
                ),
            ),
        }
    )
    historical = before.model_copy(
        update={
            "revision": 1,
            "resources": resources,
            "economics": before.economics.model_copy(
                update={
                    "hirelings": (
                        before.economics.hirelings[0].model_copy(update={"loyalty": 15}),
                    ),
                    "loyalty_checks": (
                        LoyaltyCheck(
                            id=command.id,
                            contract_id=command.contract_id,
                            circumstance_id="rescue",
                            passed=True,
                            loyalty_after=15,
                        ),
                    ),
                }
            ),
        }
    )
    restored = PlayState.model_validate_json(historical.model_dump_json())
    retried, outcome = reducer.campaign.apply(restored, command, rng=RecordedDice(()), system=True)
    assert retried is restored
    assert retried.model_dump_json() == historical.model_dump_json()
    assert outcome.model_dump_json() == historical_outcome
    assert retried.economics.hirelings[0].loyalty == 15


@pytest.mark.parametrize("seed", ["00" * 32, "01" * 32, "02" * 32])
def test_rescue_preserves_the_callers_seeded_stream(seed: str) -> None:
    reducer, before, command = rescue_campaign()
    first_rng, second_rng = SeededRandom(seed), SeededRandom(seed)
    first = reducer.campaign.apply(before, command, rng=first_rng, system=True)
    second = reducer.campaign.apply(before, command, rng=second_rng, system=True)
    assert first == second
    # The domain owns exactly one reaction's 3d, not the persisted host seed.
    reference = SeededRandom(seed)
    for _ in range(3):
        reference.randbelow(6)
    assert first_rng.randbelow(1_000_000) == reference.randbelow(1_000_000)


@pytest.mark.parametrize("kind", ["danger", "temptation", "service", "competence"])
@pytest.mark.parametrize(
    ("old", "faces", "delta", "expected_passed"),
    [(9, (3, 3, 3), 1, True), (9, (4, 4, 4), -1, False), (18, (), 1, True)],
)
def test_nonrescue_loyalty_keeps_existing_success_check_procedure(
    kind: Literal["danger", "temptation", "service", "competence"],
    old: int,
    faces: tuple[int, ...],
    delta: int,
    expected_passed: bool,
) -> None:
    reducer, before, command = rescue_campaign(old)
    assert reducer.campaign.economics is not None
    reducer.campaign.economics = reducer.campaign.economics.model_copy(
        update={
            "loyalty": (
                LoyaltyCircumstance(
                    id="ordinary",
                    hireling_rule_id="guide",
                    kind=kind,
                    loyalty_change_on_success=1,
                    loyalty_change_on_failure=-1,
                ),
            )
        }
    )
    dice = RecordedDice(faces)
    updated, outcome = reducer.campaign.apply(
        before, command.model_copy(update={"circumstance_id": "ordinary"}), rng=dice, system=True
    )
    assert dice.exhausted()
    assert updated.economics.hirelings[0].loyalty == old + delta
    assert updated.economics.loyalty_checks[-1].passed is expected_passed
    assert outcome.status == ("loyal" if expected_passed else "self-interest")


@pytest.mark.parametrize(
    ("modifiers", "success_delta", "failure_delta", "message"),
    [
        ((rescue_modifier(2),), 0, 0, "at least"),
        ((rescue_modifier(-3),), 0, 0, "at least"),
        ((rescue_modifier(),), 1, 0, "additive"),
        ((rescue_modifier(),), 0, -1, "additive"),
        ((replace(rescue_modifier(), value=True),), 0, 0, "provenance"),
        ((replace(rescue_modifier(), reason=" "),), 0, 0, "provenance"),
        ((replace(rescue_modifier(), source_id=" "),), 0, 0, "provenance"),
        ((replace(rescue_modifier(), source_version=" "),), 0, 0, "provenance"),
        ((replace(rescue_modifier(), kind=ModifierKind.TRAIT),), 0, 0, "provenance"),
        ((rescue_modifier(), rescue_modifier()), 0, 0, "Duplicate"),
    ],
)
def test_malformed_rescue_contexts_refuse_before_dice_or_effects(
    modifiers: tuple[Modifier, ...], success_delta: int, failure_delta: int, message: str
) -> None:
    reducer, before, command = rescue_campaign()
    assert reducer.campaign.economics is not None
    circumstance = reducer.campaign.economics.loyalty[-1].model_copy(
        update={
            "modifiers": modifiers,
            "loyalty_change_on_success": success_delta,
            "loyalty_change_on_failure": failure_delta,
        }
    )
    reducer.campaign.economics = reducer.campaign.economics.model_copy(
        update={"loyalty": (circumstance,)}
    )
    original = before.model_dump_json()
    with pytest.raises(ValidationError, match=message):
        reducer.campaign.apply(before, command, rng=RecordedDice(()), system=True)
    assert before.model_dump_json() == original


@pytest.mark.parametrize(
    "case",
    [
        "inactive",
        "wrong-hireling",
        "wrong-employer",
        "same-actor",
        "missing-hireling",
        "nonactor-hireling",
        "wrong-circumstance",
    ],
)
def test_invalid_rescue_parties_refuse_before_dice_or_effects(case: str) -> None:
    reducer, before, command = rescue_campaign()
    assert reducer.campaign.economics is not None
    contract = before.economics.hirelings[0]
    rule = reducer.campaign.economics.hirelings[0]
    if case == "inactive":
        contract = contract.model_copy(update={"active": False})
    elif case == "wrong-hireling":
        contract = contract.model_copy(update={"hireling_id": "other"})
    elif case == "wrong-employer":
        rule = rule.model_copy(update={"employer_id": "other"})
    elif case == "same-actor":
        contract = contract.model_copy(update={"hireling_id": "a"})
        rule = rule.model_copy(update={"hireling_id": "a"})
    elif case == "missing-hireling":
        before = before.model_copy(
            update={
                "world": replace(
                    before.world, entities=tuple(e for e in before.world.entities if e.id != "b")
                )
            }
        )
    elif case == "nonactor-hireling":
        before = before.model_copy(
            update={
                "world": replace(
                    before.world,
                    entities=tuple(
                        replace(e, kind=EntityKind.LOCATION) if e.id == "b" else e
                        for e in before.world.entities
                    ),
                )
            }
        )
    else:
        command = command.model_copy(update={"circumstance_id": "missing"})
    reducer.campaign.economics = reducer.campaign.economics.model_copy(
        update={"hirelings": (rule,)}
    )
    before = before.model_copy(
        update={"economics": before.economics.model_copy(update={"hirelings": (contract,)})}
    )
    original = before.model_dump_json()
    with pytest.raises(ValidationError, match="(Rescue|Loyalty|Unknown)"):
        reducer.campaign.apply(before, command, rng=RecordedDice(()), system=True)
    assert before.model_dump_json() == original


def test_rescue_keeps_engine_authority_and_revision_refusals() -> None:
    reducer, before, command = rescue_campaign()
    with pytest.raises(ValidationError, match="engine authority"):
        reducer.campaign.apply(before, command, rng=RecordedDice(()))
    with pytest.raises(ConflictError, match="revision changed"):
        reducer.campaign.apply(
            before,
            command.model_copy(update={"expected_revision": 1}),
            rng=RecordedDice(()),
            system=True,
        )
    with pytest.raises(ValidationError, match="Only the employer"):
        changed = before.economics.hirelings[0].model_copy(update={"employer_id": "b"})
        reducer.campaign.apply(
            before.model_copy(
                update={"economics": before.economics.model_copy(update={"hirelings": (changed,)})}
            ),
            command,
            rng=RecordedDice(()),
            system=True,
        )


def test_hire_then_rescue_uses_one_new_reaction_without_repeating_search() -> None:
    reducer, _, rescue = rescue_campaign()
    dice = RecordedDice((1, 1, 1, 4, 5, 5, 5, 5, 5))
    hired, _ = reducer.campaign.apply(
        state(reducer),
        FindHireling(id="hire", actor_id="a", expected_revision=0, hireling_rule_id="guide"),
        rng=dice,
        system=True,
    )
    assert hired.economics.hirelings[0].loyalty == 14
    updated, _ = reducer.campaign.apply(
        hired,
        rescue.model_copy(update={"expected_revision": hired.revision}),
        rng=dice,
        system=True,
    )
    assert dice.exhausted()
    assert updated.economics.hirelings[0].loyalty == 18
    assert len(updated.economics.hirelings) == 1
    assert len(updated.resources.receipts) == 2
