"""B426 numeric oracles and canonical opt-in native combat; no campaign certificate."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal, Never, cast

import pytest
from support.runtime import build_play, seed_play
from test_actions import campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.adventures import roadwatch, roadwatch_rules
from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import CampaignRules, PackagePin, RulesCatalog
from wayfarer.engine.rules.profiles import DEFAULT_REGISTRY
from wayfarer.engine.rules.types.battle_fatigue import BattleFatiguePolicy
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    EndEncounter,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.battle_fatigue import read, write
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.combat.battle_fatigue import preflight
from wayfarer.orchestration.combat.context import CombatContext
from wayfarer.orchestration.combat.steps import reduce_combat
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


def selected_engine(*, enabled: bool = True) -> ActionEngine:
    """Actual canonical suppliers with an exact new policy-bearing package pin."""
    base = roadwatch.engine()
    package = replace(
        roadwatch_rules.PACKAGE,
        version="1.0.1",
        battle_fatigue=BattleFatiguePolicy() if enabled else None,
    )
    rules = CampaignRules(
        package.edition,
        (PackagePin(package.id, package.version, package.digest),),
        roadwatch_rules.POLICY.id,
        roadwatch_rules.POLICY.version,
    )
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog, rules, roadwatch_rules.POLICY, statistics_profile=roadwatch_rules.PROFILE_ID
    )
    reviewer = PowerReviewer(compiler, base.reviewer.policy, base.reviewer.gm_ids)
    resources = ResourceEngine(
        roadwatch.world(),
        catalog,
        rules,
        roadwatch_rules.POLICY,
        roadwatch_rules.EQUIPMENT.bind((package,)),
    )
    actions = base.rules.model_copy(
        update={
            "checks": tuple(
                c.model_copy(update={"package_version": package.version}) for c in base.rules.checks
            )
        }
    )
    return ActionEngine(reviewer, resources, actions)


def test_opt_in_metadata_changes_only_new_package_pin() -> None:
    live = roadwatch_rules.PACKAGE
    assert live.version == "1.1.0" and live.battle_fatigue == BattleFatiguePolicy()
    package = replace(live, battle_fatigue=None)
    assert "battle_fatigue" not in json.loads(package.canonical_json())
    selected = replace(package, battle_fatigue=BattleFatiguePolicy())
    assert json.loads(selected.canonical_json())["battle_fatigue"] == {
        "generation": 1,
        "maximum_seconds": 119,
        "climate": "temperate",
    }
    assert selected.digest != package.digest
    assert selected.digest == live.digest
    historical = replace(package, version="1.0.0")
    assert historical.digest == "515f50869f1e7794ae48ebf63cfd61e5a5dca51cd48e8412cd4f00cc7f580d00"
    basic = DEFAULT_REGISTRY.get("profile:gurps-basic-set-4e-2004", 11)
    assert basic.digest == "483b2f942994f1ca2f0e23e8825e1cfd172fc9bddac5e9bf1aab3ec86f120a0d"


def test_unsupported_policy_cannot_be_pinned() -> None:
    with pytest.raises(ValidationError):
        BattleFatiguePolicy(generation=cast(Literal[1], True))
    with pytest.raises(ValidationError):
        BattleFatiguePolicy(maximum_seconds=120)
    with pytest.raises(ValidationError):
        BattleFatiguePolicy(climate=cast(Literal["temperate"], "hot"))


async def setup(
    tmp_path: Path, backend: str, *, seed: int = 1
) -> tuple[str, PlayService, CombatService]:
    engine = selected_engine()
    play = build_play(tmp_path, engine, backend=backend, seeds=lambda: format(seed, "064x"))
    initial = campaign(engine)
    graph = roadwatch.adventure()
    # This is a native component activation fixture, not the full public story.
    world = replace(
        graph.world,
        entities=tuple(
            replace(e, location_id="road") if e.id in ("a", "b") else e
            for e in graph.world.entities
        ),
    )
    await seed_play(play, initial, world, graph.resources, graph.actors)
    cid = initial["id"]
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id=roadwatch.BATTLEFIELD_ID,
            placements=(
                Placement(actor_id="a", position=Hex(q=2, r=0), hex_facing=0),
                Placement(actor_id="b", position=Hex(q=0, r=1), hex_facing=0),
                Placement(actor_id="brigand", position=Hex(q=3, r=0), hex_facing=3),
            ),
        ),
        principal_id="gm",
    )
    return cid, play, service


async def state(play: PlayService, cid: str) -> PlayState:
    return play._load(await play.store.read(cid))


async def turn(play: PlayService, service: CombatService, cid: str) -> None:
    current = await state(play, cid)
    encounter = current.encounters[0]
    actor = encounter.current_actor_id
    await service.execute(
        cid,
        TakeCombatTurn(
            id=f"turn-{current.revision}",
            actor_id=actor,
            expected_revision=current.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id=actor,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "seconds,expected,partial,active_defense",
    [
        (10, 0, False, False),
        (11, 1, False, False),
        (9, 0, True, False),
        (10, 1, True, False),
        (11, 1, False, True),
    ],
)
async def test_real_roll_then_source_boundary_cost_at_end_once(
    tmp_path: Path, backend: str, seconds: int, expected: int, partial: bool, active_defense: bool
) -> None:
    cid, play, service = await setup(tmp_path, backend)
    current = await state(play, cid)
    assert read(current.resources, "fight").seconds == 0
    # A real attack's result is resolved by actual native seed dice, not injected trace.
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=current.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="brigand",
        ),
        principal_id="a",
    )
    current = await state(play, cid)
    if current.encounters[0].pending_defense is not None:
        await service.execute(
            cid,
            ChooseDefense(
                id="defend",
                actor_id="brigand",
                expected_revision=current.revision,
                encounter_id="fight",
                defense="dodge" if active_defense else "none",
            ),
            principal_id="brigand",
        )
    current = await state(play, cid)
    assert read(current.resources, "fight").rolled == (
        ("a", "brigand") if active_defense else ("a",)
    )
    while read(current.resources, "fight").seconds < seconds:
        await turn(play, service, cid)
        current = await state(play, cid)
    assert next(p.current for p in current.resources.pools if p.id == "fp:a") == 11
    if partial:
        await turn(play, service, cid)
        current = await state(play, cid)
        assert (current.encounters[0].round, current.encounters[0].turn_index) == (seconds + 1, 1)
    before_end_time = current.resources.game_time
    command = EndEncounter(
        id="end",
        actor_id="gm",
        expected_revision=current.revision,
        encounter_id="fight",
        reason="test",
    )
    result = await service.execute(cid, command, principal_id="gm")
    after = await state(play, cid)
    assert read(after.resources, "fight").seconds == seconds + int(partial)
    assert read(after.resources, "fight").charged
    assert after.resources.game_time == before_end_time
    assert {p.id: p.current for p in after.resources.pools if p.id.startswith("fp:")} == {
        "fp:a": 11 - expected,
        "fp:b": 11,
        "fp:brigand": 10 - int(active_defense) * expected,
    }
    assert after.resources.revision == after.revision
    assert await service.execute(cid, command, principal_id="gm") == result
    assert await state(play, cid) == after
    assert await play.store.replay(cid) == await play.store.read(cid)
    if seconds == 11 or partial:
        history = await play.store.history(cid)
        commands = history[1:]
        events = [e for e in await play.store.stream(cid) if e.command_id != history[0].command_id]

        def refuse_fresh() -> Never:
            raise AssertionError("Official seed replay must not draw fresh entropy or time")

        executor = FixtureExecutor(
            play.engine, tmp_path / "official-replay", seeds=refuse_fresh, instants=refuse_fresh
        )
        final, checks = await verify_commands(
            history[0].state_after,
            commands,
            events,
            configuration_digest=after.configuration_digest,
            execute=executor,
        )
        assert all(row.folded and row.reexecuted and row.reason is None for row in checks)
        assert len(checks) == len(commands)
        assert final == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_missing_or_corrupt_ledger_refuses_before_any_dice(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await setup(tmp_path, backend)
    current = await state(play, cid)
    events = current.resources.events
    ledger_event = next(e for e in events if e.id.startswith("battle-fatigue:"))
    command = TakeCombatTurn(
        id="never-roll",
        actor_id="a",
        expected_revision=current.revision,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        mode_id="swing",
        target_id="brigand",
    )
    for altered in (
        tuple(e for e in events if e is not ledger_event),
        tuple(e.model_copy(update={"kind": "{}"}) if e is ledger_event else e for e in events),
        tuple(
            e.model_copy(
                update={"kind": ledger_event.kind.replace('"seconds":0', '"seconds":true')}
            )
            if e is ledger_event
            else e
            for e in events
        ),
    ):
        invalid = current.model_copy(
            update={"resources": current.resources.model_copy(update={"events": altered})}
        )
        with pytest.raises(ValidationError):
            preflight(invalid, command, CombatContext(play, invalid))
    assert await state(play, cid) == current


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "seed,total,defense,rolled",
    [
        (1, 9, "dodge", ("a", "brigand")),
        (35, 15, "dodge", ("a",)),
        (270, 3, "dodge", ("a",)),
        (1275, 18, "none", ("a",)),
    ],
)
async def test_actual_melee_trace_participation_including_criticals(
    tmp_path: Path,
    backend: str,
    seed: int,
    total: int,
    defense: Literal["none", "dodge"],
    rolled: tuple[str, ...],
) -> None:
    cid, play, service = await setup(tmp_path, backend, seed=seed)
    current = await state(play, cid)
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=current.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="brigand",
        ),
        principal_id="a",
    )
    current = await state(play, cid)
    result = await service.execute(
        cid,
        ChooseDefense(
            id="defend",
            actor_id="brigand",
            expected_revision=current.revision,
            encounter_id="fight",
            defense=defense,
        ),
        principal_id="brigand",
    )
    current = await state(play, cid)
    assert result.injury is not None
    assert result.injury.attack.total == total
    assert (result.injury.defense is not None) == (total == 9)
    assert read(current.resources, "fight").rolled == rolled


@pytest.mark.parametrize("round_,index,seconds", [(120, 0, 119), (119, 1, 118)])
async def test_duration_cap_refuses_new_turn_but_permits_zero_time_gm_end(
    tmp_path: Path, round_: int, index: int, seconds: int
) -> None:
    cid, play, _ = await setup(tmp_path, "sqlite")
    original = await state(play, cid)
    # Pure transition boundary fixture, not evidence that 119 seconds were played.
    encounter = original.encounters[0].model_copy(update={"round": round_, "turn_index": index})
    ledger = read(original.resources, "fight").model_copy(
        update={"round": round_, "index": index, "seconds": seconds}
    )
    current = original.model_copy(
        update={"encounters": (encounter,), "resources": write(original.resources, ledger)}
    )
    context = CombatContext(play, current)
    if seconds == 119:
        with pytest.raises(ValidationError, match="End this battle"):
            reduce_combat(
                current,
                TakeCombatTurn(
                    id="too-long",
                    actor_id="a",
                    expected_revision=current.revision,
                    encounter_id="fight",
                    maneuver="do_nothing",
                ),
                context,
            )
    ended, _ = reduce_combat(
        current,
        EndEncounter(
            id="safe-end",
            actor_id="gm",
            expected_revision=current.revision,
            encounter_id="fight",
            reason="scope boundary",
        ),
        context,
    )
    assert read(ended.resources, "fight").seconds == 119
    assert read(ended.resources, "fight").charged
    assert next(p.current for p in ended.resources.pools if p.id == "fp:a") == 11
    if seconds == 119:
        # An impossible-to-admit open120th cycle cannot silently receive the
        # ordinary119second disposition if a malformed checkpoint reaches end.
        beyond = current.model_copy(
            update={
                "encounters": (encounter.model_copy(update={"turn_index": 1}),),
                "resources": write(current.resources, ledger.model_copy(update={"index": 1})),
            }
        )
        with pytest.raises(ValidationError, match="supported ordinary battle duration"):
            reduce_combat(
                beyond,
                EndEncounter(
                    id="outside-end",
                    actor_id="gm",
                    expected_revision=beyond.revision,
                    encounter_id="fight",
                    reason="unsupported120th second",
                ),
                CombatContext(play, beyond),
            )


async def test_immediate_gm_end_does_not_manufacture_a_battle_second(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path, "sqlite")
    current = await state(play, cid)
    await service.execute(
        cid,
        EndEncounter(
            id="immediate-end",
            actor_id="gm",
            expected_revision=current.revision,
            encounter_id="fight",
            reason="no turns",
        ),
        principal_id="gm",
    )
    after = await state(play, cid)
    ledger = read(after.resources, "fight")
    assert ledger.seconds == 0 and ledger.rolled == () and ledger.charged
    assert after.resources.game_time == 0
    assert all(p.current == p.maximum for p in after.resources.pools if p.id.startswith("fp:"))
