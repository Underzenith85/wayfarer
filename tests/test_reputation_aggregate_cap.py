"""B28 bounds the aggregate recognized Reputation total, independent of row order."""

import json
from itertools import permutations

import pytest
from test_campaign_reaction_context import with_traits
from test_campaign_reaction_continuations import initial_case, prepare
from test_prepared_reactions import command, world

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.gurps_social import evaluate_reaction
from wayfarer.engine.rules.social.social_hooks import Reputation, Standing, standing_modifiers
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social.social import SocialContext, apply_social

PROFILE = "gurps-basic-set-4e-2004"


@pytest.mark.parametrize(
    "levels,expected",
    [
        ((4, 4, -4), 4),
        ((-4, -4, 4), -4),
        ((4, 4, 4), 4),
        ((-4, -4, -4), -4),
        ((4, -4, 4, -4), 0),
        ((4, 4, -2), 4),
        ((2, 2, 2, -3), 3),
    ],
)
def test_corrected_cap_applies_once_to_the_sum_for_every_source_order(
    levels: tuple[int, ...],
    expected: int,
) -> None:
    sources = tuple(Reputation(f"reputation-{index}", level) for index, level in enumerate(levels))
    for ordered in permutations(sources):
        trace = standing_modifiers(
            PROFILE,
            Standing(reputations=ordered),
            rng=RecordedDice(()),
            correct_reputation_cap=True,
        )
        assert trace.total == expected
        actual = {modifier.source_id: modifier.value for modifier in trace.modifiers}
        for reputation in ordered:
            assert actual["reputation:" + reputation.id] == reputation.level


def test_aggregate_cap_is_reputation_only_and_preserves_private_contribution_provenance() -> None:
    trace = standing_modifiers(
        PROFILE,
        Standing(
            "attractive",
            (
                Reputation("public", 4),
                Reputation("private", 4, hidden=True),
            ),
        ),
        rng=RecordedDice(()),
        correct_reputation_cap=True,
    )
    assert trace.total == 5  # Appearance1 plus aggregate Reputation4.
    assert [
        (modifier.value, modifier.source_id, modifier.hidden) for modifier in trace.modifiers
    ] == [
        (1, "appearance:attractive", False),
        (4, "reputation:public", False),
        (4, "reputation:private", True),
        (-4, "reputation-cap:aggregate", True),
    ]


def test_unrecognized_reputations_are_excluded_before_the_aggregate_cap() -> None:
    standing = Standing(
        reputations=(
            Reputation("first", 4, recognition="sometimes"),
            Reputation("second", 4, recognition="sometimes"),
            Reputation("third", -4, recognition="sometimes"),
        )
    )
    rng = RecordedDice((3, 3, 3, 6, 6, 6, 3, 3, 3))
    trace = standing_modifiers(PROFILE, standing, rng=rng, correct_reputation_cap=True)
    assert rng.exhausted() and trace.total == 0  # Recognized +4 and -4 only.
    assert tuple(roll.recognized for roll in trace.recognition) == (True, False, True)


def test_legacy_default_clipping_and_corrected_immediate_generation_are_distinct() -> None:
    standing = Standing(
        reputations=(Reputation("one", 4), Reputation("two", 4), Reputation("three", -4))
    )
    legacy = standing_modifiers(PROFILE, standing, rng=RecordedDice(()))
    assert legacy.total == 0
    assert [(row.source_id, row.value) for row in legacy.modifiers] == [
        ("reputation:one", 4),
        ("reputation:three", -4),
    ]
    outcomes = []
    for corrected in (False, True):
        resources, outcome = apply_social(
            ResourceState(),
            world(),
            command(),
            SocialContext(PROFILE, 0, standing=standing),
            rng=RecordedDice((3, 3, 3)),
            system=True,
            correct_reactions=corrected,
        )
        outcomes.append(outcome.outcome)
        prior = resources.events[-1].model_dump_json()
        restored, repeated = apply_social(
            resources,
            world(),
            command(),
            SocialContext(PROFILE, 0, standing=standing),
            rng=RecordedDice(()),
            system=True,
            correct_reactions=not corrected,
        )
        assert restored is resources and repeated == outcome
        assert restored.events[-1].model_dump_json() == prior
    assert outcomes == ["poor", "good"]


@pytest.mark.parametrize("order", list(permutations((0, 1, 2))))
def test_campaign_initial_contract_uses_aggregate_reputation_after_one_recognition_per_source(
    order: tuple[int, ...],
) -> None:
    engine, state, source = initial_case()
    engine, state = with_traits(engine, state)
    rows = (
        Reputation("one", 4, recognition="sometimes"),
        Reputation("two", 4, recognition="sometimes"),
        Reputation("three", -4, recognition="sometimes"),
    )
    source = source.model_copy(
        update={
            "interaction": source.interaction.model_copy(
                update={
                    "standing": Standing(reputations=tuple(rows[index] for index in order)),
                }
            )
        }
    )
    pending = prepare(engine, state, source, (2, 2, 2))
    rng = RecordedDice((3, 3, 3) * 3)
    recognized = engine.campaign.recognize_reaction(pending, rng=rng)
    assert rng.exhausted() and len(recognized.recognition) == 3
    assert sum(modifier.value for modifier in recognized.modifiers) == 4
    after, outcome = engine.campaign.resolve_reaction(
        state,
        pending,
        evaluate_reaction(PROFILE, recognized.modifiers, (3, 3, 3)),
        rng=RecordedDice(()),
        player_actor_ids=("a",),
        recognized=recognized,
    )
    assert outcome.status == "hireling-found"
    engine.validate(after)
    assert after.economics.hirelings[0].loyalty == 13  # Independent raw9 + aggregate4.
    event = next(
        event for event in after.resources.events if event.id.startswith(("social:", "social-key:"))
    )
    assert json.loads(event.kind)["private"]["total"] == 13
    retry, repeated = engine.campaign.resolve_reaction(
        after,
        pending,
        evaluate_reaction(PROFILE, recognized.modifiers, (3, 3, 3)),
        rng=RecordedDice(()),
        player_actor_ids=("a",),
        recognized=recognized,
    )
    assert retry is after and repeated == outcome


@pytest.mark.parametrize(
    "standing_levels,authored_value,expected",
    [
        ((2,), 4, 4),
        ((4, 4), -4, 4),
        ((-4, -4), 4, -4),
        ((4,), -4, 0),
    ],
)
def test_cap_covers_combined_standing_and_authored_reputation_sources(
    standing_levels: tuple[int, ...],
    authored_value: int,
    expected: int,
) -> None:
    from wayfarer.engine.rules.social.gurps_social import ReactionModifier
    from wayfarer.engine.simulation.social.reactions import prepare_reaction, recognize_reaction
    from wayfarer.engine.simulation.social.social import SocialDisclosure

    source = SocialContext(
        PROFILE,
        0,
        standing=Standing(
            reputations=tuple(
                Reputation(f"rep-{index}", value) for index, value in enumerate(standing_levels)
            )
        ),
        modifiers=(ReactionModifier("reputation", authored_value, "authored-source"),),
    )
    prepared = prepare_reaction(
        ResourceState(),
        world(),
        command(),
        source,
        SocialDisclosure(),
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )
    recognized = recognize_reaction(prepared, rng=RecordedDice(()))
    assert sum(item.value for item in recognized.modifiers) == expected
    assert (
        len([item for item in recognized.modifiers if item.source_id == "reputation-cap:aggregate"])
        <= 1
    )


def test_campaign_raw_authored_reputation_cannot_bypass_the_aggregate_limit() -> None:
    from wayfarer.engine.rules.social.gurps_social import ReactionModifier

    engine, state, source = initial_case()
    engine, state = with_traits(engine, state)
    assert engine.campaign.economics is not None
    economics = engine.campaign.economics.model_copy(
        update={
            "hirelings": (
                engine.campaign.economics.hirelings[0].model_copy(
                    update={
                        "loyalty_modifiers": (
                            ReactionModifier("reputation", -4, "authored-source"),
                        )
                    }
                ),
            )
        }
    )
    engine = ActionEngine(
        engine.reviewer, engine.resources, engine.rules.model_copy(update={"economics": economics})
    )
    state = state.model_copy(update={"configuration_digest": engine.digest})
    engine.validate(state)
    source = source.model_copy(
        update={
            "interaction": source.interaction.model_copy(
                update={
                    "standing": Standing(reputations=(Reputation("one", 4), Reputation("two", 4))),
                }
            )
        }
    )
    pending = prepare(engine, state, source, (2, 2, 2))
    recognized = engine.campaign.recognize_reaction(pending, rng=RecordedDice(()))
    after, _ = engine.campaign.resolve_reaction(
        state,
        pending,
        evaluate_reaction(PROFILE, recognized.modifiers, (3, 3, 3)),
        rng=RecordedDice(()),
        player_actor_ids=("a",),
        recognized=recognized,
    )
    engine.validate(after)
    assert after.economics.hirelings[0].loyalty == 13


def test_generated_cap_provenance_cannot_be_authored_to_erase_a_source() -> None:
    from wayfarer.engine.rules.social.gurps_social import ReactionModifier
    from wayfarer.engine.simulation.social.reactions import prepare_reaction
    from wayfarer.engine.simulation.social.social import SocialDisclosure
    from wayfarer.errors import ValidationError

    with pytest.raises(ValidationError, match="generated, not authored"):
        prepare_reaction(
            ResourceState(),
            world(),
            command(),
            SocialContext(
                PROFILE,
                0,
                modifiers=(ReactionModifier("reputation", 100, "reputation-cap:aggregate"),),
            ),
            SocialDisclosure(),
            profile_id=PROFILE,
            player_actor_ids=("actor",),
            system=True,
        )
