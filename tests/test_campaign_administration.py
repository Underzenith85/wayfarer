"""Source-referenced campaign administration procedures for issues #501 and #502."""

import pytest
from test_actions import engine, seed

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.gurps_social import ReactionModifier
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, PlayState
from wayfarer.engine.simulation.campaign.administration import (
    AcquireKnowledge,
    ActivityAllocation,
    ActivityRule,
    AdministrationRules,
    AwardPoints,
    AwardRule,
    KnowledgeSource,
    ReactionContext,
    ResolveReaction,
    ResolveTrap,
    SettleTimeUse,
    TrapConsequence,
    TrapRule,
)
from wayfarer.engine.simulation.campaign.law import (
    CrimeRule,
    EnforcementProcedure,
    IssuePermit,
    Jurisdiction,
    LawRules,
    LegalityRule,
    PermitRule,
    RecordCrime,
    ResolveLawCase,
    availability,
)
from wayfarer.engine.simulation.campaign.procedures import CampaignCommand, CampaignOutcome
from wayfarer.engine.simulation.events import fold_play, play_events
from wayfarer.errors import ConflictError, ValidationError


def configured() -> ActionEngine:
    base = engine()
    administration = AdministrationRules(
        id="basic-campaign-administration",
        version=1,
        reactions=(
            ReactionContext(
                id="dock-warden",
                npc_id="b",
                modifiers=(ReactionModifier("situation", 2, "helped-town"),),
            ),
        ),
        knowledge=(
            KnowledgeSource(
                id="read-letter",
                fact_ids=("clue",),
                audience="actor",
                provenance="Authored letter contents",
            ),
        ),
        awards=(AwardRule(id="session-one", recipient_id="a", points=3, reason="Good play"),),
        activities=(
            ActivityRule(id="course", kind="study", credit_kind="study", load=50),
            ActivityRule(
                id="day-job",
                kind="job",
                credit_kind="study",
                load=50,
                credit_numerator=1,
                credit_denominator=4,
            ),
            ActivityRule(id="full-job", kind="job", credit_kind="job"),
        ),
        traps=(
            TrapRule(
                id="pit",
                trigger_id="cross-threshold",
                detection_target=10,
                disarm_target=10,
                avoidance_target=10,
                consequence=TrapConsequence(kind="injury", target="fall", amount=3),
            ),
        ),
    )
    law = LawRules(
        id="law",
        version=1,
        jurisdictions=(
            Jurisdiction(id="frontier", control_rating=1),
            Jurisdiction(id="city", control_rating=4),
        ),
        legality=(LegalityRule(definition_id="pistol", legality_class=3),),
        permits=(
            PermitRule(
                id="city-pistol", jurisdiction_id="city", definition_id="pistol", holder_id="a"
            ),
        ),
        crimes=(
            CrimeRule(
                id="theft", jurisdiction_id="city", offense="Theft", evidence_fact_ids=("clue",)
            ),
        ),
        procedures=(
            EnforcementProcedure(
                id="arrest",
                jurisdiction_id="city",
                kind="arrest",
                from_status="accused",
                success_status="arrested",
                failure_status="released",
                duration=10,
            ),
            EnforcementProcedure(
                id="trial",
                jurisdiction_id="city",
                kind="trial",
                from_status="arrested",
                success_status="acquitted",
                failure_status="convicted",
                resolution="reaction",
                duration=20,
            ),
            EnforcementProcedure(
                id="sentence",
                jurisdiction_id="city",
                kind="punishment",
                from_status="convicted",
                success_status="punished",
                failure_status="punished",
                duration=30,
                consequence="fine:500",
            ),
        ),
    )
    return ActionEngine(
        base.reviewer,
        base.resources,
        ActionRules(
            id="actions",
            version=1,
            checks=base.rules.checks,
            consumables=base.rules.consumables,
            administration=administration,
            law=law,
        ),
    )


def apply(
    reducer: ActionEngine,
    state: PlayState,
    command: CampaignCommand,
    dice: tuple[int, ...] = (1, 1, 1),
) -> tuple[PlayState, CampaignOutcome]:
    return reducer.campaign.apply(state, command, rng=RecordedDice(dice), system=True)


def test_private_knowledge_reaction_and_award_are_authoritative_and_exact_once() -> None:
    reducer = configured()
    state = seed(reducer)
    reacted, reaction = apply(
        reducer,
        state,
        ResolveReaction(id="react", actor_id="a", expected_revision=0, context_id="dock-warden"),
    )
    assert reaction.status == "bad" and reaction.private
    learned, result = apply(
        reducer,
        reacted,
        AcquireKnowledge(id="learn", actor_id="a", expected_revision=1, source_id="read-letter"),
    )
    assert result.status == "acquired"
    assert {fact.id for fact in learned.world.perspective("a").facts} == {"clue"}
    assert learned.world.perspective("b").facts == ()
    awarded, _ = apply(
        reducer,
        learned,
        AwardPoints(
            id="award-occurrence", actor_id="a", expected_revision=2, award_id="session-one"
        ),
    )
    assert awarded.advancement[-1].points == 3
    replayed, replay = apply(
        reducer,
        awarded,
        AwardPoints(
            id="award-occurrence", actor_id="a", expected_revision=2, award_id="session-one"
        ),
    )
    assert replay.status == "awarded" and replayed == awarded
    with pytest.raises(ConflictError, match="reused"):
        apply(
            reducer,
            awarded,
            AwardPoints(
                id="award-occurrence", actor_id="a", expected_revision=2, award_id="missing"
            ),
        )


def test_time_use_rejects_overlap_and_settles_shared_clock_in_order() -> None:
    reducer = configured()
    state = seed(reducer)
    command = SettleTimeUse(
        id="downtime",
        actor_id="a",
        expected_revision=0,
        allocations=(
            ActivityAllocation(activity_id="course", start=0, end=3600),
            ActivityAllocation(activity_id="day-job", start=0, end=3600),
        ),
    )
    settled, outcome = apply(reducer, state, command)
    assert outcome.status == "settled" and settled.resources.game_time == 3600
    assert [
        (credit.kind, credit.seconds) for credit in settled.administration.time_use[-1].credits
    ] == [("study", 3600), ("study", 900)]
    assert settled.resources.fired == ("expiry",)
    with pytest.raises(ValidationError, match="exceed"):
        apply(
            reducer,
            state,
            SettleTimeUse(
                id="overlap",
                actor_id="a",
                expected_revision=0,
                allocations=(
                    ActivityAllocation(activity_id="full-job", start=0, end=10),
                    ActivityAllocation(activity_id="course", start=0, end=10),
                ),
            ),
        )


def test_trap_discovery_disarm_trigger_and_replay_projection() -> None:
    reducer = configured()
    before = seed(reducer)
    discovered, result = apply(
        reducer,
        before,
        ResolveTrap(id="find", actor_id="a", expected_revision=0, trap_id="pit", action="detect"),
    )
    assert result.status == "discovered"
    armed, result = apply(
        reducer,
        discovered,
        ResolveTrap(id="disarm", actor_id="a", expected_revision=1, trap_id="pit", action="disarm"),
        (6, 6, 6),
    )
    assert result.status == "armed"
    triggered, result = apply(
        reducer,
        armed,
        ResolveTrap(
            id="trigger",
            actor_id="a",
            expected_revision=2,
            trap_id="pit",
            action="trigger",
            trigger_id="cross-threshold",
        ),
        (6, 6, 6),
    )
    assert result.status == "triggered"
    assert isinstance(result.consequence, TrapConsequence)
    assert result.consequence.kind == "injury"
    assert fold_play(before, play_events(before, triggered, "a")) == triggered


def test_legality_permits_evidence_and_enforcement_time_are_replay_safe() -> None:
    reducer = configured()
    state = seed(reducer)
    law_rules = reducer.rules.law
    assert law_rules is not None
    assert (
        availability(
            law_rules,
            state.law,
            jurisdiction_id="frontier",
            definition_id="pistol",
            actor_id="a",
        ).availability
        == "open"
    )
    assert (
        availability(
            law_rules,
            state.law,
            jurisdiction_id="city",
            definition_id="pistol",
            actor_id="a",
        ).availability
        == "licensed"
    )
    state, _ = apply(
        reducer,
        state,
        IssuePermit(id="permit", actor_id="a", expected_revision=0, permit_rule_id="city-pistol"),
    )
    assert (
        availability(
            law_rules,
            state.law,
            jurisdiction_id="city",
            definition_id="pistol",
            actor_id="a",
        ).availability
        == "permitted"
    )
    state, _ = apply(
        reducer,
        state,
        RecordCrime(
            id="case",
            actor_id="a",
            expected_revision=1,
            crime_rule_id="theft",
            subject_id="a",
            authority_ids=("b",),
        ),
    )
    assert state.law.evidence_for("b") == (("case", "clue"),)
    state, _ = apply(
        reducer,
        state,
        ResolveLawCase(
            id="arrest-cmd",
            actor_id="a",
            expected_revision=2,
            case_id="case",
            procedure_id="arrest",
        ),
    )
    state, trial = apply(
        reducer,
        state,
        ResolveLawCase(
            id="trial-cmd", actor_id="a", expected_revision=3, case_id="case", procedure_id="trial"
        ),
    )
    assert trial.status == "convicted"
    state, sentence = apply(
        reducer,
        state,
        ResolveLawCase(
            id="sentence-cmd",
            actor_id="a",
            expected_revision=4,
            case_id="case",
            procedure_id="sentence",
        ),
    )
    assert sentence.status == "punished" and sentence.consequence == "fine:500"
    assert state.resources.game_time == 60
    replayed, replay = apply(
        reducer,
        state,
        ResolveLawCase(
            id="sentence-cmd",
            actor_id="a",
            expected_revision=4,
            case_id="case",
            procedure_id="sentence",
        ),
    )
    assert replayed == state and replay == sentence
