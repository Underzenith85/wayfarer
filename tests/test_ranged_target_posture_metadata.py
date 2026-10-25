"""Independent B551 launch generation preserves exact caller bytes and old inputs."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_actions import campaign, world
from test_ranged_target_postures import ready, shoot

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.attack_targets import require_pending_minimum
from wayfarer.engine.simulation.combat.ranged_target_policy import PREFIX, RangedTargetPolicy
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import TakeCombatTurn
from wayfarer.orchestration.views import campaign_view
from wayfarer.persistence.command_inputs import (
    DEFENSE_POSTURE_KEY,
    KEY,
    RANGED_TARGET_POSTURE_KEY,
    canonical,
    ranged_target_posture_generation,
    replay_payload,
    same_input,
    stamp,
    stamp_defense_postures,
    stamp_ranged_target_postures,
)
from wayfarer.persistence.events import CommandInput, payload_digest


def record(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


@pytest.mark.parametrize(
    "original", ['{ "operation": "combat", "choice": 1 }', "  [1, 2]  ", "opaque exact input"]
)
@pytest.mark.parametrize("defense", [False, True])
def test_new_launch_generation_is_independent_and_preserves_exact_intent(
    original: str, defense: bool
) -> None:
    old = stamp(original, version=3)
    assert not ranged_target_posture_generation(record(old))
    text = stamp_ranged_target_postures(stamp_defense_postures(old) if defense else old)
    payload = json.loads(text)
    assert payload[KEY] == 3 and payload[RANGED_TARGET_POSTURE_KEY] == 1
    assert payload.get(DEFENSE_POSTURE_KEY) == (1 if defense else None)
    assert ranged_target_posture_generation(record(text))
    assert same_input(record(text), original)
    assert replay_payload(text) == (
        json.loads(original) if original.lstrip().startswith(("{", "[")) else original
    )
    assert not same_input(record(text), original + " ")


@pytest.mark.parametrize("bad", [True, False, 0, 2, "1", None])
def test_private_launch_generation_rejects_bad_versions(bad: object) -> None:
    payload = json.loads(stamp('{"operation":"combat"}', version=3))
    payload[RANGED_TARGET_POSTURE_KEY] = bad
    with pytest.raises(ValidationError, match="Unsupported recorded ranged"):
        ranged_target_posture_generation(record(canonical(payload)))


def test_launch_generation_requires_its_validated_envelope_and_digest() -> None:
    with pytest.raises(ValidationError, match="requires command metadata"):
        ranged_target_posture_generation(record(canonical({RANGED_TARGET_POSTURE_KEY: 1})))
    text = stamp_ranged_target_postures(stamp('{"operation":"combat"}'))
    with pytest.raises(ValidationError, match="digest"):
        ranged_target_posture_generation(CommandInput("0" * 64, text))
    payload = json.loads(text)
    payload["operation"] = "changed"
    with pytest.raises(ValidationError, match="original"):
        ranged_target_posture_generation(record(canonical(payload)))


def test_player_cannot_select_the_private_posture_generation() -> None:
    with pytest.raises(SchemaError, match="Extra inputs are not permitted"):
        TakeCombatTurn.model_validate(
            {
                "id": "forged",
                "actor_id": "a",
                "expected_revision": 0,
                "encounter_id": "fight",
                "maneuver": "attack",
                "item_id": "sword-a",
                "target_id": "b",
                RANGED_TARGET_POSTURE_KEY: 1,
            }
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_private_launch_policy_cannot_be_seeded_or_projected(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await ready(tmp_path, backend, "throw")
    await shoot(cid, play)
    state = play._load(await play.store.read(cid))
    events = tuple(event for event in state.resources.events if event.id.startswith(PREFIX))
    assert events
    for member in state.members:
        visible = json.dumps(campaign_view(state, member, play.engine.rules.combat))
        assert PREFIX not in visible and RANGED_TARGET_POSTURE_KEY not in visible
    with pytest.raises(ValidationError, match="execution receipts"):
        play.initial_state(campaign(play.engine), world(), ResourceState(events=events), ())


@pytest.mark.parametrize("bad", [True, False, 1.0, "1", 0, 2, None])
def test_private_policy_requires_exact_integer_one(bad: object) -> None:
    with pytest.raises(SchemaError, match="exact integer generation 1"):
        RangedTargetPolicy.model_validate_json(json.dumps({"generation": bad}))
    assert RangedTargetPolicy.model_validate_json('{"generation":1}').generation == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("forgery", ["boolean", "float", "wrong-owner", "duplicate"])
async def test_actual_launch_forged_receipt_refuses_canonical_preflight_before_entropy(
    tmp_path: Path, backend: str, forgery: str
) -> None:
    cid, play = await ready(tmp_path, backend, "throw")
    await shoot(cid, play)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    legitimate = next(event for event in state.resources.events if event.id.startswith(PREFIX))
    if forgery == "duplicate":
        events = state.resources.events + (legitimate,)
    else:
        forged = legitimate.model_copy(
            update={"target_id": "b"}
            if forgery == "wrong-owner"
            else {"kind": '{"generation":true}' if forgery == "boolean" else '{"generation":1.0}'}
        )
        events = tuple(
            forged if event.id == legitimate.id else event for event in state.resources.events
        )
    # Deliberately corrupt a restored private receipt, without claiming the
    # forged state is lawful genesis or using it to supply rule geometry.
    corrupt = state.model_copy(
        update={"resources": state.resources.model_copy(update={"events": events})}
    )
    play.rng = RecordedDice([])
    with pytest.raises(
        ValidationError, match="recorded ranged target policy|Recorded ranged target policy"
    ):
        require_pending_minimum(play.rules_context, corrupt, corrupt.encounters[0])
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
