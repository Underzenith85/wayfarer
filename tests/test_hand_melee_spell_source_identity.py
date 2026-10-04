"""Explicit current-body identity diagnostics; no new registered delivery claim."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.hand_deathtouch import fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.hand_melee_spell_admission import hand_digest
from wayfarer.engine.simulation.magic.hand_melee_spell_state import CastHandDeathtouch, casts
from wayfarer.engine.simulation.magic.hand_melee_spell_transitions import apply
from wayfarer.engine.simulation.magic.melee_spell_state import HandCarrier
from wayfarer.errors import ConflictError


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_body_configuration_change_does_not_reuse_same_approved_hand_source(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    dice = RecordedDice(())
    runtime = replace(play.rules_context, rng=dice)
    start = CastHandDeathtouch(
        id="hand-start",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        cast_id="hand",
        energy=3,
        carrier=HandCarrier(hand="right-hand"),
    )
    state, _ = apply(runtime, state, start)
    accepted = casts(state.resources)["hand"]
    actor = next(a for a in state.actors if a.actor_id == "a")
    assert actor.body is not None
    changed_actor = actor.model_copy(
        update={"body": actor.body.model_copy(update={"male_groin": not actor.body.male_groin})}
    )
    changed = state.model_copy(
        update={"actors": tuple(changed_actor if a.actor_id == "a" else a for a in state.actors)}
    )
    assert changed_actor.approval == actor.approval
    assert (
        hand_digest(changed, "a", "right-hand", accepted.build_revision) != accepted.carrier_digest
    )
    before = changed.model_dump_json()
    with pytest.raises(ConflictError, match="source or carrier changed"):
        apply(
            runtime,
            changed,
            start.model_copy(update={"id": "hand-work", "operation": "concentrate"}),
        )
    assert changed.model_dump_json() == before and dice.exhausted()

    # Current HP/FP are consequences, never source-configuration identity fields.
    health = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": p.current - 1})
                        if p.id in ("hp:a", "fp:a")
                        else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    assert (
        hand_digest(health, "a", "right-hand", accepted.build_revision) == accepted.carrier_digest
    )
