"""Private combat metadata authenticates replay without opening player commands."""

import json

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
from wayfarer.engine.simulation.combat.generations import (
    combat_generation,
    maneuver_budget_enabled,
    missile_interposition_enabled,
    preserve_grenade_fuse,
    secondary_object_blasts_enabled,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.generations import ACTIVE, KEY, features
from wayfarer.persistence.command_inputs import stamp
from wayfarer.persistence.events import CommandInput, payload_digest


def recorded(payload: dict[str, object]) -> CommandInput:
    text = stamp(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return CommandInput(payload_digest({"input": text}), text)


def test_absent_combat_features_remain_legacy() -> None:
    assert (
        features(recorded({"operation": "combat", "command": {"kind": "take_combat_turn"}}))
        == frozenset()
    )


def test_recorded_features_are_independent_and_digest_checked() -> None:
    one = recorded({"operation": "combat", KEY: ["grenade-fuse"]})
    assert features(one) == frozenset({"grenade-fuse"})
    both = recorded({"operation": "combat", KEY: ["grenade-fuse", "maneuver-budget"]})
    assert features(both) == frozenset({"grenade-fuse", "maneuver-budget"})
    with pytest.raises(ValidationError, match="digest"):
        features(CommandInput("0" * 64, both.text))


@pytest.mark.parametrize(
    "bad", [[True], ["future"], ["grenade-fuse", "grenade-fuse"], "grenade-fuse"]
)
def test_invalid_private_generations_are_rejected(bad: object) -> None:
    with pytest.raises(ValidationError, match="generation"):
        features(recorded({"operation": "combat", KEY: bad}))


def test_nested_scopes_restore_legacy_even_on_exception() -> None:
    assert not preserve_grenade_fuse() and not maneuver_budget_enabled()
    with combat_generation(frozenset({"grenade-fuse"})):
        assert preserve_grenade_fuse() and not maneuver_budget_enabled()
        with pytest.raises(RuntimeError), combat_generation(frozenset({"maneuver-budget"})):
            assert not preserve_grenade_fuse() and maneuver_budget_enabled()
            raise RuntimeError("abort")
        assert preserve_grenade_fuse() and not maneuver_budget_enabled()
    assert not preserve_grenade_fuse() and not maneuver_budget_enabled()


def test_player_cannot_inject_private_features_into_public_turn() -> None:
    with pytest.raises(SchemaError):
        TakeCombatTurn.model_validate(
            {
                "id": "turn",
                "actor_id": "a",
                "expected_revision": 0,
                "encounter_id": "e",
                "maneuver": "move",
                KEY: ["maneuver-budget"],
            }
        )


def test_fresh_commands_capture_only_implemented_features() -> None:
    assert ACTIVE == frozenset(
        {
            "grenade-fuse",
            "maneuver-budget",
            "acrobatic-trait-bonuses",
            "acrobatic-reaction-attributes",
            "ground-dive-step",
            "secondary-object-blasts",
            "missile-interposition",
            "paralyze-buckler-drop",
            "rooted-dodge-health-trait-composition",
            "rooted-dodge-haste-composition",
            "hand-melee-spell-contact",
        }
    )


def test_recorded_secondary_blast_generation_retains_isolated_replay_scope() -> None:
    selected = features(recorded({"operation": "combat", KEY: ["secondary-object-blasts"]}))
    assert not secondary_object_blasts_enabled()
    with combat_generation(selected):
        assert secondary_object_blasts_enabled() and not preserve_grenade_fuse()
        with combat_generation(frozenset()):
            assert not secondary_object_blasts_enabled()
        assert secondary_object_blasts_enabled()
    assert not secondary_object_blasts_enabled()


def test_recorded_missile_interposition_does_not_enable_other_features() -> None:
    selected = features(recorded({"operation": "combat", KEY: ["missile-interposition"]}))
    assert not missile_interposition_enabled()
    with combat_generation(selected):
        assert missile_interposition_enabled() and not secondary_object_blasts_enabled()
        with combat_generation(frozenset()):
            assert not missile_interposition_enabled()
        assert missile_interposition_enabled()
    assert not missile_interposition_enabled()
