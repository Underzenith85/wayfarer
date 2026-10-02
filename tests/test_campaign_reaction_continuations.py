"""B518-519/B508 numeric consequences at the private task-host domain boundary.

These are fast real-reducer tests. Store atomicity, privacy and seed-only command
reexecution are acceptance obligations of the task-host integration, not claims
made by these returned-state or receipt assertions.
"""

from dataclasses import replace

import pytest
from pydantic import ValidationError as ModelValidationError
from test_actions import seed
from test_campaign_administration import configured as administration_engine
from test_economics import configured as economics_engine
from test_economics import state as economics_state
from test_rescue_loyalty import rescue_campaign

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.social.gurps_social import (
    ReactionTrace,
    evaluate_reaction,
    reaction_roll,
)
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.administration import ResolveReaction
from wayfarer.engine.simulation.campaign.economics import EconomicsOutcome, FindHireling
from wayfarer.engine.simulation.campaign.law import LawCase, ResolveLawCase
from wayfarer.engine.simulation.campaign.reactions import (
    AdversarialReactionPolicy,
    CampaignReactionInteraction,
    CampaignReactionSource,
    JudgeReactionPolicy,
    PreparedCampaignReaction,
    ReactionKnowledgeBinding,
    RescuePermanentBonus,
)
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.errors import ConflictError, ValidationError


def initial_case() -> tuple[ActionEngine, PlayState, CampaignReactionSource]:
    engine = economics_engine()
    return (
        engine,
        economics_state(engine),
        CampaignReactionSource(
            interaction=CampaignReactionInteraction(actor_id="a", mode="active"),
            role="initial-loyalty",
            command=FindHireling(
                id="find-guide", actor_id="a", expected_revision=0, hireling_rule_id="guide"
            ),
        ),
    )


def rescue_case(old: int = 14) -> tuple[ActionEngine, PlayState, CampaignReactionSource]:
    engine, state, command = rescue_campaign(old)
    return (
        engine,
        state,
        CampaignReactionSource(
            role="rescue-loyalty",
            command=command,
            interaction=CampaignReactionInteraction(actor_id="a", mode="active"),
        ),
    )


def law_case(
    *, adversarial: bool = False
) -> tuple[ActionEngine, PlayState, CampaignReactionSource]:
    engine = administration_engine()
    if adversarial:
        assert engine.rules.law is not None
        rules = engine.rules.law.model_copy(
            update={
                "procedures": tuple(
                    rule.model_copy(update={"opposing_target": 10}) if rule.id == "trial" else rule
                    for rule in engine.rules.law.procedures
                )
            }
        )
        engine = ActionEngine(
            engine.reviewer, engine.resources, engine.rules.model_copy(update={"law": rules})
        )
    state = seed(engine)
    state = state.model_copy(
        update={
            "law": state.law.model_copy(
                update={
                    "cases": (
                        LawCase(
                            id="case",
                            crime_id="theft",
                            subject_id="a",
                            jurisdiction_id="city",
                            status="arrested",
                        ),
                    )
                }
            )
        }
    )
    return (
        engine,
        state,
        CampaignReactionSource(
            interaction=CampaignReactionInteraction(actor_id="a", mode="active"),
            role="law",
            npc_id="b",
            command=ResolveLawCase(
                id="judge-case",
                actor_id="a",
                expected_revision=0,
                case_id="case",
                procedure_id="trial",
            ),
            law_policy=AdversarialReactionPolicy()
            if adversarial
            else JudgeReactionPolicy(
                outcomes=("good", "very-good", "excellent"),
                reason="The authored judge acquits only on a favorable reaction",
            ),
        ),
    )


def administration_case() -> tuple[ActionEngine, PlayState, CampaignReactionSource]:
    base = administration_engine()
    assert base.rules.administration is not None
    rules = base.rules.administration.model_copy(
        update={
            "reactions": tuple(
                rule.model_copy(update={"modifiers": ()})
                for rule in base.rules.administration.reactions
            )
        }
    )
    engine = ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"administration": rules})
    )
    state = seed(engine)
    state = state.model_copy(update={"world": state.world.learn("b", "clue")})
    return (
        engine,
        state,
        CampaignReactionSource(
            interaction=CampaignReactionInteraction(actor_id="a", mode="active"),
            role="administration",
            command=ResolveReaction(
                id="warden-answer", actor_id="a", expected_revision=0, context_id="dock-warden"
            ),
            knowledge=ReactionKnowledgeBinding(
                source_id="read-letter", outcomes=("good", "very-good", "excellent")
            ),
        ),
    )


def prepare(
    engine: ActionEngine,
    state: PlayState,
    source: CampaignReactionSource,
    faces: tuple[int, ...] = (),
) -> PreparedCampaignReaction:
    rng = RecordedDice(faces)
    untouched, pending, terminal = engine.campaign.prepare_reaction(
        state, source, rng=rng, actor_id="a", command_id="pending:reaction", player_actor_ids=("a",)
    )
    assert untouched is state and terminal is None and pending is not None
    assert rng.exhausted()
    return PreparedCampaignReaction.model_validate_json(pending.model_dump_json())


def selected(pending: PreparedCampaignReaction, faces: tuple[int, int, int]) -> ReactionTrace:
    return evaluate_reaction(pending.profile_id, pending.modifiers, faces)


def test_initial_search_precedes_and_selected_loyalty_creates_the_real_contract() -> None:
    engine, state, source = initial_case()
    pending = prepare(engine, state, source, (2, 2, 2))
    assert state.economics.hirelings == ()
    assert state.resources.receipts == ()
    assert pending.frozen_search is not None and pending.frozen_search.total == 6
    assert pending.modifiers == ()
    resolved, outcome = engine.campaign.resolve_reaction(
        state, pending, selected(pending, (5, 5, 5)), rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert outcome == EconomicsOutcome(status="hireling-found")
    contract = resolved.economics.hirelings[0]
    assert contract.loyalty == 15
    assert contract.hireling_id == "b" and contract.employer_id == "a"
    ordinary, _ = engine.campaign.apply(
        state, source.command, rng=RecordedDice((2, 2, 2, 3, 3, 3)), system=True
    )
    assert ordinary.economics.hirelings[0].loyalty == 9
    assert resolved.world == state.world
    assert resolved.economics.accounts == state.economics.accounts
    assert resolved.resources.game_time == state.resources.game_time
    engine.validate(resolved)


def test_failed_search_is_terminal_and_does_not_create_a_luck_target() -> None:
    engine, state, source = initial_case()
    rng = RecordedDice((6, 6, 6))
    final, pending, outcome = engine.campaign.prepare_reaction(
        state, source, rng=rng, player_actor_ids=("a",)
    )
    assert rng.exhausted() and pending is None
    assert outcome == EconomicsOutcome(status="not-found")
    assert final.economics.hirelings == () and final.revision == 1
    assert len(final.resources.receipts) == 1
    retry, no_pending, repeated = engine.campaign.prepare_reaction(
        final, source, rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert retry is final and no_pending is None and repeated == outcome


@pytest.mark.parametrize(
    "old,faces,expected,grateful",
    [
        (14, (5, 5, 5), 18, True),
        (14, (3, 3, 3), 14, False),
        (16, (3, 3, 4), 16, True),
        (20, (6, 6, 6), 21, True),
    ],
)
def test_selected_rescue_changes_actual_loyalty_without_rechecking(
    old: int,
    faces: tuple[int, int, int],
    expected: int,
    grateful: bool,
) -> None:
    engine, state, source = rescue_case(old)
    pending = prepare(engine, state, source)
    assert state.economics.hirelings[0].loyalty == old
    assert state.economics.loyalty_checks == ()
    updated, outcome = engine.campaign.resolve_reaction(
        state, pending, selected(pending, faces), rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert updated.economics.hirelings[0].loyalty == expected
    assert len(updated.economics.loyalty_checks) == 1
    assert updated.economics.loyalty_checks[0].passed is grateful
    assert outcome.status == ("grateful" if grateful else "loyalty-unchanged")
    engine.validate(updated)


@pytest.mark.parametrize("faces,status", [((2, 2, 2), "convicted"), ((5, 5, 5), "acquitted")])
def test_law_selected_reaction_changes_case_once_and_advances_time_afterward(
    faces: tuple[int, int, int],
    status: str,
) -> None:
    engine, state, source = law_case()
    pending = prepare(engine, state, source)
    assert state.resources.game_time == 0
    assert state.law.cases[0].status == "arrested" and state.law.cases[0].procedures == ()
    trace = selected(pending, faces)
    updated, outcome = engine.campaign.resolve_reaction(
        state, pending, trace, rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert updated.law.cases[0].status == outcome.status == status
    assert updated.law.cases[0].procedures == ("trial",)
    assert updated.resources.game_time == 20
    retry, repeated = engine.campaign.resolve_reaction(
        updated, pending, trace, rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert retry is updated and repeated == outcome
    assert len(updated.resources.receipts) == 3  # Shared recognition, clock and law receipts.
    engine.validate(updated)


@pytest.mark.parametrize(
    "contest,verdict,winner,expected",
    [
        ((2, 2, 2, 3, 3, 3), (3, 3, 3), "a", "acquitted"),  # +3 -> Neutral
        ((3, 3, 3, 2, 2, 2), (5, 5, 5), "authority:city", "convicted"),  # -3 -> Neutral
        ((3, 3, 3, 3, 3, 3), (4, 4, 4), None, "acquitted"),  # Tie -> Neutral acquittal
        ((3, 3, 3, 2, 2, 2), (6, 6, 6), "authority:city", "acquitted"),  # Good beats loss
        ((2, 2, 2, 3, 3, 3), (1, 1, 1), "a", "convicted"),  # Bad overrides win
    ],
)
def test_adversarial_verdict_freezes_contest_and_neutral_follows_that_contest(
    contest: tuple[int, ...],
    verdict: tuple[int, int, int],
    winner: str | None,
    expected: str,
) -> None:
    engine, state, source = law_case(adversarial=True)
    pending = prepare(engine, state, source, contest)
    assert pending.frozen_contest is not None and pending.frozen_contest.winner == winner
    assert pending.frozen_contest.first.dice == contest[:3]
    assert pending.frozen_contest.second.dice == contest[3:]
    assert state.resources.game_time == 0 and state.law.cases[0].status == "arrested"
    updated, _ = engine.campaign.resolve_reaction(
        state, pending, selected(pending, verdict), rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert updated.law.cases[0].status == expected
    assert updated.resources.game_time == 20


@pytest.mark.parametrize("faces,learned", [((3, 3, 3), False), ((5, 5, 5), True)])
def test_administration_reaction_applies_bound_knowledge_not_only_receipt(
    faces: tuple[int, int, int],
    learned: bool,
) -> None:
    engine, state, source = administration_case()
    pending = prepare(engine, state, source)
    assert state.world.perspective("a").facts == ()
    assert state.administration.knowledge == ()
    updated, _ = engine.campaign.resolve_reaction(
        state, pending, selected(pending, faces), rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert {fact.id for fact in updated.world.perspective("a").facts} == (
        {"clue"} if learned else set()
    )
    assert updated.world.perspective("b").facts == state.world.perspective("b").facts
    assert len(updated.administration.knowledge) == int(learned)
    if learned:
        acquisition = updated.administration.knowledge[0]
        assert acquisition.source_id == "read-letter" and acquisition.actor_ids == ("a",)
        assert acquisition.id == source.command.id and acquisition.fact_ids == ("clue",)
    engine.validate(updated)


def test_simple_information_binding_can_include_neutral_explicitly() -> None:
    engine, state, source = administration_case()
    source = source.model_copy(
        update={
            "knowledge": ReactionKnowledgeBinding(
                source_id="read-letter", outcomes=("neutral", "good", "very-good", "excellent")
            )
        }
    )
    pending = prepare(engine, state, source)
    updated, outcome = engine.campaign.resolve_reaction(
        state, pending, selected(pending, (4, 4, 4)), rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert outcome.status == "neutral"
    assert {fact.id for fact in updated.world.perspective("a").facts} == {"clue"}


@pytest.mark.parametrize("role", ["initial-loyalty", "rescue-loyalty", "law", "administration"])
def test_json_roundtrip_clock_revision_drift_and_committed_retry_preserve_frozen_role(
    role: str,
) -> None:
    fixtures = {
        "initial-loyalty": initial_case,
        "rescue-loyalty": rescue_case,
        "law": law_case,
        "administration": administration_case,
    }
    engine, state, source = fixtures[role]()
    pending = prepare(engine, state, source, (2, 2, 2) if role == "initial-loyalty" else ())
    drifted = state.model_copy(
        update={"revision": 2, "resources": state.resources.model_copy(update={"revision": 2})}
    )
    engine.campaign.validate_reaction(drifted, pending, player_actor_ids=("a",))
    trace = selected(pending, (5, 5, 5))
    updated, outcome = engine.campaign.resolve_reaction(
        drifted, pending, trace, rng=RecordedDice(()), player_actor_ids=("a",)
    )
    restored = PlayState.model_validate_json(updated.model_dump_json())
    retried, repeated = engine.campaign.resolve_reaction(
        restored, pending, trace, rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert retried is restored and repeated == outcome
    assert restored.model_dump_json() == updated.model_dump_json()
    with pytest.raises(ConflictError, match="already committed"):
        engine.campaign.validate_reaction(restored, pending, player_actor_ids=("a",))


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", "b"),
        ("npc_id", "a"),
        ("preparation_id", "foreign"),
        ("context_digest", "0" * 64),
    ],
)
def test_foreign_or_modified_preparation_rejects_without_entropy(field: str, value: str) -> None:
    engine, state, source = rescue_case()
    pending = prepare(engine, state, source)
    forged = pending.model_copy(update={field: value})
    with pytest.raises((ConflictError, ValidationError)):
        engine.campaign.resolve_reaction(
            state,
            forged,
            selected(pending, (5, 5, 5)),
            rng=RecordedDice(()),
            player_actor_ids=("a",),
        )
    assert state.economics.hirelings[0].loyalty == 14


def test_foreign_campaign_rejects_even_with_identical_actor_ids() -> None:
    engine, state, source = rescue_case()
    pending = prepare(engine, state, source)
    with pytest.raises(ConflictError, match="context changed"):
        engine.campaign.validate_reaction(
            state.model_copy(update={"campaign_id": "another"}), pending, player_actor_ids=("a",)
        )


@pytest.mark.parametrize("change", ["contract", "actor", "world", "rules"])
def test_changed_source_context_rejects_before_target_dice(change: str) -> None:
    engine, state, source = rescue_case()
    pending = prepare(engine, state, source)
    if change == "contract":
        state = state.model_copy(
            update={
                "economics": state.economics.model_copy(
                    update={
                        "hirelings": (
                            state.economics.hirelings[0].model_copy(update={"loyalty": 16}),
                        )
                    }
                )
            }
        )
    elif change == "actor":
        state = state.model_copy(
            update={"actors": (state.actors[0].model_copy(update={"conditions": ("restrained",)}),)}
        )
    elif change == "world":
        state = state.model_copy(update={"world": state.world.learn("a", "clue")})
    else:
        assert engine.campaign.economics is not None
        engine.campaign.economics = engine.campaign.economics.model_copy(update={"version": 2})
    with pytest.raises(ConflictError, match="context changed"):
        engine.campaign.validate_reaction(state, pending, player_actor_ids=("a",))


@pytest.mark.parametrize("change", ["search", "contest", "selected"])
def test_forged_frozen_or_selected_trace_rejects(change: str) -> None:
    engine, state, source = law_case(adversarial=True) if change == "contest" else initial_case()
    pending = prepare(
        engine, state, source, (2, 2, 2, 3, 3, 3) if change == "contest" else (2, 2, 2)
    )
    trace = selected(pending, (5, 5, 5))
    if change == "search":
        assert pending.frozen_search is not None
        pending = pending.model_copy(
            update={"frozen_search": replace(pending.frozen_search, total=18)}
        )
    elif change == "contest":
        assert pending.frozen_contest is not None
        pending = pending.model_copy(
            update={"frozen_contest": replace(pending.frozen_contest, winner="authority:city")}
        )
    else:
        trace = replace(trace, total=99, outcome="excellent")
    with pytest.raises((ConflictError, ValidationError)):
        engine.campaign.resolve_reaction(
            state, pending, trace, rng=RecordedDice(()), player_actor_ids=("a",)
        )
    assert state.economics.hirelings == ()


def test_administration_rejects_unknown_unavailable_and_foreign_knowledge_before_dice() -> None:
    engine, state, source = administration_case()
    no_knowledge = state.model_copy(update={"world": replace(state.world, knowledge=())})
    with pytest.raises(ValidationError, match="does not know"):
        engine.campaign.prepare_reaction(
            no_knowledge, source, rng=RecordedDice(()), player_actor_ids=("a",)
        )
    with pytest.raises(ValidationError, match="source"):
        engine.campaign.prepare_reaction(
            state,
            source.model_copy(
                update={
                    "knowledge": ReactionKnowledgeBinding(source_id="foreign", outcomes=("good",))
                }
            ),
            rng=RecordedDice(()),
            player_actor_ids=("a",),
        )
    with pytest.raises(ValidationError, match="different NPC"):
        engine.campaign.prepare_reaction(
            state,
            source.model_copy(update={"npc_id": "a"}),
            rng=RecordedDice(()),
            player_actor_ids=("a",),
        )


def test_actor_source_revision_and_role_are_bound_before_prerequisites() -> None:
    engine, state, source = initial_case()
    with pytest.raises(ValidationError, match="owner"):
        engine.campaign.prepare_reaction(
            state, source, rng=RecordedDice(()), actor_id="b", player_actor_ids=("a",)
        )
    with pytest.raises(ConflictError, match="revision"):
        engine.campaign.prepare_reaction(
            state.model_copy(
                update={"resources": state.resources.model_copy(update={"revision": 1})}
            ),
            source,
            rng=RecordedDice(()),
            player_actor_ids=("a",),
        )
    with pytest.raises(ModelValidationError, match="role"):
        CampaignReactionSource.model_validate(source.model_copy(update={"role": "rescue-loyalty"}))


def test_legacy_seeded_initial_loyalty_matches_selected_continuation() -> None:
    engine, state, source = initial_case()
    ordinary_rng = SeededRandom("5b" * 32)
    continuation_rng = SeededRandom("5b" * 32)
    ordinary, outcome = engine.campaign.apply(state, source.command, rng=ordinary_rng, system=True)
    prepared_state, pending, terminal = engine.campaign.prepare_reaction(
        state, source, rng=continuation_rng, player_actor_ids=("a",)
    )
    if pending is None:
        assert (
            prepared_state.model_dump_json() == ordinary.model_dump_json() and terminal == outcome
        )
    else:
        trace = reaction_roll(pending.profile_id, pending.modifiers, rng=continuation_rng)
        continued, resumed = engine.campaign.resolve_reaction(
            prepared_state, pending, trace, rng=continuation_rng, player_actor_ids=("a",)
        )
        assert continued.model_dump_json() == ordinary.model_dump_json() and resumed == outcome
    assert ordinary_rng.randbelow(1_000_000) == continuation_rng.randbelow(1_000_000)


def test_statted_npc_is_allowed_but_current_player_control_rejects() -> None:
    engine, state, source = rescue_case()
    state = state.model_copy(
        update={
            "actors": state.actors
            + (state.actors[0].model_copy(update={"actor_id": "b", "approval": None}),)
        }
    )
    pending = prepare(engine, state, source)
    engine.campaign.validate_reaction(state, pending, player_actor_ids=("a",))
    with pytest.raises(ValidationError, match="NPC"):
        engine.campaign.validate_reaction(state, pending, player_actor_ids=("a", "b"))
    with pytest.raises(ValidationError, match="NPC"):
        engine.campaign.prepare_reaction(
            state, source, rng=RecordedDice(()), player_actor_ids=("a", "b")
        )


def test_current_resources_are_bound_without_binding_clock_receipts() -> None:
    engine, state, source = rescue_case()
    pending = prepare(engine, state, source)
    changed = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        pool.model_copy(update={"current": 1}) if pool.id == "hp:a" else pool
                        for pool in state.resources.pools
                    )
                }
            )
        }
    )
    with pytest.raises(ConflictError, match="context changed"):
        engine.campaign.validate_reaction(changed, pending, player_actor_ids=("a",))


@pytest.mark.parametrize("role", ["initial-loyalty", "rescue-loyalty", "law", "administration"])
def test_cancel_consumes_original_source_identity_without_effect_or_prerequisite_reroll(
    role: str,
) -> None:
    fixtures = {
        "initial-loyalty": initial_case,
        "rescue-loyalty": rescue_case,
        "law": law_case,
        "administration": administration_case,
    }
    engine, state, source = fixtures[role]()
    pending = prepare(engine, state, source, (2, 2, 2) if role == "initial-loyalty" else ())
    cancelled, outcome = engine.campaign.cancel_reaction(state, pending)
    assert outcome.status == "cancelled"
    assert cancelled.world == state.world and cancelled.economics == state.economics
    assert cancelled.law == state.law and cancelled.administration == state.administration
    assert cancelled.resources.game_time == state.resources.game_time
    assert cancelled.revision == state.revision + 1
    ordinary_retry, retry_outcome = engine.campaign.apply(
        cancelled, source.command, rng=RecordedDice(()), system=True
    )
    assert ordinary_retry is cancelled and retry_outcome == outcome
    retry, repeated = engine.campaign.cancel_reaction(cancelled, pending)
    assert retry is cancelled and repeated == outcome
    _, no_pending, terminal = engine.campaign.prepare_reaction(
        cancelled, source, rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert no_pending is None and terminal == outcome


def test_cancel_is_available_after_source_rules_and_receiving_build_loss() -> None:
    engine, state, source = initial_case()
    pending = prepare(engine, state, source, (2, 2, 2))
    invalid = state.model_copy(update={"actors": ()})
    engine.campaign.economics = None
    cancelled, result = engine.campaign.cancel_reaction(invalid, pending)
    assert result.status == "cancelled" and cancelled.economics.hirelings == ()
    assert cancelled.actors == () and cancelled.resources.game_time == 0
    assert len(cancelled.resources.receipts) == 2


def test_cancel_adversarial_trial_keeps_frozen_contest_without_case_or_time_change() -> None:
    engine, state, source = law_case(adversarial=True)
    pending = prepare(engine, state, source, (2, 2, 2, 3, 3, 3))
    cancelled, result = engine.campaign.cancel_reaction(state, pending)
    assert pending.frozen_contest is not None and pending.frozen_contest.winner == "a"
    assert result.status == "cancelled" and cancelled.law == state.law
    assert cancelled.resources.game_time == 0
    ordinary, receipt = engine.campaign.apply(
        cancelled, source.command, rng=RecordedDice(()), system=True
    )
    assert ordinary is cancelled and receipt == result


@pytest.mark.parametrize("loss", ["serious-injury", "death"])
@pytest.mark.parametrize(
    "old,faces,condition,expected",
    [
        (14, (5, 5, 5), "grateful", 20),  # Selected base18, then a separate permanent +2.
        (16, (3, 3, 4), "grateful", 18),  # Base stays16, then +2; reaction is still13.
        (14, (3, 3, 3), "grateful", 14),  # Neutral: this GM chose no permanent addition.
        (14, (3, 3, 3), "any-reaction", 16),  # Explicit GM choice, not an inferred source rule.
    ],
)
def test_gm_rescue_permanent_bonus_is_separate_from_reaction_base_and_applied_once(
    loss: str,
    old: int,
    faces: tuple[int, int, int],
    condition: str,
    expected: int,
) -> None:
    engine, state, source = rescue_case(old)
    # A dead/former PC remains a known actor without requiring a current build,
    # positive HP, present consciousness, or a current control assignment.
    state = state.model_copy(
        update={
            "world": replace(
                state.world,
                entities=state.world.entities
                + (Entity("rescuer", EntityKind.ACTOR, "The fallen rescuer", location_id="dock"),),
            )
        }
    )
    bonus = RescuePermanentBonus.model_validate(
        {
            "amount": 2,
            "rescuer_actor_id": "rescuer",
            "loss": loss,
            "applies_on": condition,
            "reason": "The GM adjudicated the rescuer's sacrifice in this specific rescue",
        }
    )
    source = source.model_copy(update={"rescue_bonus": bonus})
    pending = prepare(engine, state, source)
    trace = selected(pending, faces)
    assert trace.total == sum(faces) + 3
    assert sum(modifier.value for modifier in pending.modifiers) == 3
    assert state.economics.hirelings[0].loyalty == old
    updated, outcome = engine.campaign.resolve_reaction(
        state, pending, trace, rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert updated.economics.hirelings[0].loyalty == expected
    if faces == (3, 3, 3):
        assert outcome.status == (
            "loyalty-adjusted" if condition == "any-reaction" else "loyalty-unchanged"
        )
        assert updated.economics.loyalty_checks[0].passed is False
    else:
        assert outcome.status == "grateful"
    assert updated.economics.loyalty_checks[0].loyalty_after == expected
    assert len(updated.economics.loyalty_checks) == 1
    retried, repeated = engine.campaign.resolve_reaction(
        updated, pending, trace, rng=RecordedDice(()), player_actor_ids=("a",)
    )
    assert retried is updated and repeated == outcome
    # Cancellation of the same prepared opportunity, evaluated from its original
    # state, applies neither the reaction nor the GM's contingent addition.
    cancelled, result = engine.campaign.cancel_reaction(state, pending)
    assert result.status == "cancelled"
    assert cancelled.economics == state.economics
    ordinary, prior = engine.campaign.apply(
        cancelled, source.command, rng=RecordedDice(()), system=True
    )
    assert ordinary is cancelled and prior == result


def test_gm_rescue_bonus_binding_and_changed_decision_reject_before_target_dice() -> None:
    engine, state, source = rescue_case()
    bonus = RescuePermanentBonus(
        amount=2,
        rescuer_actor_id="a",
        loss="serious-injury",
        applies_on="grateful",
        reason="The employer risked their life",
    )
    source = source.model_copy(update={"rescue_bonus": bonus})
    pending = prepare(engine, state, source)
    changed = pending.model_copy(
        update={
            "source": source.model_copy(
                update={"rescue_bonus": bonus.model_copy(update={"amount": 3})}
            )
        }
    )
    with pytest.raises(ConflictError, match="context changed"):
        engine.campaign.validate_reaction(state, changed, player_actor_ids=("a",))
    for actor_id in ("unknown", "b", "dock"):
        invalid = source.model_copy(
            update={"rescue_bonus": bonus.model_copy(update={"rescuer_actor_id": actor_id})}
        )
        with pytest.raises(ValidationError, match="named rescuer"):
            engine.campaign.prepare_reaction(
                state, invalid, rng=RecordedDice(()), player_actor_ids=("a",)
            )
    _, _, initial = initial_case()
    with pytest.raises(ModelValidationError, match="rescue-loyalty"):
        CampaignReactionSource.model_validate(initial.model_copy(update={"rescue_bonus": bonus}))
    for amount in (0, -1, True):
        with pytest.raises(ModelValidationError):
            RescuePermanentBonus.model_validate(bonus.model_copy(update={"amount": amount}))


def test_recorded_source_revision_cannot_be_rewritten_even_when_current_clock_revision_can_drift() -> (
    None
):
    engine, state, source = rescue_case()
    pending = prepare(engine, state, source)
    changed_source = source.model_copy(
        update={"command": source.command.model_copy(update={"expected_revision": 7})}
    )
    changed = pending.model_copy(update={"source": changed_source})
    with pytest.raises(ConflictError, match="context changed"):
        engine.campaign.validate_reaction(state, changed, player_actor_ids=("a",))
