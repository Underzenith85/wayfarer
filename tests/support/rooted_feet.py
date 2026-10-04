"""Original genesis, real spell producer and independently approved identifying mage."""

import secrets
from dataclasses import replace
from pathlib import Path

from test_actions import campaign
from test_haste_manufacture import prepare as manufacture_blueprint
from test_statistics import gurps_draft, profile_package

from support.runtime import build_play, seed_campaign
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.magic.body_control import package as body_package
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.fire import package as fire_package
from wayfarer.engine.rules.magic.knowledge import package
from wayfarer.engine.rules.magic.movement import package as movement_package
from wayfarer.engine.rules.magic.spell_catalog import projectile_definition
from wayfarer.engine.rules.magic.water import package as water_package
from wayfarer.engine.rules.skills.mundane.melee import definitions as melee_definitions
from wayfarer.engine.rules.skills.mundane.ranged import definitions as ranged_definitions
from wayfarer.engine.rules.traits.mundane import candidate_package as mundane_package
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneExit, SceneRules
from wayfarer.engine.simulation.combat.battlefield import Battlefield
from wayfarer.engine.simulation.equipment.basic.armor import SHIELDS
from wayfarer.engine.simulation.equipment.basic.melee import WEAPONS
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.simulation.magic.bindings import SpellRules
from wayfarer.engine.simulation.magic.haste_state import HasteMana, ObserveHasteMana
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item
from wayfarer.engine.world import Entity, EntityKind, Fact
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.play import PlayService


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def fixture(
    path: Path,
    backend: str,
    *,
    extra_purchases: tuple[Purchase, ...] = (),
    producer_iq: int | None = None,
    spell_rules: SpellRules | None = None,
    subject_mage: bool = True,
    subject_st: int = 10,
    subject_fp: int = 10,
    subject_hp: int | None = None,
    subject_ht: int | None = None,
    subject_dx: int | None = None,
    subject_purchases: tuple[Purchase, ...] = (),
    bidirectional_visibility: bool = False,
    combat_weapons: bool = False,
    ranged_weapon: bool = False,
    shield_rush_fixture: bool = False,
) -> tuple[str, PlayService, Campaign]:
    base_id, original = await manufacture_blueprint(path / "blueprint", backend, 1)
    foundation = original._load(await original.store.read(base_id))
    base = original.engine.reviewer.compiler
    definitions = dict(base.definitions)
    definitions.update(
        {
            d.id: d
            for p in (
                package(),
                movement_package(),
                water_package(),
                fire_package(),
                body_package(),
            )
            for d in p.definitions
        }
    )
    if any(
        p.definition_id == "trait:combat-reflexes" for p in (*subject_purchases, *extra_purchases)
    ):
        definitions.update(
            {d.id: d for d in mundane_package().definitions if d.id == "trait:combat-reflexes"}
        )
    sword = next(p for p in WEAPONS if p.definition_id == "equipment:broadsword")
    if combat_weapons:
        definitions.update(
            {d.id: d for d in melee_definitions() if d.id in ("skill:broadsword", "skill:brawling")}
        )
        definitions[sword.definition_id] = RuleDefinition(
            sword.definition_id,
            DefinitionKind.EQUIPMENT,
            "Broadsword",
            "sjg:basic-set-characters-4e-2004",
            500,
            ImplementationStatus.IMPLEMENTED,
        )
    hatchet = next(p for p in WEAPONS if p.definition_id == "equipment:hatchet")
    shield = next(p for p in SHIELDS if p.definition_id == "equipment:medium-shield")
    added_profiles = (
        ((sword,) if combat_weapons else ())
        + ((hatchet,) if ranged_weapon else ())
        + ((shield,) if shield_rush_fixture else ())
    )
    if ranged_weapon:
        definitions.update({d.id: d for d in melee_definitions() if d.id == "skill:axe-mace"})
        definitions.update(
            {d.id: d for d in ranged_definitions() if d.id == "skill:thrown-weapon-axe-mace"}
        )
    if shield_rush_fixture:
        definitions.update(
            {
                d.id: d
                for d in melee_definitions()
                if d.id in ("skill:shield-standard", "skill:shield")
            }
        )
    for equipment in added_profiles:
        if equipment.definition_id not in definitions:
            definitions[equipment.definition_id] = RuleDefinition(
                equipment.definition_id,
                DefinitionKind.EQUIPMENT,
                equipment.definition_id,
                "sjg:basic-set-characters-4e-2004",
                int(equipment.price),
                ImplementationStatus.IMPLEMENTED,
            )
    projectile = projectile_definition()
    definitions[projectile.id] = projectile
    combined = profile_package("gurps-basic-set-4e-2004", *definitions.values())
    combined = replace(
        combined,
        definitions=tuple({d.id: d for d in combined.definitions}.values()),
        sources=tuple(
            {
                s.id: s
                for p in (
                    combined,
                    package(),
                    movement_package(),
                    water_package(),
                    fire_package(),
                    body_package(),
                    enchantment_package(),
                    *(
                        (mundane_package(),)
                        if any(
                            p.definition_id == "trait:combat-reflexes"
                            for p in (*subject_purchases, *extra_purchases)
                        )
                        else ()
                    ),
                )
                for s in p.sources
            }.values()
        ),
    )
    catalog = RulesCatalog((combined,))
    cr_selected = any(
        p.definition_id == "trait:combat-reflexes" for p in (*subject_purchases, *extra_purchases)
    )
    base_policy = (
        replace(
            base.policy,
            permitted_sources=base.policy.permitted_sources
            | frozenset(source.id for source in mundane_package().sources),
        )
        if cr_selected
        else base.policy
    )
    compiler = CharacterCompiler(
        catalog,
        replace(base.rules, packages=(PackagePin(combined.id, combined.version, combined.digest),)),
        replace(
            base_policy,
            allowed_equipment=base_policy.allowed_equipment
            | {p.definition_id for p in added_profiles},
        )
        if added_profiles
        else base_policy,
        statistics_profile=base.statistics_profile,
        trait_runtime_hooks=base.trait_runtime_hooks
        | (frozenset({"trait.combat_reflexes"}) if cr_selected else frozenset()),
    )
    world = replace(
        foundation.world,
        facts=foundation.world.facts
        + (
            Fact("visible-b", "b", "visible", "yes"),
            Fact("visible-chest", "chest", "visible", "yes"),
        )
        + ((Fact("visible-a", "a", "visible", "yes"),) if bidirectional_visibility else ()),
        knowledge=foundation.world.knowledge
        + (("a", "visible-b"), ("a", "visible-chest"))
        + ((("b", "visible-a"),) if bidirectional_visibility else ()),
        entities=foundation.world.entities
        + (Entity("c", EntityKind.ACTOR, "Cora", location_id="dock"),),
    )
    resources = ResourceEngine(
        world,
        catalog,
        compiler.rules,
        compiler.policy,
        tuple(original.engine.resources.specs.values())
        + tuple(p.inventory_spec() for p in added_profiles),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, original.engine.reviewer.policy, frozenset({"gm"})),
        resources,
        original.engine.rules.model_copy(
            update={
                "spells": spell_rules if spell_rules is not None else original.engine.rules.spells,
                "combat": original.engine.rules.combat.model_copy(
                    update={
                        "gurps_equipment": EquipmentCatalog(
                            profile_id=original.engine.rules.combat.gurps_equipment.profile_id,
                            entries=original.engine.rules.combat.gurps_equipment.entries
                            + added_profiles,
                        )
                        if added_profiles and original.engine.rules.combat.gurps_equipment
                        else original.engine.rules.combat.gurps_equipment,
                        "battlefields": (
                            HexBattlefield(
                                id="dock-field",
                                location_id="dock",
                                coordinate_system="hex-axial-v1",
                                profile_id="gurps-basic-set-4e-2004",
                                baseline_id=BASELINE_ID,
                                cells=tuple(
                                    Cell(position=Hex(q=q, r=r))
                                    for q in range(-2, 7)
                                    for r in range(-2, 3)
                                ),
                            )
                            if shield_rush_fixture
                            else Battlefield(
                                id="dock-field", location_id="dock", width=10, height=10
                            ),
                        ),
                    }
                )
                if original.engine.rules.combat
                else None,
                "scenes": SceneRules(
                    id="analysis-workshop",
                    version=1,
                    scenes=(
                        Scene(
                            id="dock",
                            version=1,
                            location_id="dock",
                            title="Workshop",
                            exits=(SceneExit(id="to-alley", destination_id="alley"),),
                        ),
                        Scene(id="alley", version=1, location_id="alley", title="Alley"),
                    ),
                ),
            }
        ),
    )
    play = build_play(path / "host", engine, backend=backend, rng=secrets)
    play.seeds = lambda: f"{1:064x}"
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=1),
        *(
            Purchase(definition_id="spell:" + s, amount=1)
            for s in ("haste", "hinder", "rooted-feet")
        ),
    )
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 11}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    assert compiler.compile(draft).legal
    scenario = foundation.resources.model_copy(
        update={
            "revision": 0,
            "events": (),
            "items": foundation.resources.items
            + tuple(
                Item(
                    id="sword-" + actor,
                    definition_id=sword.definition_id,
                    owner_id=actor,
                    ready=True,
                    equipped=True,
                    condition=ObjectCondition(hp=sword.durability.hp) if sword.durability else None,
                )
                for actor in ("a", "b")
                if combat_weapons
            )
            + tuple(
                Item(
                    id=identifier,
                    definition_id=profile.definition_id,
                    owner_id="a",
                    ready=True,
                    equipped=True,
                    condition=ObjectCondition(hp=profile.durability.hp)
                    if profile.durability
                    else None,
                )
                for identifier, profile, enabled in (
                    ("hatchet-a", hatchet, ranged_weapon),
                    ("rush-shield", shield, shield_rush_fixture),
                )
                if enabled
            ),
            "owners": foundation.resources.owners
            + (foundation.resources.owners[0].model_copy(update={"actor_id": "c"}),),
            "pools": tuple(
                p.model_copy(update={"current": subject_fp}) if p.id == "fp:b" else p
                for p in foundation.resources.pools
            )
            + tuple(
                p.model_copy(update={"id": p.id.split(":")[0] + ":c"})
                for p in foundation.resources.pools
                if p.id in ("hp:a", "fp:a")
            ),
        }
    )
    initial = campaign(engine)
    producer_draft = foundation.actors[0].proposal.draft
    producer_draft = producer_draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": producer_iq})
                if p.definition_id == "attribute:iq" and producer_iq is not None
                else p
                for p in producer_draft.purchases
            )
            + extra_purchases
            + ((Purchase(definition_id="skill:broadsword", amount=12),) if combat_weapons else ())
            + (
                (Purchase(definition_id="skill:thrown-weapon-axe-mace", amount=8),)
                if ranged_weapon
                else ()
            )
            + (
                (Purchase(definition_id="skill:shield-standard", amount=8),)
                if shield_rush_fixture
                else ()
            )
        }
    )
    assert compiler.compile(producer_draft).legal
    subject_draft = foundation.actors[1].proposal.draft
    subject_draft = subject_draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": subject_st})
                if p.definition_id == "attribute:st"
                else p
                for p in subject_draft.purchases
            )
        }
    )
    if subject_ht is not None or subject_dx is not None or subject_purchases:
        subject_draft = subject_draft.model_copy(
            update={
                "purchases": tuple(
                    p.model_copy(update={"amount": subject_ht})
                    if p.definition_id == "attribute:ht" and subject_ht is not None
                    else p.model_copy(update={"amount": subject_dx})
                    if p.definition_id == "attribute:dx" and subject_dx is not None
                    else p
                    for p in subject_draft.purchases
                )
                + subject_purchases
            }
        )
    if combat_weapons:
        subject_draft = subject_draft.model_copy(
            update={
                "purchases": subject_draft.purchases
                + (Purchase(definition_id="skill:broadsword", amount=12),)
            }
        )
    if not subject_mage:
        subject_draft = subject_draft.model_copy(
            update={
                "purchases": tuple(
                    p
                    for p in subject_draft.purchases
                    if not p.definition_id.startswith("spell:")
                    and p.definition_id not in {"trait:magery", "trait:magery-0"}
                )
            }
        )
    assert compiler.compile(subject_draft).legal
    state = play.initial_state(
        initial,
        world,
        scenario,
        (
            *(
                ActorSetup(
                    actor_id=a.actor_id,
                    proposal=a.proposal.model_copy(update={"draft": producer_draft})
                    if a.actor_id == "a"
                    else a.proposal.model_copy(update={"draft": subject_draft})
                    if a.actor_id == "b"
                    else a.proposal,
                    body=a.body,
                    held_item_hands=(
                        (("sword-" + a.actor_id, "right-hand"),) if combat_weapons else ()
                    )
                    + (
                        (("hatchet-a", "right-hand"),)
                        if ranged_weapon and a.actor_id == "a"
                        else ()
                    )
                    + (
                        (("rush-shield", "left-hand"),)
                        if shield_rush_fixture and a.actor_id == "a"
                        else ()
                    ),
                    aware_of=("b",)
                    if a.actor_id == "a"
                    else ("a",)
                    if bidirectional_visibility and a.actor_id == "b"
                    else (),
                )
                for a in foundation.actors
            ),
            ActorSetup(
                actor_id="c",
                proposal=CharacterProposal(draft=draft),
                body=foundation.actors[0].body,
            ),
        ),
        members=foundation.members
        + (CampaignMember(principal_id="cora", role="player", actor_ids=("c",)),),
    )
    if subject_fp != 10 or subject_hp is not None:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": subject_fp})
                            if p.id == "fp:b"
                            else p.model_copy(update={"current": subject_hp})
                            if p.id == "hp:b" and subject_hp is not None
                            else p
                            for p in state.resources.pools
                        )
                    }
                )
            }
        )
    initial["play_json"] = state.model_dump_json()
    initial = await seed_campaign(play.store, initial)
    await HasteService(play).execute(
        initial["id"],
        ObserveHasteMana(
            id="mana",
            actor_id="gm",
            expected_revision=0,
            environment=HasteMana(location_id="dock", mana="normal"),
        ),
        principal_id="gm",
    )
    return initial["id"], play, initial


async def observe(play: PlayService, cid: str, *, target: str = "b") -> None:
    from wayfarer.engine.simulation.magic.rooted_feet_state import (
        ObserveRootedFeetSubject,
        RootedFeetSubject,
    )
    from wayfarer.orchestration.rooted_feet import RootedFeetService

    await RootedFeetService(play).execute(
        cid,
        ObserveRootedFeetSubject(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=RootedFeetSubject(
                id="subject",
                caster_id="c",
                target_id=target,
                touching=True,
                visible=True,
                standing_living_human=True,
                hostile_resistance=True,
            ),
        ),
        principal_id="gm",
    )


async def cast(play: PlayService, cid: str, *, dice: tuple[int, ...] = (3, 3, 3, 6, 6, 6)) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet
    from wayfarer.orchestration.rooted_feet import RootedFeetService

    await observe(play, cid)
    play.rng = RecordedDice(dice)
    await RootedFeetService(play).execute(
        cid,
        CastRootedFeet(
            id="root",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="root",
            subject_id="subject",
        ),
        principal_id="cora",
    )
