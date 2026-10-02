"""Persisted authored modifier defaults cannot become new runtime intent."""

import pytest
from test_attack_defense_traits import compiler
from test_statistics import gurps_draft
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.traits.cyclic import approvals
from wayfarer.engine.rules.traits.modifiers import (
    AttackProfile,
    EnhancementParameters,
    LimitationParameters,
    ModifierSelection,
    apply_attack_modifiers,
)
from wayfarer.errors import ValidationError


def cyclic() -> ModifierSelection:
    return ModifierSelection(
        definition_id="modifier:enhancement:cyclic",
        option="persisted",
        parameters=EnhancementParameters(
            interval_seconds=10,
            cycles=3,
            contagious="none",
            stop_condition="wash",
            damage_kind="burning",
        ),
    )


def resistible() -> ModifierSelection:
    return ModifierSelection(
        definition_id="modifier:limitation:resistible",
        option="ht+0",
        limitation=LimitationParameters(resistance_modifier=0),
    )


@pytest.mark.parametrize("selections", [(cyclic(),), (cyclic(), resistible())])
def test_approved_purchased_modifiers_survive_expanded_json_without_changing_approval(
    selections: tuple[ModifierSelection, ...],
) -> None:
    reviewer = PowerReviewer(
        compiler(),
        PowerPolicy(id="persisted", version=1, automatic_approval=True),
        frozenset({"gm"}),
    )
    proposal = CharacterProposal(
        draft=gurps_draft(
            Purchase(
                definition_id="advantage:innate-attack",
                trait=options(**{"damage-type": "burn"}).model_copy(
                    update={"attack_modifiers": selections}
                ),
            )
        )
    )
    approval = reviewer.approve(proposal, campaign_id="campaign", actor_id="a", revision=0)
    before, _ = reviewer.activate(proposal, approval, campaign_id="campaign", actor_id="a")
    encoded = proposal.model_dump_json()
    restored = CharacterProposal.model_validate_json(encoded)
    after, _ = reviewer.activate(restored, approval, campaign_id="campaign", actor_id="a")
    assert after == before and after.revision == approval.build_revision
    assert restored.model_dump_json() == encoded
    attack = apply_attack_modifiers(
        AttackProfile(damage_kind="burning"), "innate-attack", selections, approvals(selections)
    )
    replayed = tuple(ModifierSelection.model_validate_json(s.model_dump_json()) for s in selections)
    assert (
        apply_attack_modifiers(
            AttackProfile(damage_kind="burning"), "innate-attack", replayed, approvals(replayed)
        )
        == attack
    )


@pytest.mark.parametrize("restore", [False, True])
def test_meaningful_unsupported_parameters_still_reject_before_execution(restore: bool) -> None:
    source = cyclic()
    assert source.parameters is not None
    for selection in (
        source.model_copy(
            update={"parameters": source.parameters.model_copy(update={"width_yards": 2})}
        ),
        resistible().model_copy(
            update={
                "limitation": LimitationParameters(
                    resistance_modifier=0, half_damage_range_only=True
                )
            }
        ),
    ):
        if restore:
            selection = ModifierSelection.model_validate_json(selection.model_dump_json())
        selected = (selection,) if selection.parameters is not None else (cyclic(), selection)
        with pytest.raises(
            ValidationError, match="unsupported runtime parameters|no supported attack consumer"
        ):
            apply_attack_modifiers(
                AttackProfile(damage_kind="burning"),
                "innate-attack",
                selected,
                approvals(selected),
            )


def test_expanded_empty_parameters_do_not_satisfy_required_resistance() -> None:
    empty = LimitationParameters.model_validate_json(LimitationParameters().model_dump_json())
    missing = resistible().model_copy(update={"limitation": empty})
    with pytest.raises(ValidationError, match="requires explicit runtime parameters"):
        apply_attack_modifiers(
            AttackProfile(damage_kind="burning"),
            "innate-attack",
            (cyclic(), missing),
            approvals((cyclic(), missing)),
        )
