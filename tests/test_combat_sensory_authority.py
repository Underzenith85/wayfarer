"""B394 authority is directed, durable, private, and independent of map placement."""

import asyncio
import json
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal

import pytest
from support.runtime import build_play, played, seed_campaign
from test_basic_combat import start_basic
from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm
from test_gurps_melee import setup

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.rules.types.symptoms import SymptomDebt, SymptomEffect, SymptomSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import move_basic
from wayfarer.engine.simulation.combat.sensory_host import (
    ADAPTER,
    DeclareCombatSense,
    HearCombatTarget,
    RevokeCombatSense,
)
from wayfarer.engine.simulation.combat.sensory_state import (
    ExactLocation,
    NonvisualObservation,
    checkpoint,
    evidence,
    history,
    invalidations,
)
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    Placement,
    SquareSpatialContext,
)
from wayfarer.engine.simulation.events import ResourceChanged, visible
from wayfarer.engine.simulation.health.fright_state import TimedFright
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.combat_senses import CombatSensesService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded

PROFILE: Final = "gurps-basic-set-4e-2004"
Context = Literal["basic", "square", "hex"]


async def prepare(path: Path, backend: str, context: Context = "square") -> tuple[str, PlayService]:
    placements = (
        (
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        )
        if context == "hex"
        else None
    )
    board = (
        HexBattlefield(
            id="dock",
            coordinate_system="hex-axial-v1",
            profile_id=PROFILE,
            baseline_id=BASELINE_ID,
            location_id="dock",
            cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(4) for r in range(3)),
        )
        if context == "hex"
        else None
    )
    cid, original = await setup(
        path / "source",
        PROFILE,
        physical_purchases=(Purchase(definition_id="trait:acute-hearing", amount=3),),
        scene_bound=context == "basic",
        start_encounter=context != "basic",
        placements=placements,
        battlefield=board,
    )
    if context == "basic":
        await CombatService(original).execute(cid, start_basic(1), principal_id="gm")
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice((2, 2, 2)))
    await seed_campaign(play.store, await original.store.read(cid))
    return cid, play


def hearing(revision: int = 1, identifier: str = "hear") -> HearCombatTarget:
    return HearCombatTarget(
        id=identifier,
        actor_id="b",
        target_id="a",
        encounter_id="fight",
        expected_revision=revision,
        cue="The opponent's boot scrapes the gravel",
    )


def declaration(
    revision: int = 1,
    identifier: str = "declare",
    *,
    located: bool = True,
    exact: bool = False,
) -> DeclareCombatSense:
    return DeclareCombatSense(
        id=identifier,
        actor_id="b",
        target_id="a",
        encounter_id="fight",
        expected_revision=revision,
        observation=NonvisualObservation(
            basis="touch",
            explanation="The observer feels the opponent pressing against their arm",
            located=located,
            attack_awareness="Contact warns the observer of the incoming attack",
            exact_location=ExactLocation(
                basis="continuous-contact",
                explanation="Continuous contact fixes the opponent's location",
            )
            if exact
            else None,
        ),
    )


async def change(play: PlayService, cid: str, transform: Callable[[PlayState], PlayState]) -> None:
    before = play._load(await play.store.read(cid))

    def reduce(campaign: Campaign) -> CommandReceipt:
        current = play._load(campaign)
        updated = transform(current)
        updated = updated.model_copy(
            update={
                "revision": current.revision + 1,
                "resources": updated.resources.model_copy(
                    update={"revision": current.revision + 1}
                ),
            }
        )
        play.commit(campaign, checkpoint(updated, before=current))
        return CommandReceipt(action="resource", outcome="fixture-transition")

    await play.store.commit_turn(
        cid,
        f"fixture-{before.revision}",
        before.revision,
        f"fixture-{before.revision}",
        reduce,
        actor_id="gm",
    )


def move(state: PlayState, actor: str, *, back: bool = False) -> PlayState:
    encounter = state.encounters[0]
    spatial = encounter.spatial
    if isinstance(spatial, BasicSpatialContext):
        encounter = move_basic(
            encounter,
            actor_id=actor,
            reference_actor_id="a" if actor == "b" else "b",
            direction="approach" if back else "withdraw",
            yards=1,
            command_id=f"move-{state.revision}",
            revision=state.revision + 1,
            require_obstacle=False,
        )
    else:
        origin = (0 if actor == "a" else 1) if back else 2
        destination: GridPoint | Hex
        if isinstance(spatial, SquareSpatialContext):
            destination = GridPoint(x=origin, y=0)
        else:
            destination = Hex(q=origin, r=0)
        spatial = spatial.model_copy(
            update={
                "placements": tuple(
                    p.model_copy(update={"position": destination}) if p.actor_id == actor else p
                    for p in spatial.placements
                )
            }
        )
        encounter = encounter.model_copy(
            update={
                "spatial_context": spatial,
                "participants": tuple(
                    p.model_copy(update={"position": destination}) if p.actor_id == actor else p
                    for p in encounter.participants
                ),
            }
        )
    return state.model_copy(update={"encounters": (encounter,)})


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "square", "hex"])
async def test_bound_hearing_uses_approved_traits_and_preserves_private_roll(
    tmp_path: Path,
    backend: str,
    context: Context,
) -> None:
    cid, play = await prepare(tmp_path, backend, context)
    before = play._load(await play.store.read(cid))
    assert evidence(before, before.encounters[0], "b", "a") is None
    service = CombatSensesService(play)
    result = await service.execute(cid, hearing(), principal_id="gm")
    assert result is not None and result.located and not result.aware_of_attack
    assert result.exact_location is None and result.hearing is not None
    assert result.hearing.base_target == 10 and result.hearing.effective_target == 11
    assert result.hearing.dice == (2, 2, 2)
    assert [m.value for m in result.hearing.modifiers] == [-2, 3]
    assert (result.encounter_id, result.observer_id, result.target_id) == ("fight", "b", "a")
    stored = play._load(await play.store.read(cid))
    restarted = PlayState.model_validate_json(stored.model_dump_json())
    assert evidence(restarted, restarted.encounters[0], "b", "a") == result
    assert evidence(restarted, restarted.encounters[0], "a", "b") is None
    assert (
        evidence(restarted, restarted.encounters[0].model_copy(update={"id": "foreign"}), "b", "a")
        is None
    )
    facts = [
        e.event
        for e in await play.store.stream(cid)
        if isinstance(e.event, ResourceChanged) and e.event.fact.id == result.id
    ]
    assert len(facts) == 1
    assert not visible(facts[0], CampaignMember(principal_id="b", role="player", actor_ids=("b",)))
    assert not visible(facts[0], CampaignMember(principal_id="watcher", role="spectator"))
    assert visible(facts[0], CampaignMember(principal_id="gm", role="gm"))
    assert await service.execute(cid, hearing(), principal_id="gm") == result
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "square", "hex"])
@pytest.mark.parametrize("actor", ["a", "b"])
async def test_move_away_and_return_never_resurrects_proof(
    tmp_path: Path,
    backend: str,
    context: Context,
    actor: str,
) -> None:
    cid, play = await prepare(tmp_path, backend, context)
    result = await CombatSensesService(play).execute(
        cid, declaration(exact=True), principal_id="gm"
    )
    assert result and result.exact_location and result.aware_of_attack
    current = play._load(await play.store.read(cid))
    raw_moved = move(current, actor)
    assert evidence(raw_moved, raw_moved.encounters[0], "b", "a") is None
    await change(play, cid, lambda s: move(s, actor))
    await change(play, cid, lambda s: move(s, actor, back=True))
    returned = play._load(await play.store.read(cid))
    assert evidence(returned, returned.encounters[0], "b", "a") is None
    assert invalidations(returned.resources)[0].reason == "scope-changed"
    assert history(returned.resources)[0] == result
    assert (
        await CombatSensesService(play).execute(cid, declaration(exact=True), principal_id="gm")
        == result
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authority_scope_payload_cas_and_failed_hearing(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend)
    service = CombatSensesService(play)
    initial = await play.store.read(cid)
    for principal in ("a", "b"):
        with pytest.raises(ValidationError, match="authority"):
            await service.execute(cid, hearing(), principal_id=principal)
    for changes in ({"target_id": "unknown"}, {"target_id": "b"}, {"encounter_id": "old"}):
        with pytest.raises(ValidationError, match="encounter.*pair"):
            await service.execute(cid, hearing().model_copy(update=changes), principal_id="gm")
    with pytest.raises(ValidationError, match="Invalid combat"):
        await service.execute(cid, hearing().model_dump() | {"modifier": 100}, principal_id="gm")
    assert await play.store.read(cid) == initial
    result = await service.execute(cid, declaration(located=False), principal_id="gm")
    assert result and result.aware_of_attack and not result.located
    with pytest.raises(ConflictError):
        await service.execute(cid, declaration(exact=True), principal_id="gm")
    with pytest.raises(ConflictError):
        await service.execute(cid, hearing(), principal_id="gm")
    assert len(history(play._load(await play.store.read(cid)).resources)) == 1
    play.rng = RecordedDice((6, 6, 6))
    failed = await CombatSensesService(play).execute(cid, hearing(2), principal_id="gm")
    assert failed and not failed.located and failed.aware_of_attack
    assert failed.awareness_source_id == result.id
    assert failed.hearing and failed.hearing.outcome == "critical-failure"
    state = play._load(await play.store.read(cid))
    assert evidence(state, state.encounters[0], "b", "a") == failed
    assert invalidations(state.resources)[0].reason == "superseded"
    await CombatSensesService(play).execute(
        cid,
        RevokeCombatSense(
            id="revoke-sense",
            actor_id="b",
            target_id="a",
            encounter_id="fight",
            expected_revision=3,
            explanation="The sound has stopped",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert evidence(state, state.encounters[0], "b", "a") is None


def symptoms(state: PlayState, *, active: bool = True, extra: bool = False) -> PlayState:
    effects = tuple(
        SymptomEffect(
            id=f"blind-{i}",
            actor_id="b",
            pool_id="hp:b",
            source_id=f"source-{i}",
            spec=SymptomSpec(kind="blindness"),
            active=active if i == 0 else True,
        )
        for i in range(2 if extra else 1)
    )
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "symptom_effects": effects,
                    "symptom_debts": tuple(
                        SymptomDebt(
                            id=f"debt-{i}", pool_id="hp:b", source_id=f"source-{i}", remaining=6
                        )
                        for i in range(2 if extra else 1)
                    ),
                }
            )
        }
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_recovery_build_encounter_and_migration_invalidate(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, lambda s: symptoms(s, extra=True))
    result = await CombatSensesService(play).execute(cid, declaration(2), principal_id="gm")
    current = play._load(await play.store.read(cid))
    # Recovering only one causal blindness source leaves sensory scope unchanged.
    partial = symptoms(current, active=False, extra=True)
    assert evidence(partial, partial.encounters[0], "b", "a") == result
    recovered = symptoms(current, active=False)
    assert evidence(recovered, recovered.encounters[0], "b", "a") is None
    healed = checkpoint(recovered, before=current)
    blinded_again = symptoms(healed)
    assert evidence(blinded_again, blinded_again.encounters[0], "b", "a") is None
    # A different approved-build identity cannot reuse knowledge from a previous body.
    changed_actor = current.actors[1].model_copy(update={"approval": None})
    rebuilt = current.model_copy(update={"actors": (current.actors[0], changed_actor)})
    assert evidence(rebuilt, rebuilt.encounters[0], "b", "a") is None
    ended = current.encounters[0].model_copy(update={"status": "completed"})
    assert evidence(current.model_copy(update={"encounters": (ended,)}), ended, "b", "a") is None
    migrated = current.encounters[0].model_copy(update={"spatial_context": BasicSpatialContext()})
    assert (
        evidence(current.model_copy(update={"encounters": (migrated,)}), migrated, "b", "a") is None
    )
    await change(play, cid, lambda s: symptoms(s, active=False))
    assert await CombatSensesService(play).execute(cid, declaration(2), principal_id="gm") == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_hearing_uses_projected_per_and_live_condition_penalty(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)

    def debuff(state: PlayState) -> PlayState:
        effect = SymptomEffect(
            id="iq",
            actor_id="b",
            pool_id="hp:b",
            source_id="source",
            spec=SymptomSpec(kind="attribute-penalty", attribute="iq", level=2),
            active=True,
        )
        retching = TimedFright(
            id="retching",
            actor_id="b",
            trigger_id="horror",
            effect=FrightEffect(table_total=17, condition="retching"),
            started=0,
            due=100,
            active=True,
            recovery_target=10,
        )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "symptom_effects": (effect,),
                        "symptom_debts": (
                            SymptomDebt(id="debt", pool_id="hp:b", source_id="source", remaining=6),
                        ),
                        "events": state.resources.events
                        + (
                            ResourceEvent(
                                id="fright-runtime:retching",
                                at=0,
                                target_id="b",
                                kind=retching.model_dump_json(),
                            ),
                        ),
                    }
                )
            }
        )

    await change(play, cid, debuff)
    result = await CombatSensesService(play).execute(cid, hearing(2), principal_id="gm")
    assert result and result.hearing
    assert result.hearing.base_target == 8
    assert result.hearing.effective_target == 4  # Per8, Acute3, locate−2, retching−5.
    assert not result.located


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_concurrent_same_command_returns_winner_and_stale_new_command_loses(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play = await prepare(tmp_path, backend)
    other = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice((3, 3, 3)))
    a, b = await asyncio.gather(
        CombatSensesService(play).execute(cid, hearing(), principal_id="gm"),
        CombatSensesService(other).execute(cid, hearing(), principal_id="gm"),
    )
    assert a == b
    assert len(history(play._load(await play.store.read(cid)).resources)) == 1
    assert len(await played(play.store, cid)) == 1
    with pytest.raises(ConflictError):
        await CombatSensesService(other).execute(
            cid, hearing(identifier="stale-new"), principal_id="gm"
        )
    assert len(await played(play.store, cid)) == 1


@pytest.mark.parametrize("retry", [False, True])
async def test_gm_revocation_between_authorization_and_commit_or_retry(
    tmp_path: Path,
    retry: bool,
) -> None:
    cid, original = await prepare(tmp_path, "sqlite")
    if retry:
        await CombatSensesService(original).execute(cid, hearing(), principal_id="gm")
    revision = 2 if retry else 1
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    play = build_play(tmp_path, original.engine, store=store, rng=RecordedDice(()))
    store.revoke = lambda: revoke_gm(original, cid, revision)
    command = hearing() if retry else hearing(revision + 1)
    with pytest.raises(ValidationError, match="director authority"):
        await CombatSensesService(play).execute(cid, command, principal_id="gm")
    assert len(history(original._load(await original.store.read(cid)).resources)) == int(retry)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_saved_plan_rechecks_current_gm_and_seeded_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    play.rng = secrets
    before = await play.store.read(cid)
    result = await CombatSensesService(play).execute(cid, hearing(), principal_id="gm")
    records = await played(play.store, cid)
    record = records[0]
    assert record.reexecutable and record.entropy_seed
    assert record.command_input
    payload = json.loads(record.command_input)
    assert payload["operation"] == "combat-senses"
    replay = build_play(tmp_path / "replay", play.engine, rng=secrets)
    await seed_campaign(replay.store, before)
    await execute_recorded(replay, record)
    assert result == await CombatSensesService(replay).execute(cid, hearing(), principal_id="gm")
    assert await replay.store.read(cid) == await play.store.read(cid)
    snapshot = play._load(await play.store.read(cid))
    command = hearing(3, "after-revoke")
    service = CombatSensesService(play)
    plan = service.plan(play, snapshot, command, principal_id="gm")
    await revoke_gm(play, cid, 2)
    with pytest.raises(ValidationError, match="director authority"):
        await submit(play, cid, plan, principal_id="gm")


def test_typed_facts_require_explanation_and_distinct_certainty() -> None:
    from pydantic import ValidationError as SchemaError

    with pytest.raises(SchemaError):
        NonvisualObservation(basis="touch", explanation=" ", located=True)
    with pytest.raises(SchemaError):
        NonvisualObservation(
            basis="touch",
            explanation="contact",
            exact_location=ExactLocation(
                basis="continuous-contact", explanation="held continuously"
            ),
        )
    with pytest.raises(SchemaError):
        ADAPTER.validate_python(hearing().model_dump() | {"exact_location": True})


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unchanged_turns_preserve_proof_but_complete_return_path_invalidates(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play = await prepare(tmp_path, backend, "hex")
    result = await CombatSensesService(play).execute(cid, declaration(), principal_id="gm")
    current = play._load(await play.store.read(cid))
    advanced = current.model_copy(
        update={
            "revision": current.revision + 1,
            "resources": current.resources.model_copy(update={"revision": current.revision + 1}),
        }
    )
    untouched = checkpoint(advanced, before=current)
    assert evidence(untouched, untouched.encounters[0], "b", "a") == result
    unrelated = checkpoint(advanced, before=current, moved_actor_ids=frozenset({"c"}))
    assert evidence(unrelated, unrelated.encounters[0], "b", "a") == result
    returned = checkpoint(advanced, before=current, moved_actor_ids=frozenset({"a"}))
    assert evidence(returned, returned.encounters[0], "b", "a") is None
    assert len(invalidations(returned.resources)) == 1
    assert checkpoint(returned, before=current) == returned


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_transaction_failure_after_hearing_roll_rolls_back_all_evidence(
    tmp_path: Path,
    backend: str,
) -> None:
    from dataclasses import replace

    cid, play = await prepare(tmp_path, backend)
    before = await play.store.read(cid)
    state = play._load(before)
    service = CombatSensesService(play)
    plan = service.plan(play, state, hearing(), principal_id="gm")

    def fail(campaign: Campaign) -> CommandReceipt:
        plan.resolve(campaign)
        raise RuntimeError("crash after sensory checkpoint")

    with pytest.raises(RuntimeError, match="crash after sensory"):
        await submit(play, cid, replace(plan, resolve=fail), principal_id="gm")
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert not await played(play.store, cid)
    play.rng = RecordedDice((3, 3, 3))
    result = await CombatSensesService(play).execute(cid, hearing(), principal_id="gm")
    assert result and result.hearing and result.hearing.dice == (3, 3, 3)
    assert len(history(play._load(await play.store.read(cid)).resources)) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("aware", [False, True])
async def test_failed_location_roll_preserves_independent_awareness_only(
    tmp_path: Path,
    backend: str,
    aware: bool,
) -> None:
    cid, play = await prepare(tmp_path, backend)
    play.rng = RecordedDice((6, 6, 6))
    command = hearing().model_copy(
        update={
            "attack_awareness": "The opponent's shouted warning identifies an incoming attack"
            if aware
            else None,
        }
    )
    result = await CombatSensesService(play).execute(cid, command, principal_id="gm")
    assert result and not result.located and result.exact_location is None
    assert result.aware_of_attack == aware
    assert result.awareness_source_id is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_generic_physical_hearing_trace_is_not_combat_location_authority(
    tmp_path: Path,
    backend: str,
) -> None:
    from wayfarer.orchestration.physical_checks import (
        PhysicalCheck,
        PhysicalCheckCommand,
        PhysicalCheckService,
    )

    cid, play = await prepare(tmp_path, backend)
    assert await PhysicalCheckService(
        play, lambda *_: PhysicalCheck("sense", sense="hearing")
    ).execute(
        cid,
        PhysicalCheckCommand(id="generic", actor_id="b", trigger_id="noise", expected_revision=1),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert any(e.id.startswith("physical-check:") for e in state.resources.events)
    assert evidence(state, state.encounters[0], "b", "a") is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_gm_seat_alone_does_not_replace_server_trust(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "members": s.members
                + (CampaignMember(principal_id="untrusted-director", role="gm"),)
            }
        ),
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="GM authority"):
        await CombatSensesService(play).execute(cid, hearing(2), principal_id="untrusted-director")
    assert await play.store.read(cid) == before
    assert not history(play._load(before).resources)


async def hearing_traits_setup(
    path: Path,
    backend: str,
    *purchases: Purchase,
) -> tuple[str, PlayService]:
    """A real approved complete mundane catalog, with no test-only trait aliases."""
    from support.runtime import seed_play
    from test_actions import campaign, world
    from test_mundane_traits import combined_package, runtime_compiler
    from test_statistics import gurps_draft

    from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
    from wayfarer.engine.rules.catalog import RulesCatalog
    from wayfarer.engine.rules.types.location import HumanBody
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
    from wayfarer.engine.simulation.combat.battlefield import Battlefield
    from wayfarer.engine.simulation.combat.profiles import CombatRules
    from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
    from wayfarer.engine.simulation.resource_engine import ResourceEngine
    from wayfarer.engine.simulation.resources import Owner, ResourceState
    from wayfarer.orchestration.combat import StartEncounter

    compiler = runtime_compiler()
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="hearing-test", version=1), frozenset({"gm"})),
        ResourceEngine(
            world(), RulesCatalog((combined_package(),)), compiler.rules, compiler.policy, ()
        ),
        ActionRules(
            id="hearing-test",
            version=1,
            combat=CombatRules(
                id="hearing",
                version=1,
                battlefields=(Battlefield(id="dock", location_id="dock", width=3, height=3),),
                gurps_equipment=EquipmentCatalog(profile_id=PROFILE, entries=()),
            ),
        ),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice((2, 2, 2)))
    initial = campaign(engine)
    await seed_play(
        play,
        initial,
        world(),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100))
        ),
        tuple(
            ActorSetup(
                actor_id=actor,
                body=HumanBody(anatomy="human"),
                proposal=CharacterProposal(draft=gurps_draft(*(purchases if actor == "b" else ()))),
            )
            for actor in ("a", "b")
        ),
    )
    await CombatService(play).execute(
        initial["id"],
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                Placement(actor_id="b", position=GridPoint(x=1, y=0)),
            ),
        ),
        principal_id="gm",
    )
    return initial["id"], play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_approved_deafness_blocks_hearing_before_rng_but_not_other_nonvisual_means(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play = await hearing_traits_setup(
        tmp_path,
        backend,
        Purchase(definition_id="trait:disadvantage:deafness"),
    )
    play.rng = RecordedDice(())
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Deafness prevents"):
        await CombatSensesService(play).execute(cid, hearing(), principal_id="gm")
    assert await play.store.read(cid) == before
    assert not history(play._load(before).resources)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    touched = await CombatSensesService(play).execute(cid, declaration(), principal_id="gm")
    assert touched and touched.located and touched.basis == "touch" and touched.hearing is None
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("purchases", "target"),
    [
        ((Purchase(definition_id="trait:disadvantage:hard-of-hearing"),), 4),
        ((Purchase(definition_id="trait:advantage:acute-hearing", amount=3),), 11),
        (
            (
                Purchase(definition_id="trait:advantage:acute-hearing", amount=3),
                Purchase(definition_id="trait:disadvantage:hard-of-hearing"),
            ),
            7,
        ),
        (
            (
                Purchase(definition_id="trait:acute-hearing", amount=2),
                Purchase(definition_id="trait:advantage:acute-hearing", amount=3),
            ),
            11,
        ),
    ],
)
async def test_complete_catalog_hearing_traits_adjust_the_actual_bound_roll(
    tmp_path: Path,
    backend: str,
    purchases: tuple[Purchase, ...],
    target: int,
) -> None:
    cid, play = await hearing_traits_setup(tmp_path, backend, *purchases)
    result = await CombatSensesService(play).execute(cid, hearing(), principal_id="gm")
    assert result and result.hearing
    assert result.hearing.base_target == 10 and result.hearing.effective_target == target
    assert result.hearing.dice == (2, 2, 2)
    assert result.located == (target >= 6)
    assert result.exact_location is None
    assert await CombatSensesService(play).execute(cid, hearing(), principal_id="gm") == result
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_canonical_head_trauma_deafening_blocks_until_its_recovery_boundary(
    tmp_path: Path,
    backend: str,
) -> None:
    from wayfarer.engine.rules.types.location import LastingInjury

    cid, play = await hearing_traits_setup(tmp_path, backend)

    def deafen(state: PlayState) -> PlayState:
        hp = next(p for p in state.resources.pools if p.id == "hp:b")
        assert hp.injury is not None
        changed = hp.model_copy(
            update={
                "injury": hp.injury.model_copy(
                    update={
                        "lasting_injuries": (
                            LastingInjury(
                                id="head-blow",
                                location="skull",
                                kind="deafened",
                                duration="timed",
                                inflicted_at=0,
                                recovery_at=1,
                                injury=1,
                            ),
                        ),
                    }
                )
            }
        )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            changed if p.id == hp.id else p for p in state.resources.pools
                        ),
                    }
                )
            }
        )

    await change(play, cid, deafen)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Deafness prevents"):
        await CombatSensesService(play).execute(cid, hearing(2), principal_id="gm")
    assert await play.store.read(cid) == before
    # Recovery below consumes the original three dice, proving no earlier roll occurred.
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={"resources": s.resources.model_copy(update={"game_time": 1})}
        ),
    )
    result = await CombatSensesService(play).execute(cid, hearing(3), principal_id="gm")
    assert result and result.located and result.hearing and result.hearing.dice == (2, 2, 2)
