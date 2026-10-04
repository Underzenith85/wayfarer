"""Real hidden weapon declaration cannot score unsupported Rooted Dodge context."""

from pathlib import Path

import pytest
from support.rooted_feet import cast, fixture, revision
from test_special_combat_situations import hidden_facts

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.melee.resolution import _require_rooted_dodge_context
from wayfarer.engine.simulation.magic.rooted_feet_state import active_effect
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartBasicEncounter,
    TakeCombatTurn,
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_rooting_and_hidden_weapon_attack_refuse_dodge_before_any_dice(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, combat_weapons=True)
    await cast(play, cid)
    service = CombatService(play)
    current_revision = await revision(play, cid)
    facts = tuple(
        fact.model_copy(
            update={
                "provenance": fact.provenance.model_copy(
                    update={"source_id": "dock", "declared_revision": current_revision}
                )
            }
        )
        for fact in hidden_facts(defender_knows_location=False)
    )
    await service.execute(
        cid,
        StartBasicEncounter(
            id="hidden-rooted-fight",
            actor_id="gm",
            expected_revision=current_revision,
            encounter_id="fight",
            scene_id="dock",
            participant_ids=("a", "b"),
            facts=facts,
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="hidden-weapon",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        principal_id="a",
    )
    checkpoint = await play.store.read(cid)
    state = play._load(checkpoint)
    pending = state.encounters[0].pending_defense
    assert pending is not None
    assert active_effect(state.resources, "b") is not None
    assert pending.visibility_defense_penalty == -4 and pending.attention_defense_penalty == 0
    assert pending.allowed == ("none", "dodge")
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(
        ValidationError,
        match="^Selected defense is unavailable with current sensory and physical conditions$",
    ):
        await service.execute(
            cid,
            ChooseDefense(
                id="unsupported-rooted-dodge",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                defense="dodge",
            ),
            principal_id="b",
        )
    assert play.rng.exhausted()
    assert await play.store.read(cid) == checkpoint
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    # Explicit helper diagnostic: the same unsupported context must also be refused
    # when Dodge is the second defense. This is not a fabricated public Double case.
    with pytest.raises(ValidationError, match="Rooted Feet Dodge"):
        _require_rooted_dodge_context(state, pending, "parry", "dodge")
    # Attention-only coverage is a diagnostic of the same guard, not a claim
    # that this fixture authored a Dual-Weapon Attack.
    attention = pending.model_copy(
        update={"visibility_defense_penalty": 0, "attention_defense_penalty": -1}
    )
    with pytest.raises(ValidationError, match="Rooted Feet Dodge"):
        _require_rooted_dodge_context(state, attention, "dodge", None)
    _require_rooted_dodge_context(state, pending, "parry", None)
    assert play.rng.exhausted()
