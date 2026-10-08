"""B244's Haste alternative drives actual Deathtouch hosts from lawful genesis."""

import secrets
from pathlib import Path

import pytest
from support.hand_deathtouch import fixture as hand_fixture
from support.hand_deathtouch import revision
from support.melee_spell import cast as staff_cast
from support.melee_spell import fixture as staff_fixture
from support.runtime import build_play, played
from test_hand_deathtouch_host import prepare_contact
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.hand_melee_contact_state import contact_results
from wayfarer.engine.simulation.magic.hand_melee_spell_state import (
    CastHandDeathtouch,
    HandMeleeCast,
)
from wayfarer.engine.simulation.magic.hand_melee_spell_state import (
    casts as hand_casts,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    CastDeathtouch,
    HandCarrier,
    MeleeCast,
    StaffCarrier,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    casts as staff_casts,
)
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.hand_melee_spells import HandMeleeSpellService
from wayfarer.orchestration.melee_spells import MeleeSpellService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("carrier", ["hand", "staff"])
async def test_haste_prerequisite_route_executes_retries_and_replays(
    tmp_path: Path, backend: str, carrier: str
) -> None:
    fixture = hand_fixture if carrier == "hand" else staff_fixture
    cid, play, initial = await fixture(tmp_path, backend, haste_route=True)
    before = play._load(await play.store.read(cid))
    purchases = {p.definition_id for p in before.actors[0].proposal.draft.purchases}
    assert "spell:clumsiness" not in purchases
    assert {"spell:haste", "spell:rooted-feet"} <= purchases
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    charge: HandMeleeCast | MeleeCast
    if carrier == "hand":
        await prepare_contact(play, cid, energy=3)
        play.seeds = lambda: f"{19:064x}"
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="defense",
                actor_id="b",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                defense="none",
            ),
            principal_id="b",
        )
        state = play._load(await play.store.read(cid))
        result = contact_results(state.resources)[0]
        assert result.outcome == "discharged" and result.injury == sum(result.dice) > 0
        charge = hand_casts(state.resources)["hand"]
        assert charge.status == "spent" and charge.energy == 3
        complete: CastHandDeathtouch | CastDeathtouch = CastHandDeathtouch(
            id="hand-complete",
            actor_id="a",
            expected_revision=before.revision + 2,
            operation="complete",
            cast_id="hand",
            energy=3,
            carrier=HandCarrier(hand="right-hand"),
        )
    else:
        await staff_cast(play, cid, energy=3)
        state = play._load(await play.store.read(cid))
        charge = staff_casts(state.resources)["death"]
        assert charge.status == "held" and charge.energy == 3
        complete = CastDeathtouch(
            id="death-complete",
            actor_id="a",
            expected_revision=before.revision + 2,
            operation="complete",
            cast_id="death",
            energy=3,
            carrier=StaffCarrier(hand="right-hand", item_id="real-staff"),
        )
    assert charge.check is not None and charge.check.outcome.succeeded
    assert charge.credited_seconds == 1
    saved = await play.store.read(cid)
    history = await play.store.history(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    service = (
        HandMeleeSpellService(restarted) if carrier == "hand" else MeleeSpellService(restarted)
    )
    retried = await service.execute(cid, complete, principal_id="alice")
    assert retried.outcome == "held" and retried.energy_spent == charge.paid_fp
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
    assert await play.store.history(cid) == history
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, evidence = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert evidence and all(e.folded and e.reexecuted for e in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)


@pytest.mark.parametrize(
    "missing", ["haste", "rooted-feet", "pain", "paralyze-limb", "wither-limb"]
)
async def test_haste_route_missing_prerequisites_never_create_approved_genesis(
    tmp_path: Path, missing: str
) -> None:
    with pytest.raises(ValueError, match="Illegal original hand Deathtouch construction"):
        await hand_fixture(tmp_path, "sqlite", haste_route=True, missing_prerequisite=missing)
