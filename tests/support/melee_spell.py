"""Original approved genesis and actual Staff manufacture for B240/B245 hosts."""

import secrets
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Literal

from test_actions import campaign
from test_haste_manufacture import prepare as blueprint
from test_statistics import profile_compiler, profile_package

from support.runtime import build_play, build_runtime, seed_campaign
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.magic.body_control import package as body_package
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.skills.mundane.melee import definitions as melee_definitions
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.combat.battlefield import Battlefield
from wayfarer.engine.simulation.equipment.basic.armor import ARMOR, SHIELDS
from wayfarer.engine.simulation.equipment.basic.melee import WEAPONS
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
from wayfarer.engine.simulation.magic.enchanting import EnchantingRules, EnchantmentRecipe
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    SettleEnchanting,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    CastDeathtouch,
    ObserveMeleeMana,
    StaffCarrier,
)
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, ResourceState
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.melee_spells import MeleeSpellService
from wayfarer.orchestration.play import PlayService

CHAIN = (
    "itch",
    "spasm",
    "pain",
    "clumsiness",
    "hinder",
    "paralyze-limb",
    "wither-limb",
    "deathtouch",
)


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def fixture(
    path: Path,
    backend: str,
    *,
    manufacture: bool = True,
    defender_item: Literal["staff", "shield"] | None = None,
    defender_armor: bool = False,
    victim_mode: Literal["dead", "diffuse"] | None = None,
    haste_route: bool = False,
) -> tuple[str, PlayService, Campaign]:
    base_id, original = await blueprint(path / "blueprint", backend, 1)
    foundation = original._load(await original.store.read(base_id))
    base = original.engine.reviewer.compiler
    definitions = dict(base.definitions)
    definitions.update(
        {
            d.id: d
            for d in (
                *body_package().definitions,
                *(
                    d
                    for d in melee_definitions()
                    if d.id
                    in (
                        "skill:staff",
                        "skill:two-handed-sword",
                        "skill:shield",
                        "skill:shield-standard",
                        "skill:shield-buckler",
                        "skill:shield-force",
                    )
                ),
            )
        }
    )
    definitions["equipment:quarterstaff"] = RuleDefinition(
        "equipment:quarterstaff",
        DefinitionKind.EQUIPMENT,
        "Quarterstaff",
        "sjg:basic-set-characters-4e-2004",
        10,
        ImplementationStatus.IMPLEMENTED,
    )
    shield = next(p for p in SHIELDS if p.definition_id == "equipment:medium-shield")
    definitions[shield.definition_id] = RuleDefinition(
        shield.definition_id,
        DefinitionKind.EQUIPMENT,
        "Medium Shield",
        "sjg:basic-set-characters-4e-2004",
        60,
        ImplementationStatus.IMPLEMENTED,
    )
    armor = next(p for p in ARMOR if p.definition_id == "equipment:leather-armor")
    definitions[armor.definition_id] = RuleDefinition(
        armor.definition_id,
        DefinitionKind.EQUIPMENT,
        "Leather Armor",
        "sjg:basic-set-characters-4e-2004",
        100,
        ImplementationStatus.IMPLEMENTED,
    )
    combined = profile_package("gurps-basic-set-4e-2004", *definitions.values())
    combined = replace(
        combined,
        definitions=tuple({d.id: d for d in combined.definitions}.values()),
        sources=tuple(
            {
                s.id: s
                for p in (combined, body_package(), enchantment_package())
                for s in p.sources
            }.values()
        ),
    )
    rebuilt = profile_compiler("gurps-basic-set-4e-2004", package=combined)
    policy = replace(
        base.policy,
        allowed_equipment=base.policy.allowed_equipment
        | {"equipment:quarterstaff", shield.definition_id, armor.definition_id},
        point_budget=1000,
    )
    compiler = CharacterCompiler(
        RulesCatalog((combined,)), rebuilt.rules, policy, statistics_profile=base.statistics_profile
    )
    combat = original.engine.rules.combat
    assert combat is not None and combat.gurps_equipment is not None
    staff = next(p for p in WEAPONS if p.definition_id == "equipment:quarterstaff")
    workspace = combat.gurps_equipment.entries[1]
    profiles = EquipmentCatalog(
        profile_id="gurps-basic-set-4e-2004", entries=(staff, workspace, shield, armor)
    )
    resources = ResourceEngine(
        foundation.world,
        RulesCatalog((combined,)),
        compiler.rules,
        policy,
        tuple(p.inventory_spec() for p in profiles.entries),
    )
    recipe = EnchantmentRecipe(
        id="source-staff",
        spell_id="spell:staff",
        effect_id="effect:staff",
        method="slow-and-sure",
        energy_required=30,
        target_definition_ids=(staff.definition_id,),
        workspace_definition_id=workspace.definition_id,
        runtime_family="staff",
        activation="always-on",
        requires_magery=True,
    )
    engine = ActionEngine(
        PowerReviewer(compiler, original.engine.reviewer.policy, frozenset({"gm"})),
        resources,
        original.engine.rules.model_copy(
            update={
                "enchanting": EnchantingRules(id="staff", version=1, recipes=(recipe,)),
                "combat": combat.model_copy(
                    update={
                        "gurps_equipment": profiles,
                        "battlefields": (
                            Battlefield(id="dock-field", location_id="dock", width=10, height=10),
                        ),
                    }
                ),
            }
        ),
    )
    play = build_play(path, engine, backend=backend, rng=secrets)
    initial = campaign(engine)
    original_draft = foundation.actors[0].proposal.draft
    draft = original_draft.model_copy(
        update={
            "purchases": original_draft.purchases
            + (
                Purchase(definition_id="spell:staff", amount=4),
                Purchase(definition_id="skill:staff", amount=8),
                *(
                    Purchase(definition_id="spell:" + key)
                    for key in (
                        (*tuple(s for s in CHAIN if s != "clumsiness"), "rooted-feet")
                        if haste_route
                        else CHAIN
                    )
                ),
            )
        }
    )
    compilation = compiler.compile(draft)
    assert compilation.legal and compilation.build is not None, compilation.diagnostics
    defender_profile = shield if defender_item == "shield" else staff
    defender_inventory: tuple[Item, ...] = (
        (
            Item(
                id="defender-implement",
                definition_id=defender_profile.definition_id,
                owner_id="b",
                equipped=True,
                ready=True,
                condition=ObjectCondition(hp=defender_profile.durability.hp)
                if defender_profile.durability
                else None,
            ),
        )
        if defender_item is not None
        else ()
    )
    if defender_armor:
        defender_inventory += (
            Item(
                id="defender-armor",
                definition_id=armor.definition_id,
                owner_id="b",
                equipped=True,
                ready=True,
            ),
        )
    initial_state = play.initial_state(
        initial,
        foundation.world,
        ResourceState(
            owners=foundation.resources.owners,
            items=(
                Item(
                    id="real-staff",
                    definition_id=staff.definition_id,
                    owner_id="a",
                    equipped=True,
                    ready=True,
                    condition=ObjectCondition(hp=staff.durability.hp) if staff.durability else None,
                ),
                Item(id="workshop", definition_id=workspace.definition_id, owner_id="a"),
            )
            + defender_inventory,
        ),
        tuple(
            ActorSetup(
                actor_id=a.actor_id,
                proposal=CharacterProposal(
                    draft=draft.model_copy(
                        update={
                            "purchases": draft.purchases
                            + (Purchase(definition_id="skill:shield-standard", amount=8),)
                        }
                    )
                    if a.actor_id == "b" and defender_item == "shield"
                    else draft
                ),
                body=a.body,
                held_item_hands=(("real-staff", "right-hand"), ("real-staff", "left-hand"))
                if a.actor_id == "a"
                else (("defender-implement", "left-hand"),)
                if defender_item == "shield"
                else (("defender-implement", "left-hand"), ("defender-implement", "right-hand"))
                if defender_item == "staff"
                else (),
            )
            for a in foundation.actors
        ),
        members=foundation.members,
    )
    if victim_mode is not None:
        # Authored original physical scenario, never an invented health-service receipt.
        from wayfarer.engine.rules.types.location import InjuryTolerance

        initial_state = initial_state.model_copy(
            update={
                "resources": initial_state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(
                                update={
                                    "injury": p.injury.model_copy(
                                        update={"dead": True}
                                        if victim_mode == "dead"
                                        else {"tolerance": InjuryTolerance(structure="diffuse")}
                                    )
                                }
                            )
                            if p.id == "hp:b" and p.injury is not None
                            else p
                            for p in initial_state.resources.pools
                        )
                    }
                )
            }
        )
    initial["play_json"] = initial_state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    enchant = EnchantmentService(play)
    await enchant.declare_staff(
        cid,
        DeclareStaffConstruction(
            id="construction",
            actor_id="a",
            expected_revision=await revision(play, cid),
            construction=StaffConstruction(
                item_id="real-staff",
                definition_id=staff.definition_id,
                form="full-staff",
                material="wood",
                once_living=True,
                length_yards=Fraction(2),
            ),
        ),
        principal_id="gm",
    )
    if manufacture:
        await enchant.execute(
            cid,
            CreateEnchantment(
                id="create-staff",
                actor_id="a",
                expected_revision=await revision(play, cid),
                project_id="made-staff",
                recipe_id=recipe.id,
                target_item_id="real-staff",
                enchanter_ids=("a", "b"),
            ),
            principal_id="gm",
        )
        await enchant.execute(
            cid,
            BeginEnchanting(
                id="begin-staff",
                actor_id="a",
                expected_revision=await revision(play, cid),
                project_id="made-staff",
            ),
            principal_id="gm",
        )
        state = play._load(await play.store.read(cid))
        work = state.resources.enchantment_projects[0].active_work
        assert work is not None
        await enchant.execute(
            cid,
            AdvanceEnchanting(
                id="work-staff",
                actor_id="a",
                expected_revision=state.revision,
                project_id="made-staff",
                work_id=work.id,
                to=work.due,
            ),
            principal_id="gm",
        )
        play.seeds = lambda: f"{1:064x}"
        await enchant.execute(
            cid,
            SettleEnchanting(
                id="settle-staff",
                actor_id="a",
                expected_revision=await revision(play, cid),
                project_id="made-staff",
                work_id=work.id,
            ),
            principal_id="gm",
        )
        assert next(
            i
            for i in play._load(await play.store.read(cid)).resources.items
            if i.id == "real-staff"
        ).enchantments
    await MeleeSpellService(play).execute(
        cid,
        ObserveMeleeMana(
            id="melee-mana",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            location_id="dock",
        ),
        principal_id="gm",
    )
    return cid, play, initial


async def cast(play: PlayService, cid: str, *, energy: int = 3) -> CastDeathtouch:
    command = CastDeathtouch(
        id="death-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="death",
        energy=energy,
        carrier=StaffCarrier(hand="right-hand", item_id="real-staff"),
    )
    await build_runtime(play).submit_json(
        cid, command.model_dump(mode="json"), principal_id="alice"
    )
    await build_runtime(play).submit_json(
        cid,
        command.model_copy(
            update={
                "id": "death-work",
                "operation": "concentrate",
                "expected_revision": await revision(play, cid),
            }
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    completed = command.model_copy(
        update={
            "id": "death-complete",
            "operation": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    await build_runtime(play).submit_json(
        cid, completed.model_dump(mode="json"), principal_id="alice"
    )
    return completed
