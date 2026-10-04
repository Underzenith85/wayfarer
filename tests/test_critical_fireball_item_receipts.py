"""B481/B482 actual Fireball800 staff manufacture feeds general SpellService."""

import secrets
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_haste_manufacture import prepare as blueprint
from test_haste_manufacture import revision
from test_haste_manufacture_power_composition import _canonical_campaign
from test_power_project_haste import _total
from test_spell_construction import college_fixtures
from test_statistics import profile_compiler, profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.fire import package as fire_package
from wayfarer.engine.rules.magic.healing import package as healing_package
from wayfarer.engine.rules.magic.spell_catalog import projectile_definition
from wayfarer.engine.rules.skills.mundane.melee import definitions as melee_definitions
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.basic.melee import WEAPONS
from wayfarer.engine.simulation.magic.bindings import SpellChannel, SpellRules
from wayfarer.engine.simulation.magic.enchanting import (
    EnchantingRules,
    EnchantmentMaterial,
    EnchantmentRecipe,
)
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    SettleEnchanting,
)
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellEvent, event_id
from wayfarer.engine.simulation.magic.spells import SpellCommand
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, ResourceState
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import verify_commands


async def prepare(path: Path, backend: str) -> tuple[str, PlayService, Campaign]:
    cid, original = await blueprint(path / "blueprint", backend, 1)
    foundation = original._load(await original.store.read(cid))
    base = original.engine.reviewer.compiler
    definitions = dict(base.definitions)
    fire = fire_package()
    definitions.update({d.id: d for d in fire.definitions})
    definitions["skill:innate-attack-projectile"] = projectile_definition()
    definitions.update(
        {d.id: d for d in melee_definitions() if d.id in ("skill:staff", "skill:two-handed-sword")}
    )
    definitions["equipment:quarterstaff"] = replace(
        definitions["equipment:cloak"], id="equipment:quarterstaff", name="Wood staff"
    )
    definitions["equipment:fireball-materials"] = replace(
        definitions["equipment:workshop"],
        id="equipment:fireball-materials",
        name="Fireball magical materials ($400 lot)",
    )
    package = profile_package("gurps-basic-set-4e-2004", *definitions.values())
    package = replace(package, definitions=tuple({d.id: d for d in package.definitions}.values()))
    package = replace(
        package,
        sources=tuple(
            {
                s.id: s
                for s in (
                    *package.sources,
                    *fire.sources,
                    *(
                        source
                        for package in (
                            enchantment_package(),
                            healing_package(),
                            *college_fixtures(10),
                        )
                        for source in package.sources
                    ),
                )
            }.values()
        ),
    )
    catalog = RulesCatalog((package,))
    rebuilt = profile_compiler("gurps-basic-set-4e-2004", package=package)
    policy = replace(
        base.policy,
        permitted_sources=frozenset(s.id for s in package.sources),
        allowed_equipment=frozenset(
            {"equipment:quarterstaff", "equipment:workshop", "equipment:fireball-materials"}
        ),
    )
    compiler = CharacterCompiler(
        catalog, rebuilt.rules, policy, statistics_profile=base.statistics_profile
    )
    combat = original.engine.rules.combat
    assert combat and combat.gurps_equipment
    staff = next(p for p in WEAPONS if p.definition_id == "equipment:quarterstaff")
    workshop = next(
        p for p in combat.gurps_equipment.entries if p.definition_id == "equipment:workshop"
    )
    lot = workshop.model_copy(
        update={
            "definition_id": "equipment:fireball-materials",
            "price": 400,
            "weight_millipounds": 1000,
        }
    )
    equipment = combat.gurps_equipment.model_copy(update={"entries": (staff, workshop, lot)})
    recipe = EnchantmentRecipe(
        id="source-fireball",
        spell_id="spell:fireball",
        effect_id="effect:fireball",
        method="slow-and-sure",
        energy_required=800,
        target_definition_ids=(staff.definition_id,),
        workspace_definition_id=workshop.definition_id,
        materials=(EnchantmentMaterial(definition_id=lot.definition_id, quantity=1),),
        runtime_spell_id="fireball",
        runtime_family="spell",
        requires_magery=True,
    )
    rules = original.engine.rules.model_copy(
        update={
            "combat": combat.model_copy(update={"gurps_equipment": equipment}),
            "enchanting": EnchantingRules(id="source-fireball", version=1, recipes=(recipe,)),
            "spells": SpellRules(
                id="source-fireball",
                version=1,
                channels=(
                    SpellChannel(
                        id="item-fireball",
                        actor_id="a",
                        target_id="a",
                        location_id="dock",
                        spell_id="fireball",
                        magic_item_id="staff",
                    ),
                ),
            ),
        }
    )
    resources = ResourceEngine(
        foundation.world,
        catalog,
        compiler.rules,
        policy,
        tuple(e.inventory_spec() for e in equipment.entries),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, original.engine.reviewer.policy, original.engine.reviewer.gm_ids),
        resources,
        rules,
    )
    draft = foundation.actors[0].proposal.draft
    purchased = {p.definition_id for p in draft.purchases}
    draft = draft.model_copy(
        update={
            "purchases": draft.purchases
            + tuple(
                Purchase(definition_id="spell:" + key, amount=4)
                for key in ("ignite-fire", "create-fire", "shape-fire", "fireball")
                if "spell:" + key not in purchased
            )
        }
    )
    result = compiler.compile(draft)
    assert result.build is not None, result.diagnostics
    play = build_play(path / "host", engine, backend=backend, rng=secrets)
    play.seeds = lambda: f"{270:064x}"
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        foundation.world,
        ResourceState(
            owners=foundation.resources.owners,
            items=(
                Item(
                    id="staff",
                    definition_id=staff.definition_id,
                    owner_id="a",
                    equipped=True,
                    ready=True,
                ),
                Item(id="workshop", definition_id=workshop.definition_id, owner_id="a"),
                Item(id="materials", definition_id=lot.definition_id, owner_id="a"),
            ),
        ),
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=CharacterProposal(draft=draft), body=a.body)
            for a in foundation.actors
        ),
        members=foundation.members,
    )
    initial["play_json"] = state.model_dump_json()
    initial = await seed_campaign(play.store, initial)
    return initial["id"], play, initial


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("principal", ["alice", "gm"])
@pytest.mark.parametrize("failed", [False, True])
async def test_actual_critical_fireball_manufacture_general_receipt(
    tmp_path: Path, backend: str, principal: str, failed: bool
) -> None:
    cid, play, initial = await prepare(tmp_path, backend)
    service = EnchantmentService(play)
    await service.declare_staff(
        cid,
        DeclareStaffConstruction(
            id="staff-source",
            actor_id="a",
            expected_revision=await revision(play, cid),
            construction=StaffConstruction(
                item_id="staff",
                definition_id="equipment:quarterstaff",
                form="full-staff",
                material="wood",
                once_living=True,
                length_yards=Fraction(2),
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        CreateEnchantment(
            id="create",
            actor_id="a",
            expected_revision=await revision(play, cid),
            project_id="made",
            recipe_id="source-fireball",
            target_item_id="staff",
            enchanter_ids=("a", "b"),
        ),
        principal_id="gm",
    )
    assert not any(
        i.id == "materials" for i in play._load(await play.store.read(cid)).resources.items
    )
    await service.execute(
        cid,
        BeginEnchanting(
            id="begin", actor_id="a", expected_revision=await revision(play, cid), project_id="made"
        ),
        principal_id="gm",
    )
    project = play._load(await play.store.read(cid)).resources.enchantment_projects[0]
    assert project.active_work
    assert project.active_work.due == 399 * 86400 + 28800
    work = project.active_work
    await service.execute(
        cid,
        AdvanceEnchanting(
            id="work",
            actor_id="a",
            expected_revision=await revision(play, cid),
            project_id="made",
            work_id=work.id,
            to=work.due,
        ),
        principal_id="gm",
    )
    outcome = await service.execute(
        cid,
        SettleEnchanting(
            id="settle",
            actor_id="a",
            expected_revision=await revision(play, cid),
            project_id="made",
            work_id=work.id,
        ),
        principal_id="gm",
    )
    assert outcome.check and outcome.check.dice == (1, 1, 1)
    binding = next(
        i for i in play._load(await play.store.read(cid)).resources.items if i.id == "staff"
    ).enchantments[0]
    assert (
        binding.spell_id == "fireball"
        and binding.requires_magery
        and binding.power == outcome.check.effective_target + 11
    )
    cast_seed = (
        next(f"{n:064x}" for n in range(10000) if _total(f"{n:064x}") == 17)
        if failed
        else f"{1:064x}"
    )
    play.seeds = lambda: cast_seed
    spells = SpellService(play)
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                Placement(actor_id="b", position=GridPoint(x=2, y=0)),
            ),
        ),
        principal_id="gm",
    )
    start = SpellCommand(
        id="cast",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="fireball",
        cast_id="held",
        channel_id="item-fireball",
        energy=1,
    )
    await spells.execute(cid, start, principal_id=principal)
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="other-turn",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    complete = start.model_copy(
        update={
            "id": "complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    result = await spells.execute(cid, complete, principal_id=principal)
    assert result.energy_spent == 1
    assert result.outcome == ("failed" if failed else "active")
    assert bool(result.checks) == (principal == "gm")
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert not any(e.id.startswith("power-cast-origin:") for e in state.resources.events)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    canonical = RuntimeSpellEvent.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == event_id(complete.id))
    ).result
    assert canonical.checks and canonical.checks[0].base_target == binding.power
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await SpellService(restarted).execute(cid, complete, principal_id=principal) == result
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(saved).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "replay"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)
