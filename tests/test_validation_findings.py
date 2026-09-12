"""Every error finding localises the fault and says what would satisfy it (#365)."""

from pathlib import Path

import pytest
from test_actions import actor_setup
from test_authoring_boundaries import studio_at
from test_wave11 import graph_fixture

from wayfarer.engine.simulation.actions import CheckRule
from wayfarer.engine.simulation.campaign.objectives import Objective, ObjectiveRules, Predicate
from wayfarer.engine.simulation.campaign.scenes import Obstacle, Scene, SceneExit
from wayfarer.engine.simulation.campaign.studio import ApproachSupport, ScenarioGraph, StudioFinding
from wayfarer.engine.simulation.resources import Owner
from wayfarer.orchestration.studio import ScenarioStudio


def errors(studio: ScenarioStudio, graph: ScenarioGraph, code: str) -> list[StudioFinding]:
    return [f for f in studio.validate(graph).findings if f.code == code]


def only(studio: ScenarioStudio, graph: ScenarioGraph, code: str) -> StudioFinding:
    found = errors(studio, graph, code)
    assert len(found) == 1, found
    return found[0]


def without_backup(graph: ScenarioGraph) -> ScenarioGraph:
    """Drop the automatic fallback, leaving the authored check as the only revelation."""
    return graph.model_copy(
        update={
            "scenes": graph.scenes.model_copy(
                update={
                    "discoveries": tuple(d for d in graph.scenes.discoveries if d.id != "backup")
                }
            )
        }
    )


def test_unattainable_clue_names_the_discovery_and_the_unsupported_check(tmp_path: Path) -> None:
    studio = studio_at(tmp_path)
    graph = without_backup(graph_fixture())
    # The only revelation is a check the party cannot run: its tool is not carried.
    graph = graph.model_copy(
        update={
            "actions": graph.actions.model_copy(
                update={
                    "checks": tuple(
                        c.model_copy(update={"required_equipment": "tool"})
                        if c.id == "inspect"
                        else c
                        for c in graph.actions.checks
                    )
                }
            ),
            "resources": graph.resources.model_copy(
                update={"items": tuple(i for i in graph.resources.items if i.id != "tool")}
            ),
        }
    )
    finding = only(studio, graph, "clue.missing")
    assert finding.reference == "clue"
    # The author learns the discovery, the check, the skill holder and the remedy.
    assert "find-letter" in finding.message and "check inspect is unsupported" in finding.message
    assert "a has skill:observation" in finding.message
    assert "no player character carries the required tool" in finding.message
    assert "resources.items" in finding.message


def test_clue_with_no_revelation_at_all_says_which_authoring_moves_would_fix_it(
    tmp_path: Path,
) -> None:
    studio = studio_at(tmp_path)
    graph = graph_fixture()
    graph = graph.model_copy(
        update={
            "scenes": graph.scenes.model_copy(
                update={
                    "discoveries": tuple(d for d in graph.scenes.discoveries if d.fact_id != "clue")
                }
            )
        }
    )
    finding = only(studio, graph, "clue.missing")
    assert finding.reference == "clue"
    assert "no discovery, entry trigger, encounter approach or recovery option reveals it" in (
        finding.message
    )
    assert "starting knowledge" in finding.message


def test_unattainable_clue_reports_an_unreachable_discovery_scene(tmp_path: Path) -> None:
    studio = studio_at(tmp_path)
    graph = blocked_alley(without_backup(graph_fixture()))
    # Move the sole revelation behind the sealed exit.
    graph = graph.model_copy(
        update={
            "scenes": graph.scenes.model_copy(
                update={
                    "discoveries": tuple(
                        d.model_copy(update={"scene_id": "alley-scene"})
                        if d.id == "find-letter"
                        else d
                        for d in graph.scenes.discoveries
                    )
                }
            )
        }
    )
    finding = only(studio, graph, "clue.missing")
    assert "discovery find-letter sits in unreachable scene alley-scene" in finding.message


def blocked_alley(graph: ScenarioGraph) -> ScenarioGraph:
    """Seal every route to the alley: one exit, behind an obstacle with no bypass."""
    return graph.model_copy(
        update={
            "scenes": graph.scenes.model_copy(
                update={
                    "scenes": (
                        Scene(
                            id="dock-scene",
                            version=1,
                            location_id="dock",
                            title="The Dock",
                            exits=(SceneExit(id="to-alley", destination_id="alley-scene"),),
                            obstacles=(
                                Obstacle(
                                    id="sealed-gate",
                                    exit_id="to-alley",
                                    description="A sealed gate",
                                ),
                            ),
                        ),
                        Scene(id="alley-scene", version=1, location_id="alley", title="The Alley"),
                    )
                }
            )
        }
    )


def test_unreachable_scene_names_the_exit_and_what_blocks_it(tmp_path: Path) -> None:
    studio = studio_at(tmp_path)
    finding = only(studio, blocked_alley(graph_fixture()), "graph.unreachable")
    assert finding.reference == "alley-scene"
    assert "no supported route from opening scene dock-scene" in finding.message
    assert "exit to-alley from dock-scene" in finding.message
    assert "obstacles sealed-gate without an attainable bypass fact" in finding.message


def test_unreachable_scene_names_a_missing_required_fact(tmp_path: Path) -> None:
    studio = studio_at(tmp_path)
    graph = graph_fixture()
    graph = graph.model_copy(
        update={
            "scenes": graph.scenes.model_copy(
                update={
                    "scenes": tuple(
                        Scene(
                            id="dock-scene",
                            version=1,
                            location_id="dock",
                            title="The Dock",
                            exits=(
                                SceneExit(
                                    id="to-alley",
                                    destination_id="alley-scene",
                                    required_fact_ids=("promise",),
                                ),
                            ),
                        )
                        if s.id == "dock-scene"
                        else s.model_copy(update={"obstacles": (), "exits": ()})
                        for s in graph.scenes.scenes
                    )
                }
            )
        }
    )
    finding = only(studio, graph, "graph.unreachable")
    assert "exit to-alley from dock-scene needs unattainable facts promise" in finding.message


def test_advertised_approach_reports_the_actor_and_the_location_mismatch(tmp_path: Path) -> None:
    studio = studio_at(tmp_path)
    graph = graph_fixture()
    graph = graph.model_copy(
        update={
            "actors": graph.actors + (actor_setup().model_copy(update={"actor_id": "b"}),),
            "resources": graph.resources.model_copy(
                update={"owners": graph.resources.owners + (Owner(actor_id="b", capacity=100),)}
            ),
            "actions": graph.actions.model_copy(
                update={
                    "checks": tuple(
                        c.model_copy(update={"required_equipment": "potion"})
                        if c.id == "inspect"
                        else c
                        for c in graph.actions.checks
                    )
                }
            ),
        }
    )
    approach = ApproachSupport(
        id="search", scene_id="dock-scene", check_rule_id="inspect", actor_id="a"
    )
    wrong_actor = only(
        studio,
        graph.model_copy(update={"approaches": (approach.model_copy(update={"actor_id": "b"}),)}),
        "approach.unsupported",
    )
    assert wrong_actor.reference == "search"
    assert "b is not one of the player characters that can run check inspect (a)" in (
        wrong_actor.message
    )
    wrong_scene = only(
        studio,
        graph.model_copy(
            update={"approaches": (approach.model_copy(update={"scene_id": "alley-scene"}),)}
        ),
        "approach.unsupported",
    )
    assert "target chest is at dock, but scene alley-scene is at alley" in wrong_scene.message


def test_incompatible_objectives_name_both_objectives_and_the_clashing_values(
    tmp_path: Path,
) -> None:
    studio = studio_at(tmp_path)
    graph = graph_fixture()
    graph = graph.model_copy(
        update={
            "objectives": ObjectiveRules(
                id="endings",
                version=1,
                deadline=100,
                objectives=(
                    Objective(
                        id="one",
                        title="First",
                        predicates=(Predicate(kind="location", subject_id="a", value="dock"),),
                    ),
                    Objective(
                        id="two",
                        title="Second",
                        predicates=(Predicate(kind="location", subject_id="a", value="alley"),),
                    ),
                ),
            )
        }
    )
    finding = only(studio, graph, "ending.contradiction")
    assert finding.reference == "endings"
    assert "Required objectives one and two in endings" in finding.message
    assert "location a must be dock and alley" in finding.message
    assert "mark one objective optional" in finding.message


def test_ambiguous_check_rules_report_the_check_not_the_scenario(tmp_path: Path) -> None:
    studio = studio_at(tmp_path)
    graph = graph_fixture()
    duplicate = next(c for c in graph.actions.checks if c.id == "inspect").model_copy(
        update={"id": "inspect-again"}
    )
    graph = graph.model_copy(
        update={
            "actions": graph.actions.model_copy(
                update={"checks": graph.actions.checks + (duplicate,)}
            )
        }
    )
    finding = only(studio, graph, "runtime.invalid")
    assert finding.reference == "inspect-again" and finding.reference != graph.id
    assert "one check per action and target" in finding.message


def test_escrow_and_actor_defects_name_the_item_and_the_actor(tmp_path: Path) -> None:
    studio = studio_at(tmp_path)
    graph = graph_fixture()
    graph = graph.model_copy(update={"npc_actor_ids": ("ghost",)})
    finding = only(studio, graph, "actors.invalid")
    assert finding.reference == "ghost"
    assert "npc_actor_ids names ghost, which is not a declared actor" in finding.message


def test_unsupported_check_definition_is_localised_by_the_engine(tmp_path: Path) -> None:
    studio = studio_at(tmp_path)
    graph = graph_fixture()
    broken = next(c for c in graph.actions.checks if c.id == "inspect").model_copy(
        update={"id": "read-ledger", "definition_id": "attribute:dx"}
    )
    graph = graph.model_copy(
        update={
            "actions": graph.actions.model_copy(
                update={
                    "checks": tuple(c for c in graph.actions.checks if c.id != "inspect")
                    + (broken,)
                }
            )
        }
    )
    finding = only(studio, graph, "runtime.invalid")
    assert finding.reference == "read-ledger"
    assert "attribute:dx is attribute, not a skill" in finding.message


BROKEN_CASES = (
    "unreachable",
    "clue",
    "approach",
    "contradiction",
    "npc",
    "runtime",
    "opening",
)


@pytest.mark.parametrize("case", BROKEN_CASES)
def test_every_error_finding_names_the_node_it_reports(tmp_path: Path, case: str) -> None:
    """The acceptance bar for #365: a reference that is repeated in an actionable message."""
    graph = graph_fixture()
    if case == "unreachable":
        graph = blocked_alley(graph)
    elif case == "clue":
        graph = graph.model_copy(
            update={
                "scenes": graph.scenes.model_copy(
                    update={
                        "discoveries": tuple(
                            d for d in graph.scenes.discoveries if d.fact_id != "clue"
                        )
                    }
                )
            }
        )
    elif case == "approach":
        graph = graph.model_copy(
            update={
                "approaches": (
                    ApproachSupport(
                        id="search", scene_id="alley-scene", check_rule_id="inspect", actor_id="a"
                    ),
                )
            }
        )
    elif case == "contradiction":
        graph = graph.model_copy(
            update={
                "objectives": graph.objectives.model_copy(
                    update={
                        "objectives": (
                            Objective(
                                id="here",
                                title="Both",
                                predicates=(
                                    Predicate(kind="location", subject_id="a", value="dock"),
                                    Predicate(kind="location", subject_id="a", value="far"),
                                ),
                            ),
                        )
                    }
                )
            }
        )
    elif case == "npc":
        graph = graph.model_copy(update={"npc_actor_ids": ("ghost",)})
    elif case == "runtime":
        graph = graph.model_copy(
            update={
                "actions": graph.actions.model_copy(
                    update={
                        "checks": graph.actions.checks
                        + (
                            CheckRule(
                                id="second-look",
                                action="inspect",
                                target_id="chest",
                                definition_id="skill:observation",
                                package_id=graph.actions.checks[0].package_id,
                                package_version=graph.actions.checks[0].package_version,
                            ),
                        )
                    }
                )
            }
        )
    else:
        graph = graph.model_copy(update={"opening_scene_id": "no-such-scene"})
    report = studio_at(tmp_path).validate(graph)
    reported = [f for f in report.findings if f.severity == "error"]
    assert reported, "the case must actually break validation"
    for finding in reported:
        assert finding.reference, finding
        assert finding.reference in finding.message, finding
        assert len(finding.message) > len(finding.code) + 40, finding
