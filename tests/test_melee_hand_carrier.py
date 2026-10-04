"""Pure named-hand diagnostics; no hand charge or delivery acceptance claim."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.melee_spell import fixture
from test_staff_melee_contact import _charged_encounter

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.location import LastingInjury
from wayfarer.engine.simulation.combat.unarmed.records import Grip
from wayfarer.engine.simulation.magic.melee_hand_carrier import require_productive_hand
from wayfarer.errors import ConflictError, ValidationError


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_empty_named_hand_uses_current_approved_body_and_real_mana_without_mutation(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    before = state.model_dump_json()
    dice = RecordedDice(())
    runtime = replace(play.rules_context, rng=dice)
    right = require_productive_hand(runtime, state, actor_id="b", hand="right-hand")
    left = require_productive_hand(
        runtime, state, actor_id="b", hand="left-hand", build_revision=right.revision
    )
    assert right == left and right.statistics is not None
    assert not any(i.owner_id == "b" and i.enchantments for i in state.resources.items)
    assert state.model_dump_json() == before and dice.exhausted()

    with pytest.raises(ConflictError, match="source revision"):
        require_productive_hand(
            runtime, state, actor_id="b", hand="right-hand", build_revision="untrusted-revision"
        )
    # The fixture's actual manufactured Staff occupies the caster's named hand.
    # This query only sees occupancy; it neither validates enchantment nor drains it.
    with pytest.raises(ConflictError, match="empty"):
        require_productive_hand(runtime, state, actor_id="a", hand="right-hand")
    assert state.model_dump_json() == before and dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_named_control_grips_limit_only_the_current_affected_hand(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await _charged_encounter(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    # Explicit canonical-record diagnostics, not fabricated registered grapples.
    grip = Grip(
        id="diagnostic-hand-grip",
        holder_id="b",
        target_id="a",
        hands=("left-hand",),
        location="torso",
        skill="skill:wrestling",
        acquired_round=encounter.round,
    )
    held = state.model_copy(
        update={"encounters": (encounter.model_copy(update={"grips": (grip,)}),)}
    )
    dice = RecordedDice(())
    runtime = replace(play.rules_context, rng=dice)
    with pytest.raises(ConflictError, match="control grip"):
        require_productive_hand(runtime, held, actor_id="b", hand="left-hand")
    require_productive_hand(runtime, held, actor_id="b", hand="right-hand")
    arm_grip = grip.model_copy(update={"holder_id": "a", "target_id": "b", "location": "left-arm"})
    controlled = state.model_copy(
        update={"encounters": (encounter.model_copy(update={"grips": (arm_grip,)}),)}
    )
    with pytest.raises(ConflictError, match="arm is currently grappled"):
        require_productive_hand(runtime, controlled, actor_id="b", hand="left-hand")
    require_productive_hand(runtime, controlled, actor_id="b", hand="right-hand")
    assert dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("condition", ["disabled", "missing-mana", "lost-approval", "stunned"])
async def test_current_named_hand_refusal_has_no_rng_or_lifetime_side_effect(
    tmp_path: Path,
    backend: str,
    condition: Literal["disabled", "missing-mana", "lost-approval", "stunned"],
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    cached = require_productive_hand(play.rules_context, state, actor_id="b", hand="left-hand")
    if condition == "lost-approval":
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"approval": None}) if a.actor_id == "b" else a
                    for a in state.actors
                )
            }
        )
    elif condition == "missing-mana":
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "events": tuple(
                            e
                            for e in state.resources.events
                            if not e.id.startswith("melee-spell:mana:")
                        )
                    }
                )
            }
        )
    else:
        hp = next(p for p in state.resources.pools if p.id == "hp:b")
        assert hp.injury is not None
        status = (
            hp.injury.model_copy(update={"stunned": True})
            if condition == "stunned"
            else (
                hp.injury.model_copy(
                    update={
                        "lasting_injuries": hp.injury.lasting_injuries
                        + (
                            LastingInjury(
                                id="diagnostic-arm",
                                location="left-arm",
                                kind="crippled",
                                duration="permanent",
                                inflicted_at=state.resources.game_time,
                                injury=0,
                            ),
                        )
                    }
                )
            )
        )
        updated = hp.model_copy(update={"injury": status})
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            updated if p.id == hp.id else p for p in state.resources.pools
                        )
                    }
                )
            }
        )
    before = state.model_dump_json()
    dice = RecordedDice(())
    # Even a captured accepted attack build cannot bypass current lost approval.
    runtime = replace(play.rules_context, rng=dice, attack_source=("b", cached))
    with pytest.raises((ConflictError, ValidationError)):
        require_productive_hand(runtime, state, actor_id="b", hand="left-hand")
    assert state.model_dump_json() == before and dice.exhausted()
