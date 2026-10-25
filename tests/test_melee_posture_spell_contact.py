"""B551 posture penalty applies once to the real B240/B245 charged punch."""

from pathlib import Path

import pytest
from support.hand_deathtouch import revision
from test_gurps_maneuvers import turn
from test_hand_melee_striking_skills import Skill, declare, ready

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.hand_melee_contact_state import contact_results
from wayfarer.engine.simulation.magic.hand_melee_spell_state import casts
from wayfarer.orchestration.combat import ChooseDefense, CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill", ["skill:boxing", "skill:karate"])
async def test_actual_sitting_charged_punch_preserves_distinct_physical_and_spell_packets(
    tmp_path: Path, backend: str, skill: Skill
) -> None:
    cid, play, _ = await ready(tmp_path, backend, skill)
    await turn(cid, play, "a", "change_posture", posture="sitting")
    await turn(cid, play, "b", "do_nothing")
    contact = await declare(play, cid, skill)
    hit = skill == "skill:boxing"
    # Boxing12 becomes10; Karate11 becomes9. Roll10 distinguishes the real
    # posture consequence and whether the independently paid spell discharges.
    play.rng = RecordedDice([3, 3, 4] + ([3, 1, 1, 1] if hit else []))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="sitting-contact",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.unarmed
    assert result.unarmed.checks[0].effective_target == (10 if hit else 9)
    assert result.unarmed.checks[0].outcome.succeeded == hit
    assert result.unarmed.injury == (2 if hit else 0)
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    magical = contact_results(state.resources)
    if hit:
        assert magical[-1].pending_id == contact.pending_id
        assert magical[-1].triggered and magical[-1].injury == 3
    else:
        assert not any(value.triggered for value in magical)
    assert casts(state.resources)["hand"].status == ("spent" if hit else "held")
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (5 if hit else 10)
