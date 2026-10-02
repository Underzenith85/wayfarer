"""B251 actual Great Haste maneuver and end-fatigue execution."""

import secrets
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_play
from test_actions import campaign, world
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_characters import size_modifier_definition
from wayfarer.engine.rules.magic.movement import package
from wayfarer.engine.rules.traits.movement_forms import RUNTIME_HOOKS
from wayfarer.engine.rules.traits.movement_forms import package as forms_package
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.engine.simulation.magic.great_haste_state import (
    CastGreatHaste,
    DeclareGreatHasteChannel,
    GreatHasteChannel,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import PROFILE
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService


async def prepare(
    path: Path,
    backend: str = "sqlite",
    *,
    self_subject: bool = False,
    seeded: bool = False,
    native_atr: int = 0,
    subject_sm: int = 0,
    battlefield: Battlefield | HexBattlefield | None = None,
) -> tuple[str, PlayService]:
    spells = package(great_haste=True)
    crate = RuleDefinition(
        "equipment:crate",
        DefinitionKind.EQUIPMENT,
        "Test crate",
        "sjg:basic-set-characters-4e-2004",
        0,
        ImplementationStatus.IMPLEMENTED,
    )
    base = profile_package(
        PROFILE,
        *spells.definitions,
        crate,
        size_modifier_definition(),
        *(forms_package().definitions if native_atr else ()),
    )
    base = replace(
        base, sources=tuple({s.id: s for s in (*base.sources, *spells.sources)}.values())
    )
    compiled = profile_compiler(PROFILE, package=base)
    compiled = CharacterCompiler(
        RulesCatalog((base,)),
        compiled.rules,
        replace(
            compiled.policy,
            point_budget=1000,
            allow_supernatural=True,
            allowed_equipment=frozenset({"equipment:crate"}),
        ),
        statistics_profile=PROFILE,
        trait_runtime_hooks=RUNTIME_HOOKS if native_atr else frozenset(),
    )
    authored = world()
    resources = ResourceEngine(
        authored,
        RulesCatalog((base,)),
        compiled.rules,
        compiled.policy,
        (),
    )
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="great-haste", version=1), frozenset({"gm"})),
        resources,
        ActionRules(
            id="great-haste",
            version=1,
            maximum_wait=10000,
            combat=CombatRules(
                id="combat",
                version=1,
                battlefields=(
                    battlefield or Battlefield(id="dock", location_id="dock", width=10, height=10),
                ),
                gurps_equipment=EquipmentCatalog(profile_id=PROFILE, entries=()),
            ),
        ),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=1),
        Purchase(definition_id="spell:haste", amount=4),
        Purchase(definition_id="spell:great-haste", amount=4),
    )
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 12}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    assert compiled.compile(draft).build is not None, compiled.compile(draft).diagnostics
    await seed_play(
        play,
        initial,
        authored,
        ResourceState(
            owners=tuple(Owner(actor_id=a, capacity=1000000) for a in ("a", "b")),
            pools=tuple(
                Pool(id="hp:" + a, current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE))
                for a in ("a", "b")
            ),
        ),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=draft),
                body=HumanBody(anatomy="human"),
            ),
            ActorSetup(
                actor_id="b",
                proposal=CharacterProposal(
                    draft=gurps_draft(
                        *(
                            (Purchase(definition_id="trait:size-modifier", amount=subject_sm),)
                            if subject_sm
                            else ()
                        ),
                        *(
                            (
                                Purchase(
                                    definition_id="advantage:altered-time-rate", amount=native_atr
                                ),
                            )
                            if native_atr
                            else ()
                        ),
                    )
                ),
                body=HumanBody(anatomy="human"),
            ),
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    cid = initial["id"]
    if seeded:
        play.rng = secrets
    service = GreatHasteService(play)
    await service.execute(
        cid,
        DeclareGreatHasteChannel(
            id="channel",
            actor_id="gm",
            expected_revision=0,
            channel=GreatHasteChannel(
                id="great-haste",
                actor_id="a",
                target_id="a" if self_subject else "b",
                location_id="dock",
            ),
        ),
        principal_id="gm",
    )
    return cid, play


async def cast(cid: str, play: PlayService, *, seeded: bool = False, failed: bool = False) -> None:
    service = GreatHasteService(play)
    operations: tuple[Literal["start", "concentrate", "complete"], ...] = (
        "start",
        "concentrate",
        "concentrate",
        "complete",
    )
    for index, operation in enumerate(operations):
        if index:
            state = play._load(await play.store.read(cid))
            await play.execute(
                cid,
                Wait(
                    id="time-" + str(index), actor_id="a", expected_revision=state.revision, ticks=1
                ),
                principal_id="a",
            )
        state = play._load(await play.store.read(cid))
        if operation == "complete" and not seeded:
            play.rng = RecordedDice((5, 5, 5) if failed else (3, 3, 3))
        await service.execute(
            cid,
            CastGreatHaste(
                id="cast-" + str(index),
                actor_id="a",
                expected_revision=state.revision,
                operation=operation,
                channel_id="great-haste",
                cast_id="great",
            ),
            principal_id="alice",
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_spell_grants_two_actual_moves_and_one_real_second(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await cast(cid, play)
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["great"].phase == "active"
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 5
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="b", position=GridPoint(x=1, y=1)),
                Placement(actor_id="a", position=GridPoint(x=4, y=4)),
            ),
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    if state.encounters[0].current_actor_id == "a":
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="ordinary",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="a",
        )
    for index in range(2):
        state = play._load(await play.store.read(cid))
        command = TakeCombatTurn(
            id="move-" + str(index),
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=1, y=2 + index),
        )
        result = await combat.execute(cid, command, principal_id="b")
        assert await combat.execute(cid, command, principal_id="b") == result
        after = play._load(await play.store.read(cid))
        assert after.encounters[0].current_actor_id == ("b" if index == 0 else "a")
        assert after.resources.game_time == (3 if index == 0 else 4)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("self_subject", [False, True])
async def test_expiry_end_fatigue_once_and_self_exception(
    tmp_path: Path, backend: str, self_subject: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, self_subject=self_subject)
    await cast(cid, play)
    state = play._load(await play.store.read(cid))
    expire = Wait(id="expire", actor_id="a", expected_revision=state.revision, ticks=10)
    result = await play.execute(cid, expire, principal_id="a")
    assert await play.execute(cid, expire, principal_id="a") == result
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 5
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == (
        10 if self_subject else 5
    )
    assert len([e for e in state.resources.events if e.id.startswith("great-haste-ended:")]) == 1
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", expected_revision=state.revision, ticks=1),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == (
        10 if self_subject else 5
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cancel_active_spell_applies_subject_fatigue_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await cast(cid, play)
    state = play._load(await play.store.read(cid))
    command = CastGreatHaste(
        id="cancel",
        actor_id="a",
        expected_revision=state.revision,
        operation="cancel",
        channel_id="great-haste",
        cast_id="great",
    )
    service = GreatHasteService(play)
    result = await service.execute(cid, command, principal_id="alice")
    assert await service.execute(cid, command, principal_id="alice") == result
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["great"].phase == "ended"
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 5
    assert len([e for e in state.resources.events if e.id.startswith("great-haste-ended:")]) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_forced_end_fatigue_exhausts_low_fp_and_causes_canonical_hp_loss(
    tmp_path: Path, backend: str
) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue

    cid, play = await prepare(tmp_path, backend)
    await cast(cid, play)

    def exhaust(state: PlayState) -> PlayState:
        resources, _ = apply_fatigue(
            state.resources,
            FatigueCost(
                id="prior-fatigue",
                actor_id="b",
                expected_revision=state.resources.revision,
                amount=8,
                power=True,
            ),
            ht=10,
            will=10,
            rng=RecordedDice(()),
            system=True,
        )
        return state.model_copy(update={"resources": resources})

    await change(cid, play, "prior-fatigue", exhaust)
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 2
    await play.execute(
        cid,
        Wait(id="expire", actor_id="a", expected_revision=state.revision, ticks=10),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == -3
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 7
    end = next(e for e in state.resources.events if e.id.startswith("great-haste-ended:"))
    from wayfarer.engine.simulation.magic.great_haste_state import GreatHasteEnd

    witness = GreatHasteEnd.model_validate_json(end.kind)
    assert (witness.fp_lost, witness.hp_lost) == (5, 3)
    assert end.at == 13


async def start_fight(
    cid: str, play: PlayService, *, subject_position: GridPoint | None = None
) -> None:
    state = play._load(await play.store.read(cid))
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=2, y=2), facing="west"),
                Placement(
                    actor_id="b", position=subject_position or GridPoint(x=1, y=1), facing="east"
                ),
            ),
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a"
    await service.execute(
        cid,
        TakeCombatTurn(
            id="ordinary",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="a",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("launched", [False, True])
async def test_cancel_unspent_bonus_settles_real_turn_and_preserves_launched_response(
    tmp_path: Path, backend: str, launched: bool
) -> None:
    from wayfarer.errors import ConflictError
    from wayfarer.orchestration.combat import ChooseDefense, TakeUnarmedTurn

    cid, play = await prepare(tmp_path, backend)
    await cast(cid, play)
    await start_fight(cid, play)
    combat = CombatService(play)
    state = play._load(await play.store.read(cid))
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="first",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=1, y=2),
        ),
        principal_id="b",
    )
    if launched:
        state = play._load(await play.store.read(cid))
        await combat.execute(
            cid,
            TakeUnarmedTurn(
                id="kick",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                action="kick",
                target_id="a",
                foot="right-foot",
            ),
            principal_id="b",
        )
    state = play._load(await play.store.read(cid))
    revision = state.revision
    command = CastGreatHaste(
        id="cancel",
        actor_id="a",
        expected_revision=revision,
        operation="cancel",
        channel_id="great-haste",
        cast_id="great",
    )
    result = await GreatHasteService(play).execute(cid, command, principal_id="alice")
    assert await GreatHasteService(play).execute(cid, command, principal_id="alice") == result
    state = play._load(await play.store.read(cid))
    assert state.revision == revision + 1 == state.resources.revision
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 5
    if launched:
        assert state.encounters[0].current_actor_id == "b"
        assert state.encounters[0].pending_unarmed is not None
        play.rng = RecordedDice((3, 3, 3, 3, 3, 3))
        await combat.execute(
            cid,
            ChooseDefense(
                id="response",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                defense="none",
            ),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a"
    assert state.encounters[0].maneuver_budget is None
    assert state.resources.game_time == 4
    before = await play.store.read(cid)
    with pytest.raises(ConflictError):
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="extra",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == before
    assert before == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cancellation_removes_spell_bonus_and_retains_native_atr_opportunity(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, native_atr=1)
    await cast(cid, play)
    await start_fight(cid, play)
    combat = CombatService(play)
    state = play._load(await play.store.read(cid))
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="first",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=1, y=2),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    budget = state.encounters[0].maneuver_budget
    assert budget is not None and (budget.native_total, budget.spell_bonus, budget.remaining) == (
        2,
        1,
        2,
    )
    await GreatHasteService(play).execute(
        cid,
        CastGreatHaste(
            id="cancel",
            actor_id="a",
            expected_revision=state.revision,
            operation="cancel",
            channel_id="great-haste",
            cast_id="great",
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    budget = state.encounters[0].maneuver_budget
    assert budget is not None and (budget.native_total, budget.spell_bonus, budget.remaining) == (
        2,
        0,
        1,
    )
    assert state.encounters[0].current_actor_id == "b" and state.resources.game_time == 3
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="native",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=1, y=3),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a" and state.resources.game_time == 4


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seeded_spell_cast_expiry_reexecutes_exactly(tmp_path: Path, backend: str) -> None:
    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare(tmp_path, backend, seeded=True)
    initial = await play.store.read(cid)
    play.seeds = lambda: "00" * 32
    await cast(cid, play, seeded=True)
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["great"].phase == "active"
    await play.execute(
        cid,
        Wait(id="expire", actor_id="a", expected_revision=state.revision, ticks=20),
        principal_id="a",
    )
    records = [
        r
        for r in await play.store.history(cid)
        if r.expected_revision >= initial["revision"] and r.command_id != "setup:seed"
    ]
    identifiers = {r.command_id for r in records}
    final, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)
    final_state = play._load(final)
    end = next(e for e in final_state.resources.events if e.id.startswith("great-haste-ended:"))
    assert end.at == 13 and final_state.resources.game_time == 23


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_random_unarmed_host_captures_and_consumes_extra_opportunity(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.orchestration.combat import ChooseDefense
    from wayfarer.orchestration.combat.unarmed_host import RandomUnarmedService, RandomUnarmedStrike
    from wayfarer.persistence.replay import command_text

    cid, play = await prepare(tmp_path, backend)
    await cast(cid, play)
    await start_fight(cid, play, subject_position=GridPoint(x=1, y=2))
    state = play._load(await play.store.read(cid))
    command = RandomUnarmedStrike(
        id="random-kick",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        target_id="a",
        action="kick",
    )
    service = RandomUnarmedService(play)
    result = await service.execute(cid, command, principal_id="b")
    assert await service.execute(cid, command, principal_id="b") == result
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_unarmed is not None
    budget = state.encounters[0].maneuver_budget
    assert budget is not None and budget.remaining == 2
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3, 3, 3, 3))
    await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="random-response",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    budget = state.encounters[0].maneuver_budget
    assert budget is not None and budget.remaining == 1
    assert state.encounters[0].current_actor_id == "b" and state.resources.game_time == 3
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="second",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=1, y=3),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a" and state.resources.game_time == 4
    record = next(r for r in await play.store.history(cid) if r.command_id == command.id)
    assert "maneuver-budget" in command_text(record)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cancel_settles_captured_turn_after_subject_approval_revoked(
    tmp_path: Path, backend: str
) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.simulation.actions import PlayState

    cid, play = await prepare(tmp_path, backend)
    await cast(cid, play)
    await start_fight(cid, play)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="first",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=1, y=2),
        ),
        principal_id="b",
    )

    def revoke(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"approval": None}) if a.actor_id == "b" else a
                    for a in state.actors
                )
            }
        )

    await change(cid, play, "revoke-subject", revoke)
    state = play._load(await play.store.read(cid))
    await GreatHasteService(play).execute(
        cid,
        CastGreatHaste(
            id="cancel",
            actor_id="a",
            expected_revision=state.revision,
            operation="cancel",
            channel_id="great-haste",
            cast_id="great",
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a" and state.resources.game_time == 4
    assert state.encounters[0].maneuver_budget is None
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 5


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cast_authority_stale_revision_and_active_combat_fail_atomically(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import AuthorizationError, ConflictError, ValidationError

    cid, play = await prepare(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    command = CastGreatHaste(
        id="start",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="great",
    )
    service = GreatHasteService(play)
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await service.execute(cid, command, principal_id="bob")
    assert await play.store.read(cid) == before
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            command.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
    await start_fight(cid, play)
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="encounter turn"):
        await service.execute(
            cid,
            command.model_copy(update={"expected_revision": state.revision}),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("failed", [False, True])
async def test_positive_sm_cast_cost_and_ordinary_failure_payment(
    tmp_path: Path, backend: str, failed: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, subject_sm=1)
    await cast(cid, play, failed=failed)
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["great"]
    assert effect.energy == effect.cost == 10
    assert effect.phase == ("ended" if failed else "active")
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (9 if failed else 0)
    assert not any(e.id.startswith("great-haste-ended:") for e in state.resources.events)
    if not failed:
        await play.execute(
            cid,
            Wait(id="expire", actor_id="a", expected_revision=state.revision, ticks=10),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 5
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_low_caster_fp_rejects_before_casting_dice_and_payment(
    tmp_path: Path, backend: str
) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
    from wayfarer.errors import ValidationError

    cid, play = await prepare(tmp_path, backend)

    def exhaust(state: PlayState) -> PlayState:
        resources, _ = apply_fatigue(
            state.resources,
            FatigueCost(
                id="caster-fatigue",
                actor_id="a",
                expected_revision=state.resources.revision,
                amount=6,
                power=True,
            ),
            ht=10,
            will=10,
            rng=RecordedDice(()),
            system=True,
        )
        return state.model_copy(update={"resources": resources})

    await change(cid, play, "caster-fatigue", exhaust)
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Insufficient FP"):
        await GreatHasteService(play).execute(
            cid,
            CastGreatHaste(
                id="start",
                actor_id="a",
                expected_revision=state.revision,
                operation="start",
                channel_id="great-haste",
                cast_id="great",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 4


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("active", [False, True])
async def test_concurrent_same_subject_cast_rejected_before_dice(
    tmp_path: Path, backend: str, active: bool
) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await prepare(tmp_path, backend)
    service = GreatHasteService(play)
    if active:
        await cast(cid, play)
    else:
        state = play._load(await play.store.read(cid))
        await service.execute(
            cid,
            CastGreatHaste(
                id="first",
                actor_id="a",
                expected_revision=state.revision,
                operation="start",
                channel_id="great-haste",
                cast_id="great",
            ),
            principal_id="alice",
        )
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Concurrent Great Haste"):
        await service.execute(
            cid,
            CastGreatHaste(
                id="second",
                actor_id="a",
                expected_revision=state.revision,
                operation="start",
                channel_id="great-haste",
                cast_id="other",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
