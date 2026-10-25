"""B379: lawful corrosive Innate Attack erodes armor before subsequent consumers."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_basic_combat import start_basic
from test_composed_attack_host import declare
from test_composed_cyclic_boundaries import cyclic
from test_corrosion_armor import projected_dr, total_loss
from test_default_knowledge_hosts import adopt as adopt_society_knowledge
from test_gurps_melee import setup
from trait_support import options

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.ranged import definitions as ranged_definitions
from wayfarer.engine.rules.traits.attack_defense import RUNTIME_HOOKS, package
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.commands import ChooseDefense, EndEncounter, TakeCombatTurn
from wayfarer.engine.simulation.equipment.basic.armor import ARMOR
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.corrosion_state import erosion
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy, binding
from wayfarer.engine.simulation.resources import Advance, Equip, Item, Transfer, Unequip
from wayfarer.engine.simulation.traits.composed_sources import BindComposedSource
from wayfarer.engine.simulation.traits.harmful_physiology_state import AdvancePhysiology
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands

PLATE = next(e for e in ARMOR if e.definition_id == "equipment:lorica-segmentata")


async def fixture(
    path: Path,
    backend: str,
    *,
    repeated: bool = False,
    armor_entry: EquipmentProfile = PLATE,
    natural_dr: int = 0,
    spare: bool = False,
    duplicate: bool = False,
    native_criticals: bool = True,
    innate_beam_skill: bool = False,
) -> tuple[str, PlayService, str, Campaign]:
    modifiers = (cyclic("cor"),) if repeated else ()
    original_id, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        trained=True,
        scene_bound=True,
        start_encounter=False,
        extra_definitions=package().definitions
        + (
            tuple(d for d in ranged_definitions() if d.id == "skill:innate-attack-beam")
            if innate_beam_skill
            else ()
        ),
        trait_runtime_hooks=RUNTIME_HOOKS,
        extra_purchases=(
            Purchase(
                definition_id="advantage:innate-attack",
                amount=2,
                trait=options(**{"damage-type": "cor"}).model_copy(
                    update={"attack_modifiers": modifiers}
                ),
            ),
        )
        + (
            (Purchase(definition_id="advantage:damage-resistance", amount=natural_dr),)
            if natural_dr
            else ()
        ),
        extra_equipment=(armor_entry,)
        + (
            (
                armor_entry.model_copy(
                    update={
                        "definition_id": armor_entry.definition_id + ":outer",
                        "slot": "over-armor",
                        "armor": armor_entry.armor.model_copy(update={"layer": 1})
                        if armor_entry.armor
                        else None,
                    }
                ),
            )
            if duplicate
            else ()
        ),
        preserve_extra_equipment=True,
        extra_items=(
            Item(
                id="plate-b", definition_id=armor_entry.definition_id, owner_id="b", equipped=True
            ),
            *(
                (
                    Item(
                        id="spare-b",
                        definition_id=armor_entry.definition_id + (":outer" if duplicate else ""),
                        owner_id="b",
                        equipped=duplicate,
                    ),
                )
                if spare or duplicate
                else ()
            ),
        ),
    )
    # New genesis retains approved purchases and real pinned gear; it contains no erosion or bound attack.
    source = original._load(await original.store.read(original_id))
    play = build_play(
        path / "actual", original.engine, backend=backend, rng=secrets, seeds=lambda: f"{2:064x}"
    )
    initial = campaign(play.engine)
    actors = tuple(
        ActorSetup.model_validate(a.model_dump(exclude={"approval"})) for a in source.actors
    )
    members = source.members + (
        CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
        CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
    )
    state = play.initial_state(initial, source.world, source.resources, actors, members=members)
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    await CombatService(play).execute(cid, start_basic(1, ranged=True), principal_id="gm")
    state = play._load(await play.store.read(cid))
    command = BindComposedSource(
        id="bind",
        actor_id="a",
        expected_revision=state.revision,
        description="A corrosive beam",
        specialty="beam",
        cyclic_policy=CyclicPolicy(condition="wash") if repeated else None,
    )
    if native_criticals:
        result = await ComposedAttackService(play).execute(cid, command, principal_id="gm")
    else:
        # Execute the actual old producer, without the independent new bind metadata.
        result = await submit(
            play,
            cid,
            ComposedAttackService(play).plan(play, state, command, principal_id="gm"),
            principal_id="gm",
        )
    assert result.source_id
    return cid, play, result.source_id, initial


async def establish_beam_knowledge(play: PlayService, cid: str) -> None:
    """Fresh defaulted Beam use has an actual explicit GM society fact."""
    await adopt_society_knowledge(
        play, cid, ("skill:innate-attack-beam",), command_id="corrosion-beam-known"
    )


async def defend(
    play: PlayService, cid: str, identifier: str = "acid-defense", *, principal: str = "bob"
) -> ChooseDefense:
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id=identifier,
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    await CombatService(play).execute(cid, command, principal_id=principal)
    return command


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_stopped_corrosion_then_sword_uses_reduced_physical_armor(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source, _ = await fixture(tmp_path, backend, innate_beam_skill=True)
    await establish_beam_knowledge(play, cid)
    attack = await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="acid-defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await CombatService(play).execute(cid, command, principal_id="alice")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="bob"
        )
    assert await play.store.read(cid) == before
    play.rng = RecordedDice((2, 2, 2, 2, 3))
    result = await CombatService(play).execute(cid, command, principal_id="bob")
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert total_loss(state.resources, "plate-b") == 1
    assert (
        projected_dr(state.resources, next(i for i in state.resources.items if i.id == "plate-b"))
        == 4
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="wait-b",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="sword",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((3, 3, 3, 5))
    await defend(play, cid, "sword-defense", principal="b")
    final = await play.store.read(cid)
    state = play._load(final)
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 7
    assert total_loss(state.resources, "plate-b") == 1
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    await ComposedAttackService(restarted).execute(cid, attack, principal_id="alice")
    assert await CombatService(restarted).execute(cid, command, principal_id="bob") == result
    assert await restarted.store.read(cid) == final == await restarted.store.replay(cid)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_corrosive_cyclic_requeries_eroded_armor_each_due_time(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source, _ = await fixture(tmp_path, backend, repeated=True, innate_beam_skill=True)
    await establish_beam_knowledge(play, cid)
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2, 2, 3))
    await defend(play, cid)
    state = play._load(await play.store.read(cid))
    cycle = state.resources.cyclic_attacks[0]
    origin = binding(state.resources, cycle.id)
    assert origin and origin.corrosion_dr_erosion and cycle.hp_debt == 0
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="leave",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            reason="Combatants disengage",
        ),
        principal_id="gm",
    )
    for at, hp, loss in ((10, 9, 2), (20, 7, 3)):
        state = play._load(await play.store.read(cid))
        play.rng = RecordedDice((2, 3))
        await HarmfulPhysiologyService(play).execute(
            cid,
            AdvancePhysiology(
                id=f"tick-{at}", actor_id="a", expected_revision=state.revision, to=at
            ),
            principal_id="gm",
        )
        state = play._load(await play.store.read(cid))
        assert next(p.current for p in state.resources.pools if p.id == "hp:b") == hp
        assert total_loss(state.resources, "plate-b") == loss
    assert not state.resources.cyclic_attacks[0].active


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("repeated", [False, True])
async def test_real_corrosion_source_and_damage_reexecute_from_original_seed(
    tmp_path: Path, backend: str, repeated: bool
) -> None:
    cid, play, source, initial = await fixture(
        tmp_path, backend, repeated=repeated, innate_beam_skill=True
    )
    await establish_beam_knowledge(play, cid)
    await declare(play, cid, source)
    await defend(play, cid)
    state = play._load(await play.store.read(cid))
    if repeated:
        await CombatService(play).execute(
            cid,
            EndEncounter(
                id="leave",
                actor_id="gm",
                expected_revision=state.revision,
                encounter_id="fight",
                reason="Leave the fight",
            ),
            principal_id="gm",
        )
        play.seeds = lambda: f"{1:064x}"
        for at in (10, 20):
            state = play._load(await play.store.read(cid))
            await HarmfulPhysiologyService(play).execute(
                cid,
                AdvancePhysiology(
                    id=f"tick-{at}", actor_id="a", expected_revision=state.revision, to=at
                ),
                principal_id="gm",
            )
        state = play._load(await play.store.read(cid))
        assert total_loss(state.resources, "plate-b") == 3
        assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 3
    else:
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="wait-b",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="sword",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="swing",
                target_id="b",
            ),
            principal_id="a",
        )
        play.seeds = lambda: f"{7:064x}"
        await defend(play, cid, "sword-defense", principal="b")
        state = play._load(await play.store.read(cid))
        assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 4
    final = await play.store.read(cid)
    state = play._load(final)
    assert erosion(state.resources, "plate-b") is not None
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final


@pytest.mark.parametrize("variant", ["natural", "split", "multi-location", "front", "multiple"])
async def test_new_corrosion_lane_refuses_unsupported_target_composition_atomically(
    tmp_path: Path,
    variant: str,
) -> None:
    assert PLATE.armor
    armor = PLATE.armor
    if variant == "split":
        armor = armor.model_copy(update={"split_dr": (("cor", 2),)})
    elif variant == "multi-location":
        armor = armor.model_copy(update={"locations": ("torso", "groin")})
    elif variant == "front":
        armor = armor.model_copy(update={"front_only": True})
    entry = PLATE.model_copy(update={"armor": armor})
    cid, play, source, _ = await fixture(
        tmp_path,
        "sqlite",
        armor_entry=entry,
        natural_dr=1 if variant == "natural" else 0,
        duplicate=variant == "multiple",
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Corrosion"):
        await declare(play, cid, source)
    assert await play.store.read(cid) == before and play.rng.exhausted()


async def test_actual_resource_transfer_reequip_and_time_preserve_item_erosion(
    tmp_path: Path,
) -> None:
    cid, play, source, _ = await fixture(tmp_path, "sqlite", spare=True, innate_beam_skill=True)
    await establish_beam_knowledge(play, cid)
    await declare(play, cid, source)
    play.rng = RecordedDice((2, 2, 2, 2, 3))
    await defend(play, cid)
    resources = play._load(await play.store.read(cid)).resources
    resources = play.engine.resources.apply(
        resources,
        Unequip(id="off", actor_id="b", expected_revision=resources.revision, item_id="plate-b"),
    )
    resources = play.engine.resources.apply(
        resources,
        Transfer(
            id="give",
            actor_id="b",
            expected_revision=resources.revision,
            item_id="plate-b",
            quantity=1,
            owner_id="a",
        ),
    )
    resources = play.engine.resources.apply(
        resources,
        Equip(id="on", actor_id="a", expected_revision=resources.revision, item_id="plate-b"),
    )
    resources = play.engine.resources.apply(
        resources,
        Equip(id="new", actor_id="b", expected_revision=resources.revision, item_id="spare-b"),
    )
    resources = play.engine.resources.apply(
        resources,
        Advance(id="time", actor_id="a", expected_revision=resources.revision, to=86400),
        system=True,
        rng=RecordedDice(()),
    )
    old = next(i for i in resources.items if i.id == "plate-b")
    new = next(i for i in resources.items if i.id == "spare-b")
    assert old.owner_id == "a" and projected_dr(resources, old) == 4
    assert new.owner_id == "b" and projected_dr(resources, new) == 5
