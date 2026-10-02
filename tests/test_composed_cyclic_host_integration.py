"""B103–104/B443: approved delivery reaches actual independent disease clocks."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_composed_attack_host import declare, defense, fixture
from test_composed_cyclic_boundaries import cyclic

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.hazard import blocked_hp
from wayfarer.engine.simulation.combat.commands import EndEncounter
from wayfarer.engine.simulation.health.cyclic_host_state import (
    CyclicPolicy,
    ObserveCyclicExposure,
    ObserveCyclicStop,
    binding,
)
from wayfarer.engine.simulation.traits.harmful_physiology_state import AdvancePhysiology
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_approved_delivery_contact_cure_and_independent_secondary_clock(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(
        tmp_path,
        backend,
        modifiers=(cyclic("tox", contagious="mild"),),
        kind="tox",
        levels=1,
        cyclic_policy=CyclicPolicy(condition="treatment"),
        contagion_vector="respiratory",
        incubation_seconds=86400,
    )
    attack = await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2, 2))
    defense_command, _ = await defense(play, cid)
    state = play._load(await play.store.read(cid))
    primary = state.resources.cyclic_attacks[0]
    origin = binding(state.resources, primary.id)
    assert origin and origin.source_id == source and primary.actor_id == "b"
    assert primary.hp_debt == 2 and primary.due == 86400
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 8
    assert blocked_hp(state.resources.illnesses, "b", "natural") == 2
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="disengage",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            reason="The combatants disengage",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    contact = ObserveCyclicExposure(
        id="real-contact",
        actor_id="a",
        expected_revision=state.revision,
        source_occurrence_id=primary.id,
        location_id="dock",
        contact="intimate-contact",
        route="shared-air",
        reason="Observed intimate respiratory contact with the affected carrier",
    )
    await CyclicService(play).execute(cid, contact, principal_id="gm")
    state = play._load(await play.store.read(cid))
    stop = ObserveCyclicStop(
        id="carrier-cured",
        actor_id="b",
        expected_revision=state.revision,
        occurrence_id=primary.id,
        location_id="dock",
        reason="Observed completion of the source-approved treatment",
    )
    await CyclicService(play).execute(cid, stop, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert blocked_hp(state.resources.illnesses, "b", "natural") == 0
    # B443: failed HT7 exposure at day-end causes initial disease damage2,
    # with no second pre-onset HT roll. Treating the carrier doesn't cure contact.
    play.rng = RecordedDice((5, 5, 5, 2))
    onset = AdvancePhysiology(
        id="first-day", actor_id="a", expected_revision=state.revision, to=86400
    )
    await HarmfulPhysiologyService(play).execute(cid, onset, principal_id="gm")
    state = play._load(await play.store.read(cid))
    secondary = next(a for a in state.resources.cyclic_attacks if a.actor_id == "a")
    assert secondary.active and secondary.hp_debt == 2 and secondary.due == 172800
    assert binding(state.resources, secondary.id) is not None
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 8
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 8
    assert blocked_hp(state.resources.illnesses, "a", "natural") == 2
    assert play.rng.exhausted()
    # The secondary has its own B443 recovery HT, even though the direct
    # non-Resistible primary had none. Success prevents its next damage.
    play.rng = RecordedDice((2, 2, 2))
    recovery = AdvancePhysiology(
        id="second-day", actor_id="a", expected_revision=state.revision, to=172800
    )
    await HarmfulPhysiologyService(play).execute(cid, recovery, principal_id="gm")
    final = await play.store.read(cid)
    state = play._load(final)
    assert not any(a.active for a in state.resources.cyclic_attacks)
    assert blocked_hp(state.resources.illnesses, "a", "natural") == 0
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 8
    assert play.rng.exhausted()
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    await ComposedAttackService(restarted).execute(cid, attack, principal_id="alice")
    await CombatService(restarted).execute(cid, defense_command, principal_id="bob")
    await CyclicService(restarted).execute(cid, contact, principal_id="gm")
    await CyclicService(restarted).execute(cid, stop, principal_id="gm")
    await HarmfulPhysiologyService(restarted).execute(cid, onset, principal_id="gm")
    await HarmfulPhysiologyService(restarted).execute(cid, recovery, principal_id="gm")
    assert await restarted.store.read(cid) == final == await restarted.store.replay(cid)
    assert len(state.resources.cyclic_attacks) == 2
