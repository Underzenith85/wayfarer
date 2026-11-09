"""B412 inspected fixed-scope timing through actual Basic catalog equipment.

The +4 threshold is the existing inspected timing rule applied to the published
heavy-blaster's actual scope, not a new claim of selected-PDF inspection.
"""

import json
import os
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, open_store, played, seed_play
from test_actions import campaign
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.skills.mundane.ranged import definitions
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import CombatResult, RangedSituation
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.turn_commitment import _carried_state
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands

BASIC: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
FEATURE = "scope-aim-elapsed"
ENTRIES = {entry.definition_id: entry for entry in BASIC_EQUIPMENT.entries}


async def native(path: Path, backend: str) -> tuple[str, PlayService, Campaign]:
    if backend == "postgres":
        assert os.environ.get("WAYFARER_TEST_DATABASE_URL"), "PostgreSQL proof cannot skip"
    weapon, cell = ENTRIES["equipment:heavy-blaster"], ENTRIES["equipment:heavy-blaster-cell"]
    mode = weapon.modes[0]
    assert isinstance(mode, RangedMode) and (mode.accuracy, mode.scope_bonus) == (10, 4)
    old_id, template = await setup(
        path / "template",
        BASIC,
        human=True,
        start_encounter=False,
        extra_equipment=(weapon, cell),
        preserve_extra_equipment=True,
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id=mode.skill_id, amount=4),),
        campaign_technology_level=11,
    )
    source = template._load(await template.store.read(old_id))
    play = build_play(
        path / "native", template.engine, store=open_store(path / "native", backend=backend)
    )
    resources = source.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"equipped": False, "ready": False})
                if item.id == "sword-a"
                else item
                for item in source.resources.items
            )
            + (
                Item(
                    id="blaster",
                    definition_id=weapon.definition_id,
                    owner_id="a",
                    ready=True,
                    equipped=True,
                    authorized_actor_ids=("a",),
                ),
                Item(
                    id="cell",
                    definition_id=cell.definition_id,
                    owner_id="a",
                    charges=cell.power_cell_capacity,
                ),
            )
        }
    )
    actors = tuple(
        ActorSetup(
            actor_id=actor.actor_id,
            proposal=actor.proposal,
            body=actor.body,
            aware_of=("a", "b"),
            held_item_hands=(("blaster", "right-hand"), ("blaster", "left-hand"))
            if actor.actor_id == "a"
            else actor.held_item_hands,
        )
        for actor in source.actors
    )
    initial = campaign(play.engine)
    await seed_play(play, initial, source.world, resources, actors, members=source.members)
    genesis = await play.store.read(initial["id"])
    assert play.engine.combat is not None
    await CombatService(play).execute(
        initial["id"],
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id=next(iter(play.engine.combat.battlefields)),
            ranged_situations=(
                RangedSituation(attacker_id="a", defender_id="b", distance_yards=1),
            ),
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    return initial["id"], play, genesis


async def aim(cid: str, play: PlayService) -> tuple[TakeCombatTurn, CombatResult]:
    revision = (await play.store.read(cid))["revision"]
    command = TakeCombatTurn(
        id=f"aim-{revision}",
        actor_id="a",
        expected_revision=revision,
        encounter_id="fight",
        maneuver="aim",
        item_id="blaster",
        target_id="b",
        mode_id="beam",
    )
    return command, await CombatService(play).execute(cid, command, principal_id="a")


def commitment(play: PlayService, snapshot: Campaign) -> ManeuverState:
    return play._load(snapshot).encounters[0].participants[0].maneuver_state


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_fixed_four_scope_reaches_threshold_without_uncapping_extra_aim(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, initial = await native(tmp_path, backend)
    mode = ENTRIES["equipment:heavy-blaster"].modes[0]
    assert isinstance(mode, RangedMode)
    for _ in range(mode.reload_seconds):
        await turn(
            cid, play, "a", "ready", item_id="blaster", mode_id="beam", reload_ammunition_id="cell"
        )
        await turn(cid, play, "b", "do_nothing")
    draws: list[int] = []
    actual = SeededRandom.randbelow

    def observed(rng: SeededRandom, bound: int, /) -> int:
        draws.append(bound)
        return actual(rng, bound)

    monkeypatch.setattr(SeededRandom, "randbelow", observed)
    accepted: list[tuple[TakeCombatTurn, CombatResult]] = []
    for elapsed, expected in enumerate((10, 11, 12, 16, 16), start=1):
        accepted.append(await aim(cid, play))
        state = commitment(play, await play.store.read(cid))
        assert (state.aim_elapsed_seconds, state.aim_seconds, state.aim_bonus) == (
            elapsed,
            min(elapsed, 3),
            expected,
        )
        await turn(cid, play, "b", "do_nothing")
    assert draws == []
    await turn(cid, play, "a", "attack", item_id="blaster", target_id="b", mode_id="beam", shots=1)
    result = await defend(cid, play, "b")
    assert result.injury is not None
    # Purchased DX/E Beam Weapons(Rifle)4CP =>12, actual Acc10 + extra2 + scope4.
    assert result.injury.attack.effective_target == 28
    assert draws
    final = await play.store.read(cid)
    state = commitment(play, final)
    assert state.aim_seconds == state.aim_elapsed_seconds == 0
    value, outcome = accepted[0]
    saved_input = await play.store.command_input(cid, value.id)
    assert saved_input is not None
    assert saved_input.text is not None
    payload = replay_payload(saved_input.text)
    assert isinstance(payload, dict) and FEATURE in payload["combat_protocol_features"]
    history, stream = await play.store.history(cid), await play.store.stream(cid)

    def forbidden(*_: object) -> str:
        raise AssertionError("Accepted retry requested a fresh seed")

    restarted = build_play(tmp_path / "restart", play.engine, store=play.store, seeds=forbidden)
    assert await CombatService(restarted).execute(cid, value, principal_id="a") == outcome
    with pytest.raises(ConflictError):
        await CombatService(restarted).execute(
            cid, value.model_copy(update={"target_id": "a"}), principal_id="a"
        )
    assert await play.store.read(cid) == final
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, value.id) == saved_input
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        stream,
        configuration_digest=play._load(final).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "original-seed"),
    )
    assert all(check.folded and check.reexecuted for check in checks)
    assert len(checks) == 2 * mode.reload_seconds + 13
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
    assert await play.store.replay(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("refusal", ["actor", "unready", "mode"])
async def test_invalid_native_aim_refuses_without_mutation_or_dice(
    tmp_path: Path, backend: str, refusal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, _ = await native(tmp_path, backend)
    before = await play.store.read(cid)
    command = TakeCombatTurn(
        id="refused-aim",
        actor_id="a",
        expected_revision=before["revision"],
        encounter_id="fight",
        maneuver="aim",
        item_id="blaster",
        mode_id="beam",
        target_id="b",
    )
    principal = "b" if refusal == "actor" else "a"
    if refusal == "unready":
        command = command.model_copy(update={"item_id": "sword-a"})
    if refusal == "mode":
        command = command.model_copy(update={"mode_id": "invented"})
    history, stream = await play.store.history(cid), await play.store.stream(cid)

    seeds: list[str] = []

    def capture() -> str:
        assert refusal != "actor", "Authorization refusal must precede seed capture"
        seeds.append("captured")
        return format(3, "064x")

    def no_dice(*_: object) -> int:
        raise AssertionError("Rejected Aim rolled dice")

    monkeypatch.setattr(SeededRandom, "randbelow", no_dice)
    play.seeds = capture
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, command, principal_id=principal)
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, command.id) is None
    assert len(seeds) == (0 if refusal == "actor" else 1)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_other_maneuver_resets_elapsed_aim(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await native(tmp_path, backend)
    for _ in range(4):
        await aim(cid, play)
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "do_nothing")
    assert commitment(play, await play.store.read(cid)).aim_elapsed_seconds == 0
    await turn(cid, play, "b", "do_nothing")
    await aim(cid, play)
    state = commitment(play, await play.store.read(cid))
    assert (state.aim_seconds, state.aim_elapsed_seconds, state.aim_sight_bonus) == (1, 1, 0)


def test_new_internal_elapsed_default_preserves_old_json() -> None:
    state = ManeuverState()
    assert "aim_elapsed_seconds" not in state.model_dump_json()
    old = json.loads(state.model_dump_json())
    assert (
        ManeuverState.model_validate_json(json.dumps(old)).model_dump_json()
        == state.model_dump_json()
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_active_defense_discards_elapsed_and_legacy_carry_ignores_it(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await native(tmp_path, backend)
    for elapsed in range(4):
        await aim(cid, play)
        if elapsed < 3:
            await turn(cid, play, "b", "do_nothing")
    actor = play._load(await play.store.read(cid)).encounters[0].participants[0]
    assert actor.maneuver_state.aim_elapsed_seconds == 4
    with combat_generation(frozenset()):
        assert _carried_state(actor, "attack").aim_elapsed_seconds == 0
    with combat_generation(frozenset({FEATURE})):
        assert _carried_state(actor, "attack").aim_elapsed_seconds == 4
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    await defend(cid, play, "a", "dodge")
    state = commitment(play, await play.store.read(cid))
    assert state.aim_elapsed_seconds == state.aim_seconds == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_legacy_followup_discards_new_elapsed_and_adopts_only_known_duration(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.orchestration.combat import generations

    cid, play, _ = await native(tmp_path, backend)
    for _ in range(4):
        await aim(cid, play)
        await turn(cid, play, "b", "do_nothing")
    assert commitment(play, await play.store.read(cid)).aim_elapsed_seconds == 4
    active = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", active - {FEATURE})
    await aim(cid, play)
    legacy = commitment(play, await play.store.read(cid))
    assert (legacy.aim_seconds, legacy.aim_elapsed_seconds, legacy.aim_sight_bonus) == (3, 0, 0)
    await turn(cid, play, "b", "do_nothing")
    monkeypatch.setattr(generations, "ACTIVE", active)
    await aim(cid, play)
    adopted = commitment(play, await play.store.read(cid))
    # Historical duration beyond three is unavailable; adopt the known minimum.
    assert (adopted.aim_seconds, adopted.aim_elapsed_seconds, adopted.aim_sight_bonus) == (3, 4, 4)
