"""Strict private B173 producer metadata, provenance and player boundaries."""

import json
from pathlib import Path

import pytest
from support.healing_physician import fixture, start
from support.runtime import build_runtime

from wayfarer.engine.simulation.magic.physician_training import PREFIX, correct
from wayfarer.errors import ValidationError
from wayfarer.orchestration.projections import ViewRequest, project
from wayfarer.persistence.command_inputs import same_input, stamp
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.healing_physician_inputs import KEY, ORIGINAL, intent
from wayfarer.persistence.healing_physician_inputs import stamp as physician_stamp


def public() -> str:
    return json.dumps(
        {
            "operation": "spell-lifecycle",
            "principal_id": "a",
            "command": {
                "id": "begin",
                "actor_id": "a",
                "cast_id": "cast",
                "kind": "start",
                "spell_id": "minor-healing",
                "energy": 2,
            },
        },
        sort_keys=True,
    )


def record(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


@pytest.mark.parametrize("value", [True, 1.0, "1", None, 0, 2])
def test_training_input_versions_are_strict(value: object) -> None:
    data = json.loads(physician_stamp(public()))
    data[KEY] = value
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValidationError, match="healing Physician generation"):
        same_input(record(text), public())


@pytest.mark.parametrize("key", [KEY, ORIGINAL])
@pytest.mark.parametrize("outer", [False, True])
def test_duplicate_training_metadata_is_refused_before_outer_unwrap(key: str, outer: bool) -> None:
    text = physician_stamp(public())
    if outer:
        text = stamp(text, version=3)
    data = json.loads(text)
    fragment = json.dumps(key) + ":" + json.dumps(data[key], separators=(",", ":"))
    malformed = text.replace(fragment, fragment + "," + fragment)
    assert malformed != text
    with pytest.raises(ValidationError, match="Duplicate healing"):
        same_input(record(malformed), public())


def test_original_public_intent_is_byte_exact_and_binds_its_own_host() -> None:
    raw = public()
    text = physician_stamp(raw)
    assert intent(text) == raw and same_input(record(text), raw)
    assert not same_input(record(text), raw + " ")
    assert not same_input(record(text), raw.replace('"energy": 2', '"energy": 3'))
    data = json.loads(text)
    data[ORIGINAL] = raw.replace('"energy": 2', '"energy": 3')
    altered = json.dumps(data, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValidationError, match="healing Physician generation"):
        same_input(record(altered), raw)
    data = json.loads(text)
    data["unexpected"] = 1
    with pytest.raises(ValidationError, match="healing Physician generation"):
        intent(json.dumps(data, sort_keys=True, separators=(",", ":")))
    with pytest.raises(ValidationError, match="digest"):
        same_input(CommandInput("incorrect-digest", text), raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", True),
        ("generation", 1.0),
        ("started_at", True),
        ("started_at", 0.0),
        ("command_id", "society-adopt"),
        ("actor_id", "b"),
        ("target_id", "a"),
        ("spell_id", "major-healing"),
        ("build_revision", "other-build"),
    ],
)
async def test_bound_origin_refuses_forged_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    cid, play = await fixture(tmp_path, "sqlite", "point")
    command = await start(play, cid, "minor-healing")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith(PREFIX))
    data = json.loads(event.kind)
    data[field] = value
    resources = state.resources.model_copy(
        update={
            "events": tuple(
                e.model_copy(update={"kind": json.dumps(data)}) if e.id == event.id else e
                for e in state.resources.events
            )
        }
    )
    with pytest.raises(ValidationError, match="healing Physician|Healing Physician"):
        correct(resources, command.model_copy(update={"kind": "complete"}))


async def test_duplicate_origin_and_missing_original_receipt_refuse(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path, "sqlite", "point")
    command = await start(play, cid, "minor-healing")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith(PREFIX))
    complete = command.model_copy(update={"kind": "complete"})
    assert correct(state.resources, complete)
    with pytest.raises(ValidationError, match="Duplicate"):
        correct(
            state.resources.model_copy(update={"events": state.resources.events + (event,)}),
            complete,
        )
    with pytest.raises(ValidationError, match="accepted cast"):
        correct(
            state.resources.model_copy(
                update={
                    "receipts": tuple(
                        r for r in state.resources.receipts if r.command_id != command.id
                    )
                }
            ),
            complete,
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_training_origin_stays_private_and_cannot_enter_authored_genesis(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "default")
    await start(play, cid, "minor-healing")
    state = play._load(await play.store.read(cid))
    runtime = build_runtime(play)
    assert PREFIX not in str(await runtime.read(cid, principal_id="a"))
    member = next(m for m in state.members if m.principal_id == "a")
    assert PREFIX not in str(project("stream", ViewRequest(runtime, state, member)))
    initial = (await play.store.history(cid))[0].state_after.copy()
    initial["revision"] = 0
    marker = next(e for e in state.resources.events if e.id.startswith(PREFIX))
    with pytest.raises(ValidationError, match="reserved|runtime|private|receipt|command"):
        play.initial_state(
            initial,
            state.world,
            state.resources.model_copy(update={"revision": 0, "events": (marker,)}),
            (),
        )
