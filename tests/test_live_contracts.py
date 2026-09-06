"""Executable live-contract examples and negative schema/recovery regressions."""

from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import ValidationError

from scripts.validate_contracts import ROOT, mapping, read, sequence
from scripts.validate_live_contracts import Consumer, live_schema, message_validator, validate_live


def message(name: str) -> dict[str, object]:
    return deepcopy(
        mapping(
            next(
                mapping(item)["message"]
                for item in sequence(read(ROOT / "events.examples.json"))
                if mapping(item)["name"] == name
            )
        )
    )


def consumer(empty: bool = False) -> Consumer:
    name = "partial_snapshot_never_publishes" if empty else "duplicate_event_is_idempotent"
    initial = next(
        mapping(item)["initial"]
        for item in sequence(read(ROOT / "events.scenarios.json"))
        if mapping(item)["name"] == name
    )
    return Consumer.from_fixture(deepcopy(mapping(initial)))


def test_all_live_messages_and_recovery_traces() -> None:
    examples, scenarios = validate_live()
    assert examples >= 28
    assert scenarios >= 12


@pytest.mark.parametrize("field", ["principal_id", "role", "global_revision", "effects"])
def test_subscription_cannot_claim_identity_or_authority(field: str) -> None:
    value = message("Subscribe")
    value[field] = "forged"
    with pytest.raises(ValidationError):
        message_validator(live_schema(), "client").validate(value)


@pytest.mark.parametrize("field", ["cursor", "previous_cursor", "scope", "visibility_epoch"])
def test_durable_envelope_requires_recovery_and_scope_metadata(field: str) -> None:
    value = message("ActionUpdated")
    del value[field]
    with pytest.raises(ValidationError):
        message_validator(live_schema(), "server").validate(value)


def test_no_untyped_game_commands_or_binary_payloads_on_socket() -> None:
    value = message("Subscribe")
    value["type"] = "command"
    with pytest.raises(ValidationError):
        message_validator(live_schema(), "client").validate(value)
    value = message("VoiceSegment")
    value["audio_url"] = "https://example.invalid/private-audio"
    with pytest.raises(ValidationError):
        message_validator(live_schema(), "server").validate(value)


def test_narration_cannot_smuggle_resource_updates() -> None:
    value = message("NarrationDelta")
    value["resources"] = [{"hp": 100}]
    with pytest.raises(ValidationError):
        message_validator(live_schema(), "server").validate(value)


def test_unknown_protocol_and_numeric_global_cursor_rejected() -> None:
    value = message("ActionUpdated")
    value["protocol_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        message_validator(live_schema(), "server").validate(value)
    value = message("ActionUpdated")
    value["cursor"] = 123
    with pytest.raises(ValidationError):
        message_validator(live_schema(), "server").validate(value)


def test_snapshot_end_cannot_publish_missing_resources() -> None:
    view = consumer(empty=True)
    view.accept(message("SnapshotBegin"))
    view.accept(message("SnapshotCampaign"))
    with pytest.raises(ValueError, match="Incomplete"):
        view.accept(message("SnapshotEnd"))
    assert not view.cache_valid
    assert not view.resources
    assert view.cursor is None


def test_mixed_snapshot_identity_and_duplicate_resource_rejected() -> None:
    view = consumer(empty=True)
    view.accept(message("SnapshotBegin"))
    value = message("SnapshotCampaign")
    value["snapshot_id"] = "different"
    with pytest.raises(ValueError, match="identity"):
        view.accept(value)
    view.accept(message("SnapshotCampaign"))
    value = message("SnapshotCampaign")
    value["index"] = 1
    with pytest.raises(ValueError, match="Duplicate"):
        view.accept(value)


def test_snapshot_cannot_insert_another_actors_inventory() -> None:
    view = consumer(empty=True)
    view.accept(message("SnapshotBegin"))
    value = message("SnapshotInventory")
    value["index"] = 0
    mapping(mapping(value["resource"])["value"])["actor_id"] = "other-actor"
    with pytest.raises(ValueError, match="another scope"):
        view.accept(value)


def test_ready_before_snapshot_end_or_wrong_cursor_is_invalid() -> None:
    view = consumer(empty=True)
    view.accept(message("SnapshotBegin"))
    with pytest.raises(ValueError, match="checkpoint"):
        view.accept(message("Ready"))
    view = consumer()
    value = message("Ready")
    value["cursor"] = "unapplied"
    with pytest.raises(ValueError, match="checkpoint"):
        view.accept(value)


def test_changed_duplicate_forces_resync_without_overwriting_committed_state() -> None:
    view = consumer()
    view.accept(message("ActionUpdated"))
    value = message("ActionUpdated")
    mapping(mapping(value["action"])["resolution"])["summary"] = "Changed outcome"
    assert view.accept(value) == "resync"
    assert view.resources["action:action-1"] == message("ActionUpdated")["action"]


def test_revocation_purges_state_audio_and_checkpoint() -> None:
    view = consumer()
    view.accept(message("ActionUpdated"))
    view.accept(message("NarrationStarted"))
    view.accept(message("NarrationDelta"))
    assert view.accept(message("Revoked")) == "purged"
    assert not view.resources and not view.narrations and view.cursor is None
    assert view.accept(message("VoiceSegment")) == "ignored"


def test_committed_narration_requires_known_succeeded_action() -> None:
    view = consumer()
    with pytest.raises(ValueError, match="without a committed action"):
        view.accept(message("NarrationStarted"))


def test_narration_gap_stops_only_narration() -> None:
    view = consumer()
    view.accept(message("ActionUpdated"))
    view.accept(message("NarrationStarted"))
    value = message("NarrationDelta")
    value["index"] = 2
    assert view.accept(value) == "narration_stopped"
    assert view.active and view.cache_valid
    assert view.resources["action:action-1"]["status"] == "succeeded"
    assert view.cursor == message("ActionUpdated")["cursor"]


def test_voice_session_must_match_narration() -> None:
    view = consumer()
    view.accept(message("ActionUpdated"))
    view.accept(message("NarrationStarted"))
    value = message("VoiceSegment")
    value["voice_session_id"] = "other-voice-session"
    with pytest.raises(ValueError, match="Voice session"):
        view.accept(value)


def test_stale_epoch_cannot_modify_new_view() -> None:
    view = consumer()
    value = message("ActionUpdated")
    value["visibility_epoch"] = "retired-epoch"
    before = view.summary()
    assert view.accept(value) == "ignored"
    assert view.summary() == before


def test_duplicate_narration_chunks_do_not_repeat_text() -> None:
    view = consumer()
    view.accept(message("ActionUpdated"))
    view.accept(message("NarrationStarted"))
    assert view.accept(message("NarrationDelta")) == "narration"
    assert view.accept(message("NarrationDelta")) == "ignored"
    assert len(next(iter(view.narrations.values())).chunks) == 1
