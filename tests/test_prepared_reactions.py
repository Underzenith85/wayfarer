"""Independent B66/B494/B560-561 prepared-reaction and disclosure oracles.

Inspected privately: Characters third printing (872b5fec...), Campaigns fourth
printing (79cff8f7...). Complete complex answers use an authored Good+ policy;
simple answers can include Neutral. These tests do not certify other NPC
Reaction Table behaviors from a learned fact.
"""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest
from pydantic import ValidationError as SchemaError
from test_luck import approved

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.social.gurps_social import (
    ReactionModifier,
    ReactionTrace,
    evaluate_reaction,
    reaction_roll,
)
from wayfarer.engine.rules.social.social_hooks import Reputation, Standing
from wayfarer.engine.rules.traits.mundane.runtime import Audience
from wayfarer.engine.simulation.health.fright_state import TimedFright
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.engine.simulation.social.reactions import (
    PreparedReaction,
    ResolvedReactionContext,
    prepare_reaction,
    recognize_reaction,
    resolve_prepared_reaction,
    validate_prepared_reaction,
)
from wayfarer.engine.simulation.social.social import (
    SocialCommand,
    SocialContext,
    SocialDisclosure,
    SocialOutcome,
    apply_interaction,
)
from wayfarer.engine.simulation.traits.luck import LuckCommand, LuckRoll, LuckState, apply_luck
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, ValidationError

PROFILE = "gurps-basic-set-4e-2004"
DISCLOSURE = SocialDisclosure(("answer",))
EMPTY = ResourceState()


class CountedDice(RecordedDice):
    def __init__(self, faces: list[int]) -> None:
        super().__init__(faces)
        self.draws = 0

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        self.draws += 1
        return super().randbelow(exclusive_upper_bound)


def world() -> World:
    return World(
        entities=(
            Entity("actor", EntityKind.ACTOR, "Player"),
            Entity("npc", EntityKind.ACTOR, "Sage"),
            Entity("other", EntityKind.ACTOR, "Bystander"),
        ),
        facts=(
            Fact("answer", "npc", "route", "Through the north tunnel at low tide"),
            Fact("private", "npc", "secret", "Unrelated private fact"),
        ),
        knowledge=(("npc", "answer"), ("npc", "private")),
    )


def command(index: int = 0) -> SocialCommand:
    return SocialCommand(
        id=f"command{index}",
        actor_id="actor",
        subject_id="npc",
        kind="reaction",
        trigger_id=f"trigger{index}",
        expected_revision=index,
    )


def prepare(
    state: ResourceState = EMPTY,
    source_world: World | None = None,
    value: SocialCommand | None = None,
    context: SocialContext | None = None,
    disclosure: SocialDisclosure = DISCLOSURE,
) -> PreparedReaction:
    return prepare_reaction(
        state,
        source_world or world(),
        value or command(),
        context or SocialContext(PROFILE, 0),
        disclosure,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )


def finish(
    prepared: PreparedReaction,
    recognized: ResolvedReactionContext,
    selected: ReactionTrace,
    *,
    state: ResourceState = EMPTY,
    source_world: World | None = None,
    context: SocialContext | None = None,
    disclosure: SocialDisclosure = DISCLOSURE,
) -> tuple[ResourceState, World, SocialOutcome]:
    return resolve_prepared_reaction(
        state,
        source_world or world(),
        prepared.command.model_copy(update={"expected_revision": state.revision}),
        context or SocialContext(PROFILE, 0),
        disclosure,
        prepared,
        recognized,
        selected,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )


def selected_luck(
    prepared: PreparedReaction, recognized: ResolvedReactionContext, rng: CountedDice
) -> tuple[ReactionTrace, LuckState]:
    build, definitions = approved()
    roll = LuckRoll(
        id="pending-reaction",
        actor_id="actor",
        kind="reaction",
        modifier=sum(modifier.value for modifier in recognized.modifiers),
        secret=True,
        task_class="social",
    )
    luck, _ = apply_luck(
        LuckState(rolls=(roll,), pending_roll_id=roll.id),
        LuckCommand(id="use-luck", actor_id="actor", roll_id=roll.id, expected_revision=0),
        build,
        definitions,
        real_time=0,
        authorized_actor_id="actor",
        system=True,
        rng=rng,
    )
    dice = luck.rolls[0].chosen_dice
    assert dice is not None and len(dice) == 3
    return evaluate_reaction(
        prepared.source.profile_id, recognized.modifiers, (dice[0], dice[1], dice[2])
    ), luck


def test_unrolled_luck_9_15_12_selects_good_and_learns_only_the_complete_answer() -> None:
    before, before_world = ResourceState(), world()
    prepared = prepare(before, before_world)
    assert before == ResourceState() and before_world == world()
    assert not before_world.perspective("actor").facts
    rng = CountedDice([3, 3, 3, 5, 5, 5, 4, 4, 4])
    recognized = recognize_reaction(prepared, rng=rng)
    assert rng.draws == 0
    selected, luck = selected_luck(prepared, recognized, rng)
    assert selected.total == 15 and selected.outcome == "good"
    assert luck.receipts[0].attempts == ((3, 3, 3), (5, 5, 5), (4, 4, 4))
    assert luck.receipts[0].chosen_index == 1 and rng.draws == 9
    updated, learned, _ = finish(prepared, recognized, selected)
    assert learned.knowledge == (("actor", "answer"), ("npc", "answer"), ("npc", "private"))
    assert learned.perspective("npc") == before_world.perspective("npc")
    assert learned.perspective("other") == before_world.perspective("other")
    assert learned.perspective("actor").facts == (before_world.facts[0],)
    assert updated.revision == 1 and len(updated.receipts) == len(updated.events) == 1
    with pytest.raises(ConflictError, match="already committed"):
        finish(prepared, recognized, selected, state=updated, source_world=learned)
    assert before_world.knowledge == world().knowledge and not before.receipts


def test_ordinary_nine_remains_poor_and_does_not_reveal_answer() -> None:
    prepared = prepare()
    rng = CountedDice([3, 3, 3])
    recognized = recognize_reaction(prepared, rng=rng)
    selected = reaction_roll(PROFILE, recognized.modifiers, rng=rng)
    updated, learned, _ = finish(prepared, recognized, selected)
    assert selected.total == 9 and selected.outcome == "poor" and rng.draws == 3
    assert learned == world() and updated.revision == 1


@pytest.mark.parametrize(
    "dice,modifier,total,outcome",
    [
        ((3, 3, 3), 0, 9, "poor"),
        ((3, 3, 4), 0, 10, "neutral"),
        ((4, 4, 4), 0, 12, "neutral"),
        ((4, 4, 5), 0, 13, "good"),
        ((5, 5, 5), 0, 15, "good"),
        ((5, 5, 6), 0, 16, "very-good"),
        ((6, 6, 6), 0, 18, "very-good"),
        ((6, 6, 6), 1, 19, "excellent"),
    ],
)
def test_independent_b560_561_adjacent_bands(
    dice: tuple[int, int, int], modifier: int, total: int, outcome: str
) -> None:
    trace = evaluate_reaction(PROFILE, (ReactionModifier("situation", modifier, "scene"),), dice)
    assert (trace.total, trace.outcome) == (total, outcome)


def test_authored_simple_answer_includes_neutral_and_empty_disclosure_is_valid() -> None:
    disclosure = SocialDisclosure(("answer",), ("neutral", "good", "very-good", "excellent"))
    prepared = prepare(disclosure=disclosure)
    recognized = recognize_reaction(prepared, rng=RecordedDice([]))
    trace = evaluate_reaction(PROFILE, (), (3, 3, 4))
    _, learned, _ = finish(prepared, recognized, trace, disclosure=disclosure)
    assert ("actor", "answer") in learned.knowledge
    empty = SocialDisclosure()
    prepared = prepare(disclosure=empty)
    recognized = recognize_reaction(prepared, rng=RecordedDice([]))
    state, learned, _ = finish(prepared, recognized, trace, disclosure=empty)
    assert learned == world() and state.revision == 1


@pytest.mark.parametrize(
    "modifier,faces,index,total,outcome,learned",
    [
        (2, [3, 3, 3, 5, 6, 6, 4, 4, 4], 1, 19, "excellent", True),
        (-2, [3, 3, 3, 5, 5, 5, 4, 4, 4], 1, 13, "good", True),
        (0, [5, 5, 5, 4, 5, 6, 4, 4, 4], 0, 15, "good", True),
        (0, [1, 1, 1, 3, 3, 3, 2, 2, 2], 1, 9, "poor", False),
    ],
)
def test_signed_modifiers_equal_highest_and_unsuccessful_luck_still_spent(
    modifier: int, faces: list[int], index: int, total: int, outcome: str, learned: bool
) -> None:
    context = SocialContext(
        PROFILE, 0, modifiers=(ReactionModifier("situation", modifier, "scene"),)
    )
    prepared = prepare(context=context)
    rng = CountedDice(faces)
    recognized = recognize_reaction(prepared, rng=rng)
    trace, luck = selected_luck(prepared, recognized, rng)
    assert (trace.total, trace.outcome, luck.receipts[0].chosen_index) == (total, outcome, index)
    assert len(luck.receipts) == 1 and luck.receipts[0].available_at == 3600
    _, after, _ = finish(prepared, recognized, trace, context=context)
    assert (("actor", "answer") in after.knowledge) is learned
    assert rng.draws == 9


@pytest.mark.parametrize(
    "recognition_faces,recognized_flag,total", [([1, 1, 1], True, 17), ([6, 6, 6], False, 15)]
)
def test_unknown_recognition_once_then_three_attempts_and_remembered_next_trigger(
    recognition_faces: list[int], recognized_flag: bool, total: int
) -> None:
    context = SocialContext(
        PROFILE, 0, standing=Standing(reputations=(Reputation("hero", 2, recognition="sometimes"),))
    )
    prepared = prepare(context=context)
    rng = CountedDice(recognition_faces + [3, 3, 3, 5, 5, 5, 4, 4, 4])
    recognized = recognize_reaction(prepared, rng=rng)
    assert rng.draws == 3 and len(recognized.recognition) == 1
    assert recognized.recognition[0].recognized is recognized_flag
    trace, _ = selected_luck(prepared, recognized, rng)
    assert trace.total == total and rng.draws == 12
    updated, learned, _ = finish(prepared, recognized, trace, context=context)
    event_bytes = updated.model_dump_json()
    next_prepared = prepare(updated, learned, command(1), context)
    next_rng = CountedDice([4, 4, 4])
    next_recognized = recognize_reaction(next_prepared, rng=next_rng)
    assert next_rng.draws == 0 and next_recognized == recognized.model_copy(
        update={"preparation_provenance": next_prepared.provenance}
    )
    next_trace = reaction_roll(PROFILE, next_recognized.modifiers, rng=next_rng)
    again, _, _ = finish(
        next_prepared,
        next_recognized,
        next_trace,
        state=updated,
        source_world=learned,
        context=context,
    )
    assert next_rng.draws == 3 and again.events[0] == updated.events[0]
    assert updated.model_dump_json() == event_bytes


def test_inapplicable_reputation_never_draws_or_requires_recognition() -> None:
    context = SocialContext(
        PROFILE,
        0,
        standing=Standing(
            reputations=(Reputation("guild", 2, "small-class", "sometimes", ("guild",)),)
        ),
    )
    prepared = prepare(context=context)
    recognized = recognize_reaction(prepared, rng=RecordedDice([]))
    assert recognized.recognition == () and recognized.modifiers == ()
    finish(prepared, recognized, evaluate_reaction(PROFILE, (), (5, 5, 5)), context=context)


def test_preparation_round_trip_is_immutable_and_context_is_not_aliased() -> None:
    context = SocialContext(PROFILE, 0, standing=Standing(appearance="attractive"))
    prepared = prepare(context=context)
    assert PreparedReaction.model_validate_json(prepared.model_dump_json()) == prepared
    context.modifiers = (ReactionModifier("situation", 5, "changed"),)
    assert prepared.source.modifiers == ()
    with pytest.raises(SchemaError):
        prepared.provenance = "forged"
    assert prepared.source.standing is not None
    with pytest.raises(FrozenInstanceError):
        prepared.source.standing.appearance = "ugly"  # type: ignore[misc]


@pytest.mark.parametrize(
    "change",
    ["modifier", "standing", "audience", "required", "disclosure", "fact", "actor", "recognition"],
)
def test_current_source_changes_refuse_before_terminal_dice(change: str) -> None:
    context = SocialContext(
        PROFILE, 0, standing=Standing(reputations=(Reputation("hero", 2, recognition="sometimes"),))
    )
    prepared = prepare(context=context)
    state, current_world, disclosure = ResourceState(), world(), DISCLOSURE
    if change == "modifier":
        context.modifiers = (ReactionModifier("situation", 1, "different"),)
    elif change == "standing":
        context.standing = Standing(appearance="attractive")
    elif change == "audience":
        context.audience = Audience(visible=False)
    elif change == "required":
        context.required_fact_ids = ("private",)
    elif change == "disclosure":
        disclosure = SocialDisclosure(("answer",), ("neutral",))
    elif change == "fact":
        current_world = replace(
            current_world,
            facts=(replace(current_world.facts[0], value="Changed answer"), current_world.facts[1]),
        )
    elif change == "actor":
        current_world = replace(
            current_world,
            entities=(
                replace(current_world.entities[0], owner_id="other"),
                *current_world.entities[1:],
            ),
        )
    else:
        state = ResourceState(
            events=(
                ResourceEvent(
                    id="social:reaction:npc:earlier",
                    target_id="npc",
                    at=0,
                    kind=json.dumps(
                        {
                            "private": {
                                "recognition_actor_id": "actor",
                                "recognition": [
                                    {
                                        "reputation_id": "hero",
                                        "dice": [1, 1, 1],
                                        "total": 3,
                                        "target": 10,
                                        "recognized": True,
                                    }
                                ],
                            }
                        }
                    ),
                ),
            )
        )
    with pytest.raises(ValidationError, match="source or context changed"):
        validate_prepared_reaction(
            state,
            current_world,
            command(),
            context,
            disclosure,
            prepared,
            profile_id=PROFILE,
            player_actor_ids=("actor",),
            system=True,
        )


def test_only_resource_revision_can_drift_and_prepared_identity_cannot_be_forged() -> None:
    prepared = prepare()
    context = SocialContext(PROFILE, 0)
    state = ResourceState(revision=2)
    current = command().model_copy(update={"expected_revision": 2})
    validate_prepared_reaction(
        state,
        world(),
        current,
        context,
        DISCLOSURE,
        prepared,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )
    forged = prepared.model_copy(
        update={"command": command().model_copy(update={"actor_id": "other"})}
    )
    with pytest.raises(ValidationError, match="source or context changed"):
        validate_prepared_reaction(
            state,
            world(),
            current,
            context,
            DISCLOSURE,
            forged,
            profile_id=PROFILE,
            player_actor_ids=("actor",),
            system=True,
        )


@pytest.mark.parametrize(
    "change",
    [
        "subject",
        "same-actor",
        "player",
        "profile",
        "unknown-profile",
        "missing-fact",
        "duplicate-disclosure",
        "invalid-outcome",
        "duplicate-modifier",
        "committed",
        "trigger",
        "incapacitated",
        "authority",
        "influence",
    ],
)
def test_invalid_preparation_fails_without_any_dice(change: str) -> None:
    state, current_world, value = ResourceState(), world(), command()
    context, disclosure, system = SocialContext(PROFILE, 0), DISCLOSURE, True
    players: tuple[str, ...] = ("actor",)
    if change == "subject":
        value = value.model_copy(update={"subject_id": "missing"})
    elif change == "same-actor":
        value = value.model_copy(update={"subject_id": "actor"})
    elif change == "player":
        players = ("actor", "npc")
    elif change == "profile":
        context.profile_id = "gurps-lite-4e-2004"
    elif change == "unknown-profile":
        context.profile_id = "missing"
    elif change == "missing-fact":
        current_world = replace(current_world, knowledge=())
    elif change == "duplicate-disclosure":
        disclosure = SocialDisclosure(("answer", "answer"))
    elif change == "invalid-outcome":
        disclosure = SocialDisclosure(("answer",), ("invented",))
    elif change == "duplicate-modifier":
        modifier = ReactionModifier("situation", 1, "duplicate")
        context.modifiers = (modifier, modifier)
    elif change == "committed":
        state = ResourceState(receipts=(Receipt(command_id=value.id, digest="previous"),))
    elif change == "trigger":
        state = ResourceState(
            events=(
                ResourceEvent(id="social:reaction:npc:trigger0", target_id="npc", at=0, kind="{}"),
            )
        )
    elif change == "incapacitated":
        from wayfarer.engine.rules.fright import FrightEffect

        fright = TimedFright(
            id="fear",
            actor_id="actor",
            trigger_id="fear",
            effect=FrightEffect(table_total=4, condition="stunned", duration_seconds=1),
            started=0,
            due=1,
            active=True,
            recovery_target=10,
        )
        state = ResourceState(
            events=(
                ResourceEvent(
                    id="fright-runtime:fear", target_id="actor", at=0, kind=fright.model_dump_json()
                ),
            )
        )
    elif change == "authority":
        system = False
    else:
        value = value.model_copy(update={"kind": "influence"})
    with pytest.raises((ValidationError, ConflictError)):
        prepare_reaction(
            state,
            current_world,
            value,
            context,
            disclosure,
            profile_id=PROFILE,
            player_actor_ids=players,
            system=system,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("total", 16),
        ("outcome", "excellent"),
        ("modifiers", (ReactionModifier("situation", 1, "forged"),)),
        ("dice", (0, 5, 5)),
        ("dice", (True, 5, 5)),
    ],
)
def test_selected_trace_cannot_forge_score_modifiers_or_dice(field: str, value: object) -> None:
    prepared = prepare()
    recognized = recognize_reaction(prepared, rng=RecordedDice([]))
    forged = replace(evaluate_reaction(PROFILE, (), (5, 5, 5)), **{field: value})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        finish(prepared, recognized, forged)
    assert world().knowledge == (("npc", "answer"), ("npc", "private"))


@pytest.mark.parametrize("dice", [(1, 1), (1, 1, 1, 1), (1, 1, True), (1, 1, 7)])
def test_supplied_dice_strictly_require_three_d6(dice: tuple[int, ...]) -> None:
    with pytest.raises(ValidationError, match="three integer faces"):
        evaluate_reaction(PROFILE, (), cast(tuple[int, int, int], dice))


def test_new_prepared_generation_combines_standing_and_authored_retribution() -> None:
    state = ResourceState(
        events=(
            ResourceEvent(
                id="interrogation-retribution:prior:npc",
                target_id="npc",
                at=0,
                kind=json.dumps(
                    {
                        "interrogator_id": "actor",
                        "modifier": {
                            "kind": "situation",
                            "value": -2,
                            "source_id": "skill:interrogation:prior",
                            "hidden": False,
                        },
                        "reference": "B202/B494",
                    }
                ),
            ),
        )
    )
    context = SocialContext(PROFILE, 0, standing=Standing(appearance="attractive"))
    prepared = prepare(state, context=context)
    recognized = recognize_reaction(prepared, rng=RecordedDice([]))
    trace = evaluate_reaction(PROFILE, recognized.modifiers, (5, 5, 5))
    assert [modifier.value for modifier in trace.modifiers] == [1, -2]
    assert trace.total == 14 and trace.outcome == "good"
    updated, learned, _ = finish(prepared, recognized, trace, state=state, context=context)
    assert ("actor", "answer") in learned.knowledge and updated.events[0] == state.events[0]
    # A new explicit generation corrects the old precedence without changing
    # ordinary historical execution or rewriting the already-recorded event.
    _, _, legacy = apply_interaction(
        state, world(), command(), context, DISCLOSURE, rng=RecordedDice([5, 5, 5]), system=True
    )
    assert legacy.outcome == "very-good"


@pytest.mark.parametrize(
    "kind,with_standing,expected,next_draw",
    [
        ("reaction", False, "bb9c5678ec8281faf9716439d3912cf99169e048f44d04780897afc64353e6cd", 0),
        ("reaction", True, "4d877a738e406340a0e83b22d88339e9602c5e21c197c51fe6764f580a8cf940", 0),
        ("influence", True, "ce35e7c5227b37e3535ffb97c104b8f93047e13f4f3de4fa5daf6915bc05c6d5", 2),
    ],
)
def test_preexisting_seeded_legacy_receipt_bytes(
    kind: str, with_standing: bool, expected: str, next_draw: int
) -> None:
    # Captured from untouched fc9fd343 before factoring: not regenerated here.
    state, current_world, rng = ResourceState(), world(), SeededRandom("ab" * 32)
    standing = (
        Standing(reputations=(Reputation("hero", 2, recognition="sometimes"),))
        if with_standing
        else None
    )
    for index in range(2):
        state, current_world, _ = apply_interaction(
            state,
            current_world,
            command(index).model_copy(update={"kind": kind}),
            SocialContext(PROFILE, 12, standing=standing),
            DISCLOSURE,
            rng=rng,
            system=True,
        )
    assert hashlib.sha256(state.model_dump_json().encode()).hexdigest() == expected
    assert rng.randbelow(6) == next_draw
