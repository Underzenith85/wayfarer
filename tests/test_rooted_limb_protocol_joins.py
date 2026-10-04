"""Shared protocol guards tested against a genuinely accepted Rooted Feet producer."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.rooted_feet import cast, fixture, observe, revision
from support.runtime import played

from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.concentration import require_no_held_melee
from wayfarer.engine.simulation.magic.rooted_feet_state import expire
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import payload_digest


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("generation", [True, 1.0, 2, None])
async def test_actual_rooted_record_generation_is_strict(
    tmp_path: Path, backend: str, generation: bool | float | None
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet
    from wayfarer.orchestration.rooted_feet import RootedFeetService

    await observe(play, cid)
    play.seeds = lambda: f"{1:064x}"
    await RootedFeetService(play).execute(
        cid,
        CastRootedFeet(
            id="root",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="root",
            subject_id="subject",
        ),
        principal_id="cora",
    )
    record = next(
        r
        for r in reversed(await played(play.store, cid))
        if r.command_input
        and validation.mapping(replay_payload(r.command_input)).get("operation") == "rooted-feet"
    )
    payload = validation.mapping(replay_payload(record.command_input or "{}"))
    encoded = json.dumps({**payload, "generation": generation}, sort_keys=True)
    candidate = replace(
        record, command_input=encoded, payload_hash=payload_digest({"input": encoded})
    )
    saved = await play.store.read(cid)
    history = await play.store.history(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="captured generation"):
        await execute_recorded(play, candidate)
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_rooted_spell_on_guard_is_caster_only_and_expires(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid, dice=(1, 1, 1))
    resources = play._load(await play.store.read(cid)).resources
    with pytest.raises(ConflictError, match="spell-on composition"):
        require_no_held_melee(resources, "c")
    require_no_held_melee(resources, "b")
    expired = expire(resources, resources.game_time + 60)
    require_no_held_melee(expired, "c")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_rooted_identify_inventory_refuses_live_but_not_expired_history(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.identify_spell_admission import _private_spell_producers

    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid, dice=(1, 1, 1))
    state = play._load(await play.store.read(cid))
    for actor in ("b", "c"):
        with pytest.raises(ConflictError, match="unsupported private spell producer"):
            _private_spell_producers(state, actor)
    later = state.resources.game_time + 60
    resources = expire(state.resources, later).model_copy(update={"game_time": later})
    expired = state.model_copy(update={"resources": resources})
    for actor in ("b", "c"):
        _private_spell_producers(expired, actor)
