"""B187/B359: selected fallback continues both frozen Diplomacy entry routes.

The B66 choice replaces only the reaction. The preceding contest still has its
original success/failure and dice; B359's better response determines disclosure.
"""

import hashlib
import json
from dataclasses import replace

import pytest
from test_prepared_reactions import (
    DISCLOSURE,
    EMPTY,
    PROFILE,
    CountedDice,
    command,
    selected_luck,
    world,
)

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.skills.mundane.social.inventory import Verdict
from wayfarer.engine.rules.social.gurps_social import InfluenceConditions, evaluate_reaction
from wayfarer.engine.rules.social.social_hooks import Reputation, Standing
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social.diplomacy import (
    PreparedDiplomacy,
    prepare_diplomacy,
    resolve_prepared_diplomacy,
    validate_prepared_diplomacy,
)
from wayfarer.engine.simulation.social.social import (
    SocialContext,
    SocialDisclosure,
    apply_interaction,
)
from wayfarer.errors import ConflictError, ValidationError


@pytest.fixture(params=["influence", "skill"])
def route(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def context(route: str) -> SocialContext:
    return SocialContext(
        PROFILE,
        8,
        will=12,
        skill="diplomacy",
        procedure_id="skill:diplomacy" if route == "skill" else None,
        skill_level=8,
        conditions=frozenset({"audience-audible", "shared-language"}),
    )


def test_lost_contest_good_selected_fallback_learns_once_through_actual_path(route: str) -> None:
    before, before_world, source = ResourceState(), world(), context(route)
    value = command().model_copy(update={"kind": route})
    rng = CountedDice([5, 5, 5, 3, 3, 3, 3, 3, 3, 5, 5, 5, 4, 4, 4])
    prepared = prepare_diplomacy(
        before,
        before_world,
        value,
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=rng,
        system=True,
    )
    assert rng.draws == 6 and before == EMPTY and before_world == world()
    assert prepared.influence.outcome == "bad" and prepared.influence.fallback is None
    contest = prepared.influence.contest
    assert contest is not None and contest.winner == "npc"
    assert (contest.first.dice, contest.second.dice) == ((5, 5, 5), (3, 3, 3))
    assert not contest.first.outcome.succeeded and contest.second.outcome.succeeded
    frozen_bytes = prepared.model_dump_json()
    prepared = PreparedDiplomacy.model_validate_json(frozen_bytes)
    selected, luck = selected_luck(prepared.reaction, prepared.recognized, rng)
    assert selected.total == 15 and selected.outcome == "good" and rng.draws == 15
    assert luck.receipts[0].chosen_index == 1
    after, learned, outcome = resolve_prepared_diplomacy(
        before,
        before_world,
        value,
        source,
        DISCLOSURE,
        prepared,
        selected,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )
    assert outcome.kind == route and outcome.outcome == "good"
    assert learned.knowledge == (("actor", "answer"), ("npc", "answer"), ("npc", "private"))
    assert learned.perspective("npc") == before_world.perspective("npc")
    assert learned.perspective("other") == before_world.perspective("other")
    assert after.revision == 1 and len(after.receipts) == len(after.events) == 1
    private = json.loads(after.events[0].kind)["private"]
    influence = private if route == "influence" else private["influence"]
    assert influence["contest"]["first"]["dice"] == [5, 5, 5]
    assert influence["contest"]["second"]["dice"] == [3, 3, 3]
    assert influence["contest"]["winner"] == "npc"
    assert influence["fallback"]["dice"] == [5, 5, 5] and influence["outcome"] == "good"
    if route == "skill":
        assert private["verdict"] == "failure" and private["effect"]["id"] == "diplomacy-rebuffed"
        assert prepared.skill is not None and prepared.skill.verdict is Verdict.FAILURE
    assert prepared.model_dump_json() == frozen_bytes and rng.draws == 15
    with pytest.raises(ConflictError, match="already committed"):
        resolve_prepared_diplomacy(
            after,
            learned,
            value.model_copy(update={"expected_revision": 1}),
            source,
            DISCLOSURE,
            prepared,
            selected,
            profile_id=PROFILE,
            player_actor_ids=("actor",),
            system=True,
        )


@pytest.mark.parametrize(
    "contest_faces,selected_faces,expected,learned",
    [
        ([1, 1, 1, 6, 6, 6], (3, 3, 3), "good", True),
        ([5, 5, 5, 3, 3, 3], (3, 3, 3), "poor", False),
        ([5, 5, 5, 3, 3, 3], (1, 1, 1), "bad", False),
    ],
)
def test_better_of_frozen_influence_and_fallback_not_merely_the_selected_band(
    route: str,
    contest_faces: list[int],
    selected_faces: tuple[int, int, int],
    expected: str,
    learned: bool,
) -> None:
    value, source = command().model_copy(update={"kind": route}), context(route)
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=RecordedDice(contest_faces),
        system=True,
    )
    selected = evaluate_reaction(PROFILE, (), selected_faces)
    _, after, outcome = resolve_prepared_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        prepared,
        selected,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )
    assert outcome.outcome == expected and (("actor", "answer") in after.knowledge) is learned


@pytest.mark.parametrize(
    "automatic,initial",
    [
        (InfluenceConditions(indomitable=True), "bad"),
        (InfluenceConditions(slave_mentality=True), "good"),
    ],
)
def test_automatic_preceding_influence_consumes_zero_dice(
    route: str, automatic: InfluenceConditions, initial: str
) -> None:
    value, source = command().model_copy(update={"kind": route}), context(route)
    source.influence_conditions = automatic
    rng = CountedDice([])
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=rng,
        system=True,
    )
    assert (
        rng.draws == 0
        and prepared.influence.contest is None
        and prepared.influence.outcome == initial
    )
    _, learned, outcome = resolve_prepared_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        prepared,
        evaluate_reaction(PROFILE, (), (5, 5, 5)),
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )
    assert outcome.outcome == "good" and ("actor", "answer") in learned.knowledge


def test_recognition_is_frozen_before_contest_and_never_repeated_at_selection(route: str) -> None:
    value, source = command().model_copy(update={"kind": route}), context(route)
    source.standing = Standing(reputations=(Reputation("hero", 2, recognition="sometimes"),))
    rng = CountedDice([1, 1, 1, 5, 5, 5, 3, 3, 3, 3, 3, 3, 5, 5, 5, 4, 4, 4])
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=rng,
        system=True,
    )
    assert rng.draws == 9 and prepared.recognized.recognition[0].dice == (1, 1, 1)
    assert prepared.influence.contest is not None
    assert prepared.influence.contest.first.base_target == 10
    selected, _ = selected_luck(prepared.reaction, prepared.recognized, rng)
    assert rng.draws == 18 and selected.total == 17 and selected.outcome == "very-good"
    after, learned, _ = resolve_prepared_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        prepared,
        selected,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )
    assert rng.draws == 18
    next_rng = CountedDice([5, 5, 5, 3, 3, 3])
    next_prepared = prepare_diplomacy(
        after,
        learned,
        command(1).model_copy(update={"kind": route}),
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=next_rng,
        system=True,
    )
    assert (
        next_rng.draws == 6
        and next_prepared.recognized.recognition == prepared.recognized.recognition
    )


def test_skill_named_condition_bonus_belongs_to_contest_not_fallback() -> None:
    source = context("skill")
    source.conditions |= {"audible-voice-trait"}
    value = command().model_copy(update={"kind": "skill"})
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=RecordedDice([5, 5, 5, 3, 3, 3]),
        system=True,
    )
    assert prepared.skill is not None and prepared.skill.effective_skill == 10
    assert (
        prepared.influence.contest is not None
        and prepared.influence.contest.first.base_target == 10
    )
    assert prepared.recognized.modifiers == ()
    after, _, outcome = resolve_prepared_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        prepared,
        evaluate_reaction(PROFILE, (), (5, 5, 5)),
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )
    assert outcome.outcome == "good"
    assert json.loads(after.events[0].kind)["private"]["influence"]["fallback"]["total"] == 15


@pytest.mark.parametrize(
    "outcomes,selected,learned",
    [
        (("neutral",), (3, 3, 4), True),
        (("diplomacy-rebuffed",), (3, 3, 3), True),
        (("diplomacy-persuaded",), (5, 5, 5), False),
    ],
)
def test_authored_skill_prerequisite_policy_is_distinct_from_combined_reaction(
    outcomes: tuple[str, ...], selected: tuple[int, int, int], learned: bool
) -> None:
    source, value = context("skill"), command().model_copy(update={"kind": "skill"})
    disclosure = SocialDisclosure(("answer",), outcomes)
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        disclosure,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=RecordedDice([5, 5, 5, 3, 3, 3]),
        system=True,
    )
    after, changed, outcome = resolve_prepared_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        disclosure,
        prepared,
        evaluate_reaction(PROFILE, (), selected),
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )
    assert (("actor", "answer") in changed.knowledge) is learned
    assert outcome.outcome in ("poor", "neutral", "good")
    assert json.loads(after.events[0].kind)["private"]["effect"]["id"] == "diplomacy-rebuffed"


@pytest.mark.parametrize(
    "field", ["target", "will", "standing", "conditions", "identity", "contest", "selected"]
)
def test_context_or_frozen_prerequisite_forgery_refuses_without_new_dice(
    route: str, field: str
) -> None:
    source, value = context(route), command().model_copy(update={"kind": route})
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=RecordedDice([5, 5, 5, 3, 3, 3]),
        system=True,
    )
    selected = evaluate_reaction(PROFILE, (), (5, 5, 5))
    if field == "target":
        source.target += 1
        source.skill_level += 1
    elif field == "will":
        source.will += 1
    elif field == "standing":
        source.standing = Standing(appearance="attractive")
    elif field == "conditions":
        source.influence_conditions = InfluenceConditions(slave_mentality=True)
    elif field == "identity":
        value = value.model_copy(update={"id": "another-command"})
    elif field == "contest":
        assert prepared.influence.contest is not None
        prepared = prepared.model_copy(
            update={
                "influence": replace(
                    prepared.influence, contest=replace(prepared.influence.contest, winner="actor")
                )
            }
        )
    else:
        selected = replace(selected, total=18, outcome="very-good")
    with pytest.raises(ValidationError):
        resolve_prepared_diplomacy(
            EMPTY,
            world(),
            value,
            source,
            DISCLOSURE,
            prepared,
            selected,
            profile_id=PROFILE,
            player_actor_ids=("actor",),
            system=True,
        )
    assert world().knowledge == (("npc", "answer"), ("npc", "private"))


def test_invalid_skill_precondition_refuses_before_reputation_recognition() -> None:
    source = context("skill")
    source.conditions = frozenset()
    source.standing = Standing(reputations=(Reputation("hero", 2, recognition="sometimes"),))
    rng = CountedDice([])
    with pytest.raises(ValidationError, match="prerequisite"):
        prepare_diplomacy(
            EMPTY,
            world(),
            command().model_copy(update={"kind": "skill"}),
            source,
            DISCLOSURE,
            profile_id=PROFILE,
            player_actor_ids=("actor",),
            rng=rng,
            system=True,
        )
    assert rng.draws == 0


def test_revision_only_rebinds_both_routes_without_reopening_the_contest(route: str) -> None:
    source, value = context(route), command().model_copy(update={"kind": route})
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=RecordedDice([5, 5, 5, 3, 3, 3]),
        system=True,
    )
    validate_prepared_diplomacy(
        ResourceState(revision=3),
        world(),
        value.model_copy(update={"expected_revision": 3}),
        source,
        DISCLOSURE,
        prepared,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
    )


def test_original_seeded_skill_diplomacy_receipt_bytes_remain_exact() -> None:
    # Captured from untouched fc9fd343; the staged result has a new response
    # projection, but historical immediate commands retain their exact bytes.
    state, current_world, rng = ResourceState(), world(), SeededRandom("ab" * 32)
    source = context("skill")
    source.target = 0
    source.standing = Standing(reputations=(Reputation("hero", 2, recognition="sometimes"),))
    for index in range(2):
        state, current_world, _ = apply_interaction(
            state,
            current_world,
            command(index).model_copy(update={"kind": "skill"}),
            source,
            DISCLOSURE,
            rng=rng,
            system=True,
        )
    assert (
        hashlib.sha256(state.model_dump_json().encode()).hexdigest()
        == "b1511bf90c8d81cb6af6ba8bb6d560d8bdddeb49646486c523f1dc2d04bad19c"
    )
    assert rng.randbelow(6) == 2
