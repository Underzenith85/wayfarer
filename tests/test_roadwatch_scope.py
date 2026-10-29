"""Crafted payloads cannot escape the installed campaign's static scope."""

from dataclasses import replace
from pathlib import Path

import pytest

from wayfarer.adventures.roadwatch import adventure, engine
from wayfarer.adventures.roadwatch_care import POLICY, admit, environment
from wayfarer.adventures.roadwatch_scope import RoadwatchScope
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.rules.types.recovery import RecoveryTask
from wayfarer.engine.simulation.actions import PlayActor, PlayState
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical import MedicalService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def turn(**changes: object) -> dict[str, object]:
    return {
        "kind": "take_combat_turn",
        "id": "turn-1",
        "actor_id": "a",
        "expected_revision": 7,
        "encounter_id": "clearing-fight",
        "maneuver": "attack",
        "item_id": "sword-a",
        "target_id": "brigand",
        "mode_id": "swing",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"maneuver": "all_out_attack"},
        {"maneuver": "move_and_attack"},
        {"maneuver": "aim"},
        {"attack_option": "rapid-strike"},
        {"shots": 2},
        {"hit_location": "skull"},
        {"armor_chink": True},
        {"target_item_id": "sword-brigand"},
        {"second_item_id": "sword-b"},
        {"shield_rush": True},
        {"electrical_contact_seconds": 1},
        {"item_id": "invented-firearm"},
        {"mode_id": "spell"},
    ],
)
def test_advanced_attack_options_rejected_on_both_native_surfaces(
    changes: dict[str, object],
) -> None:
    scope = RoadwatchScope(adventure())
    requests: list[tuple[str, dict[str, object]]] = [
        ("/campaigns/roadwatch/commands", turn(**changes)),
        ("/api/tactical/v2/campaigns/roadwatch/commands", {"command": turn(**changes)}),
    ]
    for path, body in requests:
        with pytest.raises((ValidationError, ValueError)):
            scope.mutation(path, body, "party")


def test_ordinary_armed_attack_and_actual_gm_continuation_admitted() -> None:
    scope = RoadwatchScope(adventure())
    scope.command(turn())
    scope.command(
        {
            "kind": "continue_critical_miss",
            "id": "resolve-1",
            "actor_id": "gm",
            "expected_revision": 9,
            "encounter_id": "clearing-fight",
            "critical_id": "saved-id",
            "stage": "resume",
        }
    )


@pytest.mark.parametrize(
    "path",
    [
        "/campaigns/roadwatch/drafts",
        "/campaigns/roadwatch/workshop-grants",
        "/campaigns/roadwatch/workshop-advancement/advance",
        "/setups/roadwatch/migration",
        "/api/tactical/v1/campaigns/roadwatch/commands",
        "/api/v1/campaigns/roadwatch/actions",
        "/api/v1/campaigns/roadwatch/actions/pending/cancel",
        "/scenario-catalog",
    ],
)
def test_other_mutation_entrypoints_fail_closed(path: str) -> None:
    with pytest.raises(ValidationError):
        RoadwatchScope(adventure()).mutation(path, {}, "gm")


def test_fixed_graph_cannot_be_replaced_by_edited_build_or_anatomy() -> None:
    graph = adventure()
    raw = graph.model_dump(mode="json")
    raw["actors"][0]["body"] = None
    with pytest.raises(ValidationError):
        RoadwatchScope(graph).mutation(
            "/setups",
            {
                "id": "fresh-watch",
                "brief": graph.brief.model_dump(mode="json"),
                "graph": raw,
            },
            "party",
        )


def test_same_controller_does_not_admit_unpaid_care_or_other_procedures() -> None:
    scope = RoadwatchScope(adventure())
    base = {"kind": "gurps_recovery", "id": "care-1", "actor_id": "a", "expected_revision": 8}
    for choice in ("gurps:first-aid:a:b:wound", "gurps:physician:a:b:wound", "gurps:rest:a:b"):
        with pytest.raises(ValidationError):
            scope.command({**base, "choice_id": choice})


def clinic_state(tmp_path: Path) -> tuple[PlayService, PlayState]:
    """Pure policy prestate, not evidence of a played injury or treatment."""
    graph = adventure()
    play = PlayService(AsyncSQLiteStore(tmp_path / "never-opened.sqlite3"), engine())
    world = replace(
        graph.world,
        entities=tuple(
            replace(entity, location_id="shelter") if entity.id in {"a", "b"} else entity
            for entity in graph.world.entities
        ),
    )
    return play, PlayState(
        campaign_id="policy-unit",
        configuration_digest=play.engine.digest,
        world=world,
        resources=graph.resources,
        actors=tuple(PlayActor.model_validate(actor.model_dump()) for actor in graph.actors),
    )


def treatment(wound_id: str = "injury:wound-1") -> BeginRecovery:
    return BeginRecovery(
        id="care-1",
        actor_id="b",
        expected_revision=0,
        kind="first-aid",
        target_id="a",
        wound_id=wound_id,
    )


def test_fp_rest_does_not_require_bandages_but_first_aid_does(tmp_path: Path) -> None:
    play, state = clinic_state(tmp_path)
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        item for item in state.resources.items if item.id != "bandages-post"
                    ),
                }
            )
        }
    )
    assert environment(play, state, "a").technology_level == 3
    admit(
        play,
        state,
        BeginRecovery(id="rest-1", actor_id="a", expected_revision=0, kind="rest", target_id="a"),
    )
    with pytest.raises(ValidationError, match="bandages"):
        admit(play, state, treatment())


@pytest.mark.parametrize(
    "change", ["damaged", "improvised", "kit-excluded", "wrong-custody", "grounded"]
)
def test_inadequate_actual_bandages_refused(tmp_path: Path, change: str) -> None:
    play, state = clinic_state(tmp_path)
    bandages = next(item for item in state.resources.items if item.id == "bandages-post")
    variations: dict[str, dict[str, object]] = {
        "damaged": {"condition": ObjectCondition(hp=0, disabled=True)},
        "improvised": {"definition_id": "equipment:blanket"},
        "kit-excluded": {"definition_id": "equipment:first-aid-kit"},
        "wrong-custody": {"owner_id": "brigand"},
        "grounded": {"world_ground_location_id": "post"},
    }
    changed = bandages.model_copy(update=variations[change])
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        changed if item.id == bandages.id else item
                        for item in state.resources.items
                    ),
                }
            )
        }
    )
    before = state.model_dump_json()
    with pytest.raises(ValidationError):
        admit(play, state, treatment())
    assert state.model_dump_json() == before


def test_capacity_reserves_six_distinct_wounds_not_two_procedures_per_wound(tmp_path: Path) -> None:
    play, state = clinic_state(tmp_path)
    tasks = tuple(
        RecoveryTask(
            id=f"task-{number}",
            actor_id="b",
            target_id="a",
            profile_id="gurps-basic-set-4e-2004",
            kind="bandage",
            start=0,
            due=60,
            status="completed",
            settled=True,
            wound_id=f"injury:wound-{number}",
        )
        for number in range(1, 7)
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"recovery_tasks": tasks})}
    )
    # Following bandaging with First Aid for wound6 reuses its reserved provision.
    admit(play, state, treatment("injury:wound-6"))
    with pytest.raises(ValidationError, match="six-wound"):
        admit(play, state, treatment("injury:wound-7"))
    assert len(state.resources.recovery_tasks) == 6


def test_policy_plan_requires_prestate_instead_of_skipping_preentropy_assessment(
    tmp_path: Path,
) -> None:
    play, _state = clinic_state(tmp_path)
    with pytest.raises(ValidationError, match="admission prestate"):
        MedicalService(play, POLICY).plan(
            play, "gurps-basic-set-4e-2004", treatment(), principal_id="b"
        )


def test_actual_json_setup_and_ordinary_hex_path_are_admitted() -> None:
    graph = adventure()
    scope = RoadwatchScope(graph)
    scope.mutation(
        "/setups",
        {
            "id": "fresh-watch",
            "brief": graph.brief.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
        },
        "party",
    )
    scope.mutation(
        "/api/tactical/v2/campaigns/fresh-watch/commands",
        {
            "command": turn(
                maneuver="move",
                item_id=None,
                target_id=None,
                mode_id=None,
                hex_path=[{"q": 1, "r": 0}],
                hex_facing=0,
            ),
        },
        "party",
    )


def test_start_requires_actual_two_sided_watch_alliance() -> None:
    scope = RoadwatchScope(adventure())
    start: dict[str, object] = {
        "kind": "start_encounter",
        "id": "fight-start",
        "actor_id": "gm",
        "expected_revision": 4,
        "encounter_id": "road-fight",
        "battlefield_id": "roadwatch-clearing",
        "scene_id": "road-scene",
        "placements": [
            {"actor_id": "a", "position": {"q": 1, "r": 0}, "hex_facing": 0},
            {"actor_id": "b", "position": {"q": 0, "r": 1}, "hex_facing": 0},
            {"actor_id": "brigand", "position": {"q": 3, "r": 0}, "hex_facing": 3},
        ],
        "allegiances": [
            {"actor_id": "a", "side_id": "watch"},
            {"actor_id": "b", "side_id": "watch"},
            {"actor_id": "brigand", "side_id": "brigand"},
        ],
        "oppositions": [{"side_ids": ["brigand", "watch"]}],
    }
    scope.command(start)
    with pytest.raises(ValidationError):
        scope.command({**start, "allegiances": [], "oppositions": []})


def test_clinic_requires_actual_completed_bandage_before_first_aid(tmp_path: Path) -> None:
    play, state = clinic_state(tmp_path)
    with pytest.raises(ValidationError, match="Complete bandaging"):
        admit(play, state, treatment())
    # A pending procedure is not a completed bandage and cannot grant the stage.
    task = RecoveryTask(
        id="bandage-pending",
        actor_id="b",
        target_id="a",
        profile_id="gurps-basic-set-4e-2004",
        kind="bandage",
        start=0,
        due=60,
        wound_id="injury:wound-1",
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"recovery_tasks": (task,)})}
    )
    with pytest.raises(ValidationError, match="Complete bandaging"):
        admit(play, state, treatment())


def test_parry_requires_explicit_actual_broadsword_mode_before_any_roll() -> None:
    scope = RoadwatchScope(adventure())
    defense: dict[str, object] = {
        "kind": "choose_defense",
        "id": "defend-1",
        "actor_id": "a",
        "expected_revision": 4,
        "encounter_id": "road-fight",
        "defense": "parry",
        "item_id": "sword-a",
    }
    with pytest.raises(ValidationError):
        scope.command(defense)
    scope.command({**defense, "parry_mode_id": "swing"})
    scope.command({**defense, "parry_mode_id": "thrust"})
    scope.command({**defense, "defense": "dodge", "item_id": None})
    scope.command({**defense, "defense": "none", "item_id": None})
