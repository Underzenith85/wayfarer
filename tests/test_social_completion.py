"""Independent social regression cases, not expectations derived from the code.

Sources: Basic Set Characters, Fourth Edition, third printing, B121;
Campaigns, Fourth Edition, fourth printing, B359-361 and B428. These numeric
examples do not certify the registry's distinct selected-printing baseline.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_mundane_trait_runtime import prepare as prepare_traits
from test_social_dispatch import PROFILE, command, prepare, world

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import Modifier, RecordedDice
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.rules.gurps_social import (
    InfluenceConditions,
    InfluenceSkill,
    fright_roll,
    influence_roll,
    self_control_roll,
)
from wayfarer.engine.rules.skill_types import ControllingAttribute, Difficulty, SkillSpec, Specialty
from wayfarer.engine.rules.traits import TraitOptions, TraitRules
from wayfarer.engine.simulation.fright import (
    TimedFright,
    aftermath_penalty,
    apply_effect,
    effects,
    recover,
    save,
)
from wayfarer.engine.simulation.npcs import NPCSocialTrigger
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social import SocialCommand, SocialContext, apply_social
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.npcs import social_occurrence


@pytest.mark.parametrize(
    "skill,expected",
    [
        ("diplomacy", "good"),
        ("fast-talk", "good"),
        ("intimidation", "good"),
        ("savoir-faire", "good"),
        ("sex-appeal", "very-good"),
        ("streetwise", "good"),
    ],
)
def test_b359_all_influence_procedures(skill: InfluenceSkill, expected: str) -> None:
    # Skill 12 rolls 9 (margin 3), Will 10 rolls 12 (margin -2).
    dice = [3, 3, 3, 4, 4, 4] + ([3, 3, 3] if skill == "diplomacy" else [])
    result = influence_roll(PROFILE, skill, "a", "npc", 12, 10, (), rng=RecordedDice(dice))
    assert result.outcome == expected
    assert result.contest is not None and result.contest.victory_margin == 5


@pytest.mark.parametrize(
    "conditions,outcome,reason",
    [
        (InfluenceConditions(indomitable=True), "bad", "indomitable"),
        (InfluenceConditions(unfazeable=True), "bad", "unfazeable"),
        (InfluenceConditions(slave_mentality=True), "good", "slave-mentality"),
    ],
)
def test_b359_automatic_influence_does_not_draw_contest_dice(
    conditions: InfluenceConditions, outcome: str, reason: str
) -> None:
    result = influence_roll(
        PROFILE,
        "intimidation",
        "a",
        "npc",
        12,
        10,
        (),
        conditions=conditions,
        rng=RecordedDice([]),
    )
    assert result.contest is None and result.automatic == reason and result.outcome == outcome


def test_b359_empathy_specious_threat_and_diplomacy_fallback() -> None:
    result = influence_roll(
        PROFILE,
        "fast-talk",
        "a",
        "npc",
        12,
        10,
        (),
        conditions=InfluenceConditions(indomitable=True, appropriate_empathy=True),
        rng=RecordedDice([3, 3, 3, 4, 4, 4]),
    )
    assert result.contest is not None and result.outcome == "good"
    result = influence_roll(
        PROFILE,
        "intimidation",
        "a",
        "npc",
        10,
        10,
        (),
        conditions=InfluenceConditions(specious_intimidation=True),
        rng=RecordedDice([3, 3, 3, 3, 3, 3]),
    )
    assert result.outcome == "very-bad"  # A tie is not an influence win.
    result = influence_roll(
        PROFILE,
        "diplomacy",
        "a",
        "npc",
        12,
        10,
        (),
        conditions=InfluenceConditions(indomitable=True),
        rng=RecordedDice([5, 5, 5]),
    )
    assert result.automatic == "indomitable" and result.outcome == "good"
    assert result.fallback is not None and result.fallback.total == 15


def test_invalid_social_configuration_rejects_before_randomness() -> None:
    with pytest.raises(ValidationError, match="Conflicting"):
        influence_roll(
            PROFILE,
            "fast-talk",
            "a",
            "npc",
            12,
            10,
            (),
            rng=RecordedDice([]),
            conditions=InfluenceConditions(indomitable=True, slave_mentality=True),
        )
    with pytest.raises(ValidationError, match="Basic Set"):
        influence_roll(
            "gurps-lite-4e-2004",
            "fast-talk",
            "a",
            "npc",
            12,
            10,
            (),
            rng=RecordedDice([]),
            conditions=InfluenceConditions(slave_mentality=True),
        )
    for ht in (0, -1):
        with pytest.raises(ValidationError, match="positive HT"):
            fright_roll(PROFILE, 10, ht=ht, rng=RecordedDice([]))
    with pytest.raises(SchemaError, match="Unsupported influence"):
        NPCSocialTrigger(kind="influence", subject_id="npc", skill_id="skill:invented")
    with pytest.raises(SchemaError, match="Intimidation"):
        NPCSocialTrigger(kind="influence", subject_id="npc", specious_intimidation=True)


def test_b121_self_control_stress_uses_rating_instead_of_will() -> None:
    check = self_control_roll(
        PROFILE,
        -5,
        1,
        TraitOptions(self_control=15),
        TraitRules(PROFILE, self_control=True),
        modifiers=(Modifier(-5, "Stress", "stress", "B121"),),
        rng=RecordedDice([3, 4, 4]),
    )
    assert check.base_target == 15 and check.effective_target == 10
    assert not check.outcome.succeeded


async def test_authored_self_control_modifier_approved_trait_and_restart(tmp_path: Path) -> None:
    cid, play = await prepare_traits(
        tmp_path, Purchase(definition_id="trait:bad-temper", trait=TraitOptions(self_control=15))
    )
    before = play._load(await play.store.read(cid))
    trigger = NPCSocialTrigger(
        kind="self-control",
        subject_id="a",
        trait_id="trait:bad-temper",
        modifier=-5,
    )
    play.rng = RecordedDice([3, 4, 4])
    updated = social_occurrence(play, before, "npc", trigger, "private-insult")
    event = next(e for e in updated.resources.events if e.id.startswith("social:"))
    private = json.loads(event.kind)["private"]
    public = json.loads(json.loads(event.kind)["public"])
    assert private["effective_target"] == 10 and public["outcome"] == "triggered"
    assert "private-insult" not in json.dumps(public) and "-5" not in json.dumps(public)
    assert updated.actors == before.actors
    play.rng = RecordedDice([])
    restored = type(updated).model_validate_json(updated.model_dump_json())
    assert social_occurrence(play, restored, "npc", trigger, "private-insult") == restored
    with pytest.raises(ValidationError, match="approved disadvantage"):
        social_occurrence(
            play,
            before,
            "npc",
            trigger.model_copy(update={"trait_id": "trait:curious"}),
            "unowned-trait",
        )


@pytest.mark.parametrize(
    "identifier,attribute,difficulty,citation,expected",
    [
        ("fast-talk", ControllingAttribute.IQ, Difficulty.AVERAGE, "B195", "good"),
        ("intimidation", ControllingAttribute.WILL, Difficulty.AVERAGE, "B202", "good"),
        ("savoir-faire-high-society", ControllingAttribute.IQ, Difficulty.EASY, "B218", "good"),
        ("sex-appeal", ControllingAttribute.HT, Difficulty.AVERAGE, "B219", "very-good"),
        ("streetwise", ControllingAttribute.IQ, Difficulty.AVERAGE, "B223", "good"),
    ],
)
async def test_live_authored_influence_uses_the_pinned_skill(
    tmp_path: Path,
    identifier: str,
    attribute: ControllingAttribute,
    difficulty: Difficulty,
    citation: str,
    expected: str,
) -> None:
    # An explicitly pinned test package supplies these B195/202/218/219/223 skills.
    # Existing profile package digests are not rewritten to make this test pass.
    from test_mundane_traits import combined_package

    package = combined_package()
    definition = RuleDefinition(
        id="skill:" + identifier,
        kind=DefinitionKind.SKILL,
        name=identifier,
        source_id=package.sources[0].id,
        point_cost=None,
        status=ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(
            attribute,
            difficulty,
            citation,
            specialty=Specialty("savoir-faire", "high-society")
            if identifier == "savoir-faire-high-society"
            else None,
        ),
    )
    cid, play = await prepare_traits(
        tmp_path,
        Purchase(definition_id=definition.id, amount=4),
        extra_definitions=(definition,),
    )
    before = play._load(await play.store.read(cid))
    trigger = NPCSocialTrigger(kind="influence", subject_id="npc", skill_id=definition.id)
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4])
    updated = social_occurrence(play, before, "a", trigger, "audience")
    event = next(e for e in updated.resources.events if e.id.startswith("social:"))
    private = json.loads(event.kind)["private"]
    result = json.loads(json.loads(event.kind)["public"])
    assert result["outcome"] == expected
    assert private["contest"]["first"]["effective_target"] == (
        12 if identifier == "savoir-faire-high-society" else 11
    )
    play.rng = RecordedDice([])
    assert social_occurrence(play, updated, "a", trigger, "audience") == updated


def with_aftermath(state: ResourceState, actor_id: str = "a") -> ResourceState:
    return save(
        state,
        TimedFright(
            id="prior-coma",
            actor_id=actor_id,
            trigger_id="coma",
            started=0,
            due=None,
            active=False,
            recovery_target=10,
            aftermath_until=21600,
            effect=FrightEffect(table_total=28, aftermath_penalty=-2, aftermath_seconds=21600),
        ),
        "recovered-coma",
    )


@pytest.mark.parametrize("actor_id,outcome", [("a", "bad"), ("npc", "good")])
def test_b361_aftermath_affects_both_influence_contestants_privately(
    actor_id: str, outcome: str
) -> None:
    state = with_aftermath(ResourceState(), actor_id)
    value = command().model_copy(update={"kind": "influence"})
    updated, result = apply_social(
        state,
        world(),
        value,
        SocialContext(PROFILE, 12, will=12, skill="fast-talk"),
        rng=RecordedDice([3, 3, 4, 3, 3, 4]),
        system=True,
    )
    assert result.outcome == outcome
    assert "coma" not in result.model_dump_json()
    assert apply_social(
        updated,
        world(),
        value,
        SocialContext(PROFILE, 0),
        rng=RecordedDice([]),
        system=True,
    ) == (updated, result)


def test_aftermath_expiration_reaction_and_self_control_exclusions() -> None:
    state = with_aftermath(ResourceState())
    assert aftermath_penalty(state.model_copy(update={"game_time": 21599}), "a") == -2
    assert aftermath_penalty(state.model_copy(update={"game_time": 21600}), "a") == 0
    assert aftermath_penalty(state, "npc") == 0
    _, reaction = apply_social(
        state,
        world(),
        command(),
        SocialContext(PROFILE, 10),
        rng=RecordedDice([5, 5, 5]),
        system=True,
    )
    assert reaction.outcome == "good"  # Reaction totals are not success checks.
    value = command().model_copy(update={"kind": "self-control", "subject_id": "a"})
    _, control = apply_social(
        state,
        world(),
        value,
        SocialContext(
            PROFILE,
            20,
            trait_options=TraitOptions(self_control=12),
            trait_rules=TraitRules(PROFILE, self_control=True),
        ),
        rng=RecordedDice([3, 4, 4]),
        system=True,
    )
    assert control.outcome == "resisted"  # Rating 12, not Will 20 and not rating 10.


def test_aftermath_modifies_fright_check_without_shortening_physical_duration() -> None:
    state = with_aftermath(ResourceState())
    updated, _ = apply_social(
        state,
        world(),
        command().model_copy(update={"kind": "fright", "subject_id": "a"}),
        SocialContext(PROFILE, 10, ht=10),
        rng=RecordedDice([4, 5, 5, 2, 2, 2]),
        system=True,
    )
    private = json.loads(updated.events[-1].kind)["private"]
    # Effective Will 8 rolls 14: failure by 6, table 6 => row 12.
    assert private["check"]["effective_target"] == 8
    assert private["effect"]["table_total"] == 12
    assert private["effect"]["duration_seconds"] == 15  # 25 - actual HT 10.


async def test_b428_retching_costs_one_fp_only_at_recovery_and_replay(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = apply_effect(
        play._load(await play.store.read(cid)).resources,
        FrightEffect(
            table_total=12,
            condition="retching",
            duration_seconds=15,
            recovery_attribute="ht",
            recovery_interval_seconds=1,
        ),
        actor_id="a",
        trigger_id="retch",
        command_id="retch",
        ht=10,
        will=10,
        modified_will=10,
        rng=RecordedDice([]),
    )
    assert next(p.current for p in state.pools if p.id == "fp:a") == 10
    state, passed = recover(
        state.model_copy(update={"game_time": 15}),
        actor_id="a",
        trigger_id="retch",
        command_id="failed-recovery",
        rng=RecordedDice([6, 6, 6]),
    )
    assert not passed and next(p.current for p in state.pools if p.id == "fp:a") == 10
    value = SocialCommand(
        id="recovery",
        actor_id="a",
        subject_id="a",
        trigger_id="retch",
        kind="fright-recovery",
        expected_revision=state.revision,
    )
    recovered, result = apply_social(
        state.model_copy(update={"game_time": 16}),
        world(),
        value,
        SocialContext(PROFILE, 0),
        rng=RecordedDice([1, 1, 1]),
        system=True,
    )
    assert result.outcome == "recovered" and not effects(recovered)[0].active
    assert recovered.revision == state.revision + 1
    assert next(p.current for p in recovered.pools if p.id == "fp:a") == 9
    restored = ResourceState.model_validate_json(recovered.model_dump_json())
    assert apply_social(
        restored,
        world(),
        value,
        SocialContext(PROFILE, 0),
        rng=RecordedDice([]),
        system=True,
    ) == (recovered, result)
    with pytest.raises(ConflictError):
        recover(
            recovered, actor_id="a", trigger_id="retch", command_id="again", rng=RecordedDice([])
        )
