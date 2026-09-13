"""Independent artifact expectations derived from Campaigns B478-B480."""

import pytest
from test_actions import engine, seed

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, PlayState
from wayfarer.engine.simulation.campaign.world_context import (
    Realm,
    TechnologyField,
    WorldContextRules,
)
from wayfarer.engine.simulation.equipment.artifacts import (
    AnalyzeArtifact,
    ArtifactAnalysis,
    ArtifactCapability,
    ArtifactDefinition,
    ArtifactKnowledge,
    ArtifactOutcome,
    ArtifactProcedure,
    ArtifactProperty,
    ArtifactRules,
    ArtifactState,
    ArtifactStateEffect,
    OperateArtifact,
    artifact_procedure_context,
    visible_artifact,
)
from wayfarer.errors import ValidationError


def worlds() -> WorldContextRules:
    return WorldContextRules(
        id="world-context",
        version=1,
        realms=(
            Realm(
                id="home",
                kind="physical",
                baseline_technology_level=5,
                technology_fields=(TechnologyField(field="physics", level=5),),
                location_ids=("dock", "alley", "far"),
            ),
        ),
    )


def procedure(*, native: int = 8) -> ArtifactProcedure:
    return ArtifactProcedure(
        skill_id="skill:observation",
        technology_field="physics",
        native_technology_level=native,
    )


def artifact_rules(
    *,
    executable: tuple[str, ...] = ("beam", "glitch", "shift"),
    families: tuple[str, ...] = ("artifact-state",),
) -> ArtifactRules:
    return ArtifactRules.model_validate(
        {
            "id": "artifacts",
            "version": 1,
            "artifacts": (
                ArtifactDefinition(
                    id="alien-lens",
                    item_definition_id="tool",
                    apparent_function="A seamless lens with an unmarked control surface.",
                    actual_capability="Projects a field through a registered campaign adapter.",
                    origin="Authored off-world salvage.",
                    property_definitions=(
                        ArtifactProperty(
                            id="shell",
                            label="Outer shell",
                            description="The visible housing and controls.",
                        ),
                        ArtifactProperty(
                            id="beam-property",
                            label="Field projector",
                            description="The identified primary function.",
                            capability_ids=("beam",),
                        ),
                        ArtifactProperty(
                            id="resonance",
                            label="Resonance",
                            description="A still-hidden secondary property.",
                            capability_ids=("glitch", "shift"),
                        ),
                    ),
                    public_property_ids=("shell",),
                    capabilities=(
                        ArtifactCapability(
                            id="beam",
                            property_id="beam-property",
                            operation=procedure(),
                            effect_family="artifact-state",
                            effect=ArtifactStateEffect(
                                id="field", state="condition", target="operator"
                            ),
                            side_effect_ids=("glitch", "shift"),
                        ),
                        ArtifactCapability(
                            id="glitch",
                            property_id="resonance",
                            operation=procedure(),
                            effect_family="artifact-state",
                            effect=ArtifactStateEffect(
                                id="glitch", state="malfunction", target="artifact"
                            ),
                        ),
                        ArtifactCapability(
                            id="shift",
                            property_id="resonance",
                            operation=procedure(),
                            effect_family="artifact-state",
                            effect=ArtifactStateEffect(
                                id="shift",
                                state="transformation",
                                target="operator",
                                duration_seconds=10,
                            ),
                        ),
                    ),
                    repair=ArtifactProcedure(
                        skill_id="skill:observation",
                        technology_field="physics",
                        native_technology_level=8,
                        modifier=-2,
                    ),
                ),
            ),
            "analyses": (
                ArtifactAnalysis(
                    id="analyze-beam",
                    artifact_id="alien-lens",
                    property_id="beam-property",
                    procedure=procedure(),
                ),
            ),
            "executable_capability_ids": executable,
            "executable_effect_families": families,
        }
    )


def configured(rules: ArtifactRules | None = None) -> ActionEngine:
    base = engine()
    return ActionEngine(
        base.reviewer,
        base.resources,
        ActionRules(
            id="actions",
            version=1,
            checks=base.rules.checks,
            consumables=base.rules.consumables,
            world_context=worlds(),
            artifacts=rules or artifact_rules(),
        ),
    )


def known_state(reducer: ActionEngine) -> PlayState:
    state = seed(reducer)
    return state.model_copy(
        update={
            "configuration_digest": reducer.digest,
            "artifacts": ArtifactState(
                knowledge=(
                    ArtifactKnowledge(
                        actor_id="a",
                        artifact_id="alien-lens",
                        property_ids=("beam-property",),
                    ),
                )
            ),
        }
    )


def test_unknown_and_partial_artifacts_expose_only_authorized_properties() -> None:
    rules = artifact_rules()
    unknown = visible_artifact(rules, ArtifactState(), "alien-lens", "a")
    assert tuple(item.id for item in unknown.known_properties) == ("shell",)
    assert unknown.capability_ids == ()

    partial = visible_artifact(
        rules,
        ArtifactState(
            knowledge=(
                ArtifactKnowledge(
                    actor_id="a",
                    artifact_id="alien-lens",
                    property_ids=("beam-property",),
                ),
            )
        ),
        "alien-lens",
        "a",
    )
    assert tuple(item.id for item in partial.known_properties) == ("shell", "beam-property")
    assert partial.capability_ids == ("beam",)
    assert "resonance" not in partial.model_dump_json()


def test_analysis_discovers_one_property_and_records_the_minute() -> None:
    reducer = configured()
    state = seed(reducer).model_copy(update={"configuration_digest": reducer.digest})
    updated, outcome = reducer.campaign.apply(
        state,
        AnalyzeArtifact(
            id="inspect-lens",
            actor_id="a",
            expected_revision=0,
            item_id="tool",
            analysis_id="analyze-beam",
        ),
        rng=RecordedDice((1, 1, 1)),
    )
    assert isinstance(outcome, ArtifactOutcome)
    assert outcome.status == "identified"
    assert outcome.property_ids == ("shell", "beam-property")
    assert updated.resources.game_time == 60
    assert updated.artifacts.knowledge[0].property_ids == ("beam-property",)
    assert "resonance" not in outcome.model_dump_json()


def test_anachronistic_tl_context_changes_procedures_without_catalog_mutation() -> None:
    reducer = configured()
    state = seed(reducer)
    artifact = artifact_rules().artifacts[0]
    operation = artifact_procedure_context(
        artifact.capabilities[0].operation,
        worlds(),
        state.world_context,
        state.world,
        "a",
    )
    assert (operation.native_technology_level, operation.local_technology_level) == (8, 5)
    assert operation.modifier == -3
    assert artifact.repair is not None
    repair = artifact_procedure_context(
        artifact.repair, worlds(), state.world_context, state.world, "a"
    )
    assert repair.modifier == -5
    assert reducer.resources.specs["tool"].technology_level == 0


def test_random_side_effect_is_recorded_once_and_replays_without_entropy() -> None:
    reducer = configured()
    state = known_state(reducer)
    command = OperateArtifact(
        id="use-lens",
        actor_id="a",
        expected_revision=0,
        item_id="tool",
        capability_id="beam",
    )
    updated, outcome = reducer.campaign.apply(state, command, rng=RecordedDice((1, 1, 1, 2)))
    assert isinstance(outcome, ArtifactOutcome)
    assert outcome.status == "activated" and outcome.side_effect_id == "shift"
    assert updated.artifacts.occurrences[0].side_effect_id == "shift"
    assert len(updated.resources.active_effect_ids) == len(state.resources.active_effect_ids) + 2
    assert updated.resources.scheduled[-1].due == 10

    replayed, replay = reducer.campaign.apply(updated, command, rng=RecordedDice(()))
    assert replay == outcome
    assert replayed == updated
    assert len(replayed.artifacts.occurrences) == 1


def test_missing_effect_family_rejects_before_check_side_effect_or_depletion() -> None:
    reducer = configured(artifact_rules(families=()))
    state = known_state(reducer)
    before = state.model_dump_json()
    with pytest.raises(ValidationError, match="no registered adapter"):
        reducer.campaign.apply(
            state,
            OperateArtifact(
                id="unsupported",
                actor_id="a",
                expected_revision=0,
                item_id="tool",
                capability_id="beam",
            ),
            rng=RecordedDice(()),
        )
    assert state.model_dump_json() == before


def test_unregistered_capability_stays_unusable_while_item_is_present() -> None:
    reducer = configured(artifact_rules(executable=("glitch", "shift")))
    state = known_state(reducer)
    assert any(item.id == "tool" for item in state.resources.items)
    with pytest.raises(ValidationError, match="unavailable"):
        reducer.campaign.apply(
            state,
            OperateArtifact(
                id="no-capability",
                actor_id="a",
                expected_revision=0,
                item_id="tool",
                capability_id="beam",
            ),
            rng=RecordedDice(()),
        )


def test_rule_validation_rejects_unknown_side_effects() -> None:
    rules = artifact_rules()
    artifact = rules.artifacts[0]
    primary = artifact.capabilities[0].model_copy(update={"side_effect_ids": ("narrated-power",)})
    with pytest.raises(ValueError, match="Unknown artifact side-effect"):
        ArtifactDefinition(
            **{
                **artifact.model_dump(),
                "capabilities": (primary, *artifact.capabilities[1:]),
            }
        )
