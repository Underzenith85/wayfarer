"""Actual manufactured Staff charge enters the ordinary pending-defense host."""

import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.melee_spell import cast, fixture, revision
from support.runtime import build_play, played
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.objects.locations import bind_ready_hand
from wayfarer.engine.simulation.combat.settlement import reconcile_equipment
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.melee_spell_state import (
    MeleeSpellContactResult,
    casts,
    read_contact,
)
from wayfarer.engine.simulation.resources import Equip, Unequip
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def _charged_encounter(
    tmp_path: Path,
    backend: str,
    *,
    defender_item: Literal["staff", "shield"] | None = None,
    armor: bool = False,
    victim_mode: Literal["dead", "diffuse"] | None = None,
) -> tuple[str, PlayService, Campaign]:
    cid, play, initial = await fixture(
        tmp_path,
        backend,
        defender_item=defender_item,
        defender_armor=armor,
        victim_mode=victim_mode,
    )
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    await play.execute(
        cid,
        Wait(id="next-second", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="fight-start",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    return cid, play, initial


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "selected,succeeds,critical_table,seeded,defender_item,physical_table,armor,lethal",
    [
        ("none", False, None, False, None, None, False, False),
        ("none", False, None, True, None, None, False, False),
        ("dodge", True, None, False, None, None, False, False),
        ("dodge", False, None, False, None, None, False, False),
        ("parry", True, None, False, None, None, False, False),
        ("parry", True, (5, 5, 5), False, None, None, False, False),
        ("parry", False, None, False, None, None, False, False),
        ("parry", False, (3, 3, 3), False, None, None, False, False),
        ("parry", False, (1, 1, 2), False, None, None, False, False),
        ("parry", False, (1, 1, 1), False, None, None, False, False),
        ("parry", True, None, False, "staff", None, False, False),
        ("block", True, None, False, "shield", None, False, False),
        ("none", False, None, False, None, (2, 2, 2), False, False),
        ("none", False, None, False, None, (1, 2, 2), False, False),
        ("none", False, None, False, None, (1, 1, 1), False, False),
        ("none", False, None, False, None, None, True, False),
        ("none", False, None, False, None, (1, 1, 1), False, True),
    ],
)
async def test_manufactured_held_staff_attaches_actual_pending_contact(
    tmp_path: Path,
    backend: str,
    selected: Literal["none", "dodge", "parry", "block"],
    succeeds: bool,
    critical_table: tuple[int, ...] | None,
    seeded: bool,
    defender_item: Literal["staff", "shield"] | None,
    physical_table: tuple[int, ...] | None,
    armor: bool,
    lethal: bool,
) -> None:
    cid, play, initial = await _charged_encounter(
        tmp_path, backend, defender_item=defender_item, armor=armor
    )
    service = CombatService(play)
    await service.execute(
        cid,
        TakeCombatTurn(
            id="staff-attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="real-staff",
            mode_id="staff-swing" if lethal else "staff-thrust",
            target_id="b",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    contact = read_contact(state.resources, pending.id)
    assert contact is not None
    assert contact.cast_id == "death" and contact.carrier_item_id == "real-staff"
    assert contact.pending_id == pending.id and contact.energy == 3

    before_hp = next(p.current for p in state.resources.pools if p.id == "hp:b")
    command = ChooseDefense(
        id="staff-defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense=selected,
        item_id="defender-implement"
        if defender_item
        else "left-hand"
        if selected == "parry"
        else None,
        parry_mode_id="staff-thrust" if defender_item == "staff" else None,
    )
    before_response = await play.store.read(cid)
    before_history = await play.store.history(cid)
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            command.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id="b",
        )
    assert before_response == await play.store.read(cid)
    assert before_history == await play.store.history(cid)
    defense_dice = () if selected == "none" else ((2, 2, 2) if succeeds else (4, 4, 4))
    if critical_table is not None:
        defense_dice = (1, 1, 1) if succeeds else (6, 6, 6)
    paused = critical_table == (1, 1, 1)
    hit = not succeeds and not paused
    triggers = hit or (
        succeeds and (selected == "block" or selected == "parry" and defender_item is None)
    )
    play.rng = RecordedDice(
        (3, 3, 3)
        + defense_dice
        + (critical_table or ())
        + ((1,) if succeeds and critical_table == (5, 5, 5) else ())
        + ((1,) if hit else ())
        + ((1, 1, 1) if triggers else ())
    )
    if physical_table is not None:
        play.rng = RecordedDice(
            (1, 1, 1)
            + physical_table
            + ((1, 1, 1) if sum(physical_table) == 6 else (1,))
            + (1, 1, 1)
        )
    if lethal:
        play.rng = RecordedDice((1, 1, 1) + (1, 1, 1) + (6,) + (6, 6, 6) + (1, 1, 1))
    if seeded:
        play.rng = secrets
    if selected == "none" and not seeded and physical_table is None and not armor:
        failing = FailingCommitPlay(
            play.store, play.engine, rng=RecordedDice((3, 3, 3, 1, 1, 1, 1))
        )
        before_stream = await play.store.stream(cid)
        with pytest.raises(RuntimeError, match="crash after candidate checkpoint"):
            await submit(failing, cid, CombatService(failing).plan(cid, command), principal_id="b")
        assert await play.store.read(cid) == before_response
        assert await play.store.history(cid) == before_history
        assert await play.store.stream(cid) == before_stream
    result = await service.execute(cid, command, principal_id="b")
    assert result.injury is not None
    if critical_table is not None:
        assert result.injury.critical_table == critical_table
    assert (result.injury.adjudication_required is not None) == paused
    assert (result.injury.injury > 0) == (hit and not armor)
    if armor:
        assert result.injury.resistance == 2 and result.injury.injury == 0
        assert result.injury.basic_damage == 1
    if physical_table is not None:
        assert result.injury.critical_table == physical_table
        assert result.injury.injury == (
            24
            if lethal
            else 6
            if sum(physical_table) == 6
            else 3
            if sum(physical_table) == 3
            else 2
        )
    after = play._load(await play.store.read(cid))
    magical = MeleeSpellContactResult.model_validate_json(
        next(e.kind for e in after.resources.events if e.id.startswith("melee-spell:result:"))
    )
    assert magical.triggered == triggers
    assert magical.hp_after == magical.hp_before - magical.injury
    if lethal:
        victim = next(p for p in after.resources.pools if p.id == "hp:b")
        assert victim.injury is not None and victim.injury.dead
        assert result.injury.incapacitated
    assert magical.injury == (sum(magical.dice) if seeded else 3 if triggers else 0)
    if not seeded:
        assert magical.dice == ((1, 1, 1) if triggers else ())
    assert casts(after.resources)["death"].status == ("spent" if triggers else "held")
    if succeeds and critical_table == (5, 5, 5):
        from wayfarer.engine.simulation.magic.melee_staff_carrier import carrier_digest

        with pytest.raises(ConflictError, match="hand"):
            carrier_digest(after, "a", casts(after.resources)["death"].carrier, require_action=True)
        assert magical.injury == 3
    assert (
        next(p.current for p in after.resources.pools if p.id == "hp:b")
        == before_hp - int(critical_table == (1, 1, 2)) - result.injury.injury - magical.injury
    )
    assert result.injury.hp_before == before_hp - int(critical_table == (1, 1, 2))
    assert result.injury.hp_after == result.injury.hp_before - result.injury.injury
    play.rng = RecordedDice(())
    assert await service.execute(cid, command, principal_id="b") == result
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)

    if seeded:
        records = await played(play.store, cid)
        ids = {r.command_id for r in records}
        replayed, checks = await verify_commands(
            initial,
            records,
            [e for e in await play.store.stream(cid) if e.command_id in ids],
            configuration_digest=after.configuration_digest,
            execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
        )
        assert checks and all(c.folded and c.reexecuted for c in checks), [
            c for c in checks if not c.folded or not c.reexecuted
        ]
        final = await play.store.read(cid)
        assert {k: v for k, v in replayed.items() if k != "play_json"} == {
            k: v for k, v in final.items() if k != "play_json"
        }
        assert json.loads(replayed["play_json"]) == json.loads(final["play_json"])

    if selected == "dodge" and succeeds:
        # Reducer diagnostic: actual equipment transitions are observed at the
        # intermediate canonical settlement boundary. This is not an authored
        # one-command drop-and-retrieve host operation.
        unready = play.engine.resources.apply(
            after.resources,
            Equip(
                id="diagnostic-unready",
                actor_id="a",
                expected_revision=after.resources.revision,
                item_id="real-staff",
                ready=False,
            ),
        )
        held, held_encounter = reconcile_equipment(
            after.model_copy(update={"resources": unready}), after.encounters[0]
        )
        assert casts(held.resources)["death"].status == "held"
        assert (
            "real-staff"
            not in next(p for p in held_encounter.participants if p.actor_id == "a").ready_item_ids
        )
        assert ("real-staff", "right-hand") in next(
            a for a in held.actors if a.actor_id == "a"
        ).held_item_hands
        ready = play.engine.resources.apply(
            held.resources,
            Equip(
                id="diagnostic-ready",
                actor_id="a",
                expected_revision=held.resources.revision,
                item_id="real-staff",
            ),
        )
        held, _ = reconcile_equipment(held.model_copy(update={"resources": ready}), held_encounter)
        assert casts(held.resources)["death"].status == "held"
        lost = play.engine.resources.apply(
            after.resources,
            Unequip(
                id="diagnostic-lost-touch",
                actor_id="a",
                expected_revision=after.resources.revision,
                item_id="real-staff",
            ),
        )
        observed, encounter = reconcile_equipment(
            after.model_copy(update={"resources": lost}), after.encounters[0]
        )
        assert casts(observed.resources)["death"].status == "dissipated"
        recovered = play.engine.resources.apply(
            observed.resources,
            Equip(
                id="diagnostic-recovered",
                actor_id="a",
                expected_revision=observed.resources.revision,
                item_id="real-staff",
            ),
        )
        restored = observed.model_copy(update={"resources": recovered})
        encounter = bind_ready_hand(
            play.rules_context, restored, encounter, "a", "real-staff", "both"
        )
        restored, _ = reconcile_equipment(restored, encounter)
        assert next(i for i in restored.resources.items if i.id == "real-staff").ready
        assert casts(restored.resources)["death"].status == "dissipated"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_staff_rejects_current_diffuse_victim_before_attack_rng(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await _charged_encounter(tmp_path, backend, victim_mode="diffuse")
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="ordinary human"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="unsupported-victim",
                actor_id="a",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                maneuver="attack",
                item_id="real-staff",
                mode_id="staff-thrust",
                target_id="b",
            ),
            principal_id="a",
        )
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and stream == await play.store.stream(cid)
    assert casts(play._load(before).resources)["death"].status == "held"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_staff_lost_source_refuses_before_response_dice(
    tmp_path: Path, backend: str
) -> None:
    """Actual producer/pending host, followed by canonical custody reducers.

    The changed checkpoint is a reducer diagnostic, not a claim that a public
    drop-and-retrieve command is available while an attack awaits defense.
    """
    from wayfarer.engine.simulation.combat.melee.resolution import resolve_melee

    cid, play, _ = await _charged_encounter(tmp_path, backend)
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="pending-source",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="real-staff",
            mode_id="staff-thrust",
            target_id="b",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    lost = play.engine.resources.apply(
        state.resources,
        Unequip(
            id="source-loss",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="real-staff",
        ),
    )
    state, encounter = reconcile_equipment(
        state.model_copy(update={"resources": lost}), state.encounters[0]
    )
    assert casts(state.resources)["death"].status == "dissipated"
    recovered = play.engine.resources.apply(
        state.resources,
        Equip(
            id="source-recovered",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="real-staff",
        ),
    )
    state = state.model_copy(update={"resources": recovered})
    encounter = bind_ready_hand(play.rules_context, state, encounter, "a", "real-staff", "both")
    state, encounter = reconcile_equipment(state, encounter)
    state = state.model_copy(update={"encounters": (encounter,)})
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="held"):
        resolve_melee(play.rules_context, state, encounter, "none", None)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_legacy_armed_continuation_keeps_already_discharged_magic(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compatibility diagnostic, following the existing legacy-limb fixture.

    Fresh complete Staff contexts resolve this row directly. Only the old limb
    adapter's pause is simulated; manufacture, casting, defense, discharge and
    public migration/resumption all execute their real consumers.
    """
    import wayfarer.engine.simulation.combat.criticals.limbs as limbs
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.combat.critical import CriticalMiss
    from wayfarer.engine.simulation.combat.criticals.limbs import CriticalLimbResult
    from wayfarer.engine.simulation.combat.encounter import Encounter
    from wayfarer.engine.simulation.rules_context import RulesContext
    from wayfarer.orchestration.combat import ContinueCriticalMiss

    def old_limb_adapter(
        runtime: RulesContext,
        state: PlayState,
        encounter: Encounter,
        *,
        table: tuple[int, ...],
        defender_item: str | None,
        blocker: str,
        defender_mode_id: str | None = None,
    ) -> tuple[PlayState, Encounter, CriticalLimbResult]:
        return state, encounter, CriticalLimbResult.model_validate({"table_rolls": (table,)})

    cid, play, _ = await _charged_encounter(tmp_path, backend)
    service = CombatService(play)
    await service.execute(
        cid,
        TakeCombatTurn(
            id="legacy-contact",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="real-staff",
            mode_id="staff-thrust",
            target_id="b",
        ),
        principal_id="a",
    )
    with monkeypatch.context() as legacy:
        legacy.setattr(limbs, "resolve_limb", old_limb_adapter)
        play.rng = RecordedDice((3, 3, 3, 1, 1, 1, 5, 5, 5, 1, 1, 1))
        result = await service.execute(
            cid,
            ChooseDefense(
                id="legacy-parry",
                actor_id="b",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                defense="parry",
                item_id="left-hand",
            ),
            principal_id="b",
        )
    assert (
        result.injury is not None
        and result.injury.adjudication_required == "basic-critical-miss:15:attacker"
    )
    discharged = play._load(await play.store.read(cid))
    saved = CriticalMiss.model_validate_json(
        next(e.kind for e in discharged.resources.events if e.id.startswith("critical:"))
    )
    assert saved.table_total == 15 and casts(discharged.resources)["death"].status == "spent"
    magical_events = tuple(
        e for e in discharged.resources.events if e.id.startswith("melee-spell:result:")
    )
    assert len(magical_events) == 1
    victim_hp = next(p.current for p in discharged.resources.pools if p.id == "hp:b")
    play.rng = RecordedDice(())
    await service.execute(
        cid,
        ContinueCriticalMiss(
            id="legacy-migrate",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            critical_id=saved.id,
            stage="migrate",
        ),
        principal_id="gm",
    )
    resume = ContinueCriticalMiss(
        id="legacy-resume",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        critical_id=saved.id,
        stage="resume",
    )
    play.rng = RecordedDice((6,))
    resumed = await service.execute(cid, resume, principal_id="gm")
    assert play.rng.exhausted()
    final = play._load(await play.store.read(cid))
    assert final.encounters[0].blocked_reason is None
    assert next(p.current for p in final.resources.pools if p.id == "hp:b") == victim_hp
    assert (
        tuple(e for e in final.resources.events if e.id.startswith("melee-spell:result:"))
        == magical_events
    )
    assert casts(final.resources)["death"].status == "spent"
    play.rng = RecordedDice(())
    assert await service.execute(cid, resume, principal_id="gm") == resumed
