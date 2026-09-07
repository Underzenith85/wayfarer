"""Independent hand-entered expectations; frozen-source audit remains pending."""

import pytest

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.gurps_social import (
    ReactionModifier,
    fright_roll,
    influence_roll,
    reaction_outcome,
    self_control_roll,
)
from wayfarer.rules.traits import TraitOptions, TraitRules
from wayfarer.simulation.resources import ResourceState
from wayfarer.simulation.social import SocialCommand, SocialContext, apply_social
from wayfarer.world import Entity, EntityKind, Fact, World

PROFILE = "gurps-basic-set-4e-2004"


@pytest.mark.parametrize(
    "total,outcome",
    [
        (-1, "disastrous"),
        (0, "disastrous"),
        (1, "very-bad"),
        (3, "very-bad"),
        (4, "bad"),
        (6, "bad"),
        (7, "poor"),
        (9, "poor"),
        (10, "neutral"),
        (12, "neutral"),
        (13, "good"),
        (15, "good"),
        (16, "very-good"),
        (18, "very-good"),
        (19, "excellent"),
    ],
)
def test_reaction_boundaries(total: int, outcome: str) -> None:
    assert reaction_outcome(total) == outcome


def test_influence_diplomacy_and_sex_appeal() -> None:
    fallback = influence_roll(
        PROFILE, "diplomacy", "pc", "npc", 10, 10, (), rng=RecordedDice([4, 4, 4, 3, 3, 3, 5, 5, 5])
    )
    assert fallback.outcome == "good" and fallback.contest.winner == "npc"
    seduction = influence_roll(
        PROFILE, "sex-appeal", "pc", "npc", 12, 10, (), rng=RecordedDice([3, 3, 3, 4, 4, 4])
    )
    assert seduction.outcome == "very-good"
    tie = influence_roll(
        PROFILE, "fast-talk", "pc", "npc", 10, 10, (), rng=RecordedDice([3, 3, 3, 3, 3, 3])
    )
    assert tie.outcome == "bad"


def test_fright_rule_of_fourteen_and_deferred_consequence() -> None:
    result = fright_roll(PROFILE, 20, rng=RecordedDice([4, 5, 5, 3, 3, 3]))
    assert not result.check.outcome.succeeded
    assert result.check.effective_target == 13
    assert result.table_total == 10
    assert result.consequence_status == "awaiting-reviewed-table"
    with pytest.raises(ValidationError):
        fright_roll("gurps-lite-4e-2004", 10, rng=RecordedDice([]))


@pytest.mark.parametrize("rating,success", [(6, False), (9, True), (12, True), (15, True)])
def test_self_control_from_typed_trait(rating: int, success: bool) -> None:
    options = TraitOptions.model_validate({"self_control": rating})
    rules = TraitRules(PROFILE, self_control=True)
    trace = self_control_roll(PROFILE, -5, 1, options, rules, rng=RecordedDice([3, 3, 3]))
    assert trace.outcome.succeeded is success


def test_secret_modifiers_and_stable_trigger_receipts() -> None:
    world = World(
        entities=(
            Entity("pc", EntityKind.ACTOR, "Player"),
            Entity("npc", EntityKind.ACTOR, "Guard"),
        ),
        facts=(Fact("secret", "npc", "loyalty", "enemy"),),
        knowledge=(("npc", "secret"),),
    )
    command = SocialCommand(
        id="reaction",
        actor_id="pc",
        expected_revision=0,
        kind="reaction",
        subject_id="npc",
        trigger_id="meeting-1",
    )
    context = SocialContext(
        PROFILE,
        10,
        modifiers=(ReactionModifier("reputation", -3, "secret", True),),
        required_fact_ids=("secret",),
    )
    state, public = apply_social(
        ResourceState(), world, command, context, rng=RecordedDice([4, 4, 4]), system=True
    )
    assert public.outcome == "poor"
    assert "secret" not in public.model_dump_json() and "-3" not in public.model_dump_json()
    assert world.perspective("pc").facts == ()
    replayed = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_social(replayed, world, command, context, rng=RecordedDice([]), system=True) == (
        state,
        public,
    )
    with pytest.raises(ConflictError):
        apply_social(
            state,
            world,
            command.model_copy(update={"id": "reroll", "expected_revision": 1}),
            context,
            rng=RecordedDice([]),
            system=True,
        )
    with pytest.raises(ValidationError):
        apply_social(ResourceState(), world, command, context, rng=RecordedDice([]))
