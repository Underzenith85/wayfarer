"""Minimal original-genesis learned Deathtouch and genuinely empty hands; no Staff project."""

import secrets
from dataclasses import replace
from pathlib import Path
from typing import Literal

from test_actions import campaign, world
from test_statistics import gurps_draft, profile_compiler, profile_package

from support.runtime import build_play, build_runtime, seed_campaign
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.magic.body_control import package as body_package
from wayfarer.engine.rules.magic.movement import package as movement_package
from wayfarer.engine.rules.skills.mundane.melee import definitions as melee_definitions
from wayfarer.engine.rules.skills.mundane.ranged import definitions as ranged_definitions
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.battlefield import Battlefield
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.equipment.basic.armor import ARMOR
from wayfarer.engine.simulation.equipment.basic.melee import WEAPONS
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
from wayfarer.engine.simulation.magic.melee_spell_state import ObserveMeleeMana
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, Owner, ResourceState
from wayfarer.engine.world import Fact
from wayfarer.orchestration.play import PlayService

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
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
    defender: Literal["unarmed", "staff", "armor", "knife"] = "unarmed",
    spectator: bool = False,
    spell_skill: int = 16,
    missing_prerequisite: str | None = None,
    magery: int = 2,
    haste_route: bool = False,
) -> tuple[str, PlayService, Campaign]:
    body = body_package()
    staff = next(e for e in WEAPONS if e.definition_id == "equipment:quarterstaff")
    armor = next(e for e in ARMOR if e.definition_id == "equipment:leather-armor")
    knife = next(e for e in WEAPONS if e.definition_id == "equipment:large-knife")
    profiles = (staff, armor) + ((knife,) if defender == "knife" else ())
    equipment = EquipmentCatalog(profile_id=PROFILE, entries=profiles)
    definitions = (
        tuple(body.definitions)
        + (
            tuple(d for d in movement_package().definitions if d.id == "spell:haste")
            if haste_route
            else ()
        )
        + tuple(
            d
            for d in melee_definitions()
            if d.id in ("skill:brawling", "skill:staff", "skill:two-handed-sword")
        )
        + tuple(
            RuleDefinition(
                e.definition_id,
                DefinitionKind.EQUIPMENT,
                name,
                "sjg:basic-set-characters-4e-2004",
                int(e.price or 0),
                ImplementationStatus.IMPLEMENTED,
            )
            for e, name in ((staff, "Quarterstaff"), (armor, "Leather Armor"))
        )
    )
    if defender == "knife":
        definitions += tuple(
            d
            for d in (*melee_definitions(), *ranged_definitions())
            if d.id in ("skill:knife", "skill:thrown-weapon-knife")
        ) + (
            RuleDefinition(
                knife.definition_id,
                DefinitionKind.EQUIPMENT,
                "Large Knife",
                "sjg:basic-set-characters-4e-2004",
                int(knife.price or 0),
                ImplementationStatus.IMPLEMENTED,
            ),
        )
    package = profile_package(PROFILE, *definitions)
    package = replace(
        package,
        sources=tuple(
            {
                s.id: s
                for s in (
                    *package.sources,
                    *body.sources,
                    *(movement_package().sources if haste_route else ()),
                )
            }.values()
        ),
    )
    baseline = profile_compiler(PROFILE, package=package)
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        baseline.rules,
        replace(
            baseline.policy,
            point_budget=1000,
            skill_ceiling=40,
            allow_supernatural=True,
            allowed_equipment=frozenset(e.definition_id for e in equipment.entries),
        ),
        statistics_profile=PROFILE,
    )
    authored = world()
    authored = replace(
        authored,
        facts=authored.facts
        + (Fact("visible-b", "b", "visible", "yes"), Fact("visible-a", "a", "visible", "yes")),
        knowledge=(("a", "visible-b"), ("b", "visible-a")),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="hand-deathtouch", version=1), frozenset({"gm"})),
        ResourceEngine(
            authored,
            catalog,
            compiler.rules,
            compiler.policy,
            tuple(e.inventory_spec() for e in equipment.entries),
        ),
        ActionRules(
            id="hand-deathtouch",
            version=1,
            maximum_wait=10000,
            combat=CombatRules(
                id="hand-deathtouch",
                version=1,
                gurps_equipment=equipment,
                battlefields=(
                    Battlefield(id="dock-field", location_id="dock", width=10, height=10),
                ),
            ),
        ),
    )
    play = build_play(path, engine, backend=backend, rng=secrets)
    play.seeds = lambda: f"{1:064x}"
    caster = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=magery),
        *(
            Purchase(definition_id="spell:" + key)
            for key in (
                (*tuple(s for s in CHAIN if s != "clumsiness"), "haste", "rooted-feet")
                if haste_route
                else CHAIN
            )
            if key != missing_prerequisite
        ),
        Purchase(definition_id="skill:brawling", amount=8),
    )
    caster = caster.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": spell_skill})
                if p.definition_id == "attribute:iq"
                else p
                for p in caster.purchases
            )
        }
    )
    compiled = compiler.compile(caster)
    if not compiled.legal:
        raise ValueError(
            "Illegal original hand Deathtouch construction: " + str(compiled.diagnostics)
        )
    assert compiled.build is not None
    assert (
        next(int(s.value) for s in compiled.build.sheet.values if s.target == "spell:deathtouch")
        == spell_skill
    )
    defender_draft = gurps_draft(
        Purchase(definition_id="skill:brawling", amount=8),
        *(
            (Purchase(definition_id="skill:staff", amount=8),)
            if defender == "staff"
            else (Purchase(definition_id="skill:knife", amount=8),)
            if defender == "knife"
            else ()
        ),
    )
    items = (
        ()
        if defender == "unarmed"
        else (
            Item(
                id="defender-implement",
                owner_id="b",
                definition_id=staff.definition_id
                if defender == "staff"
                else knife.definition_id
                if defender == "knife"
                else armor.definition_id,
                equipped=True,
                ready=True,
                condition=ObjectCondition(hp=staff.durability.hp)
                if defender == "staff" and staff.durability
                else ObjectCondition(hp=knife.durability.hp)
                if defender == "knife" and knife.durability
                else None,
            ),
        )
    )
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        authored,
        ResourceState(
            items=items,
            owners=(Owner(actor_id="a", capacity=100000), Owner(actor_id="b", capacity=100000)),
        ),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=caster),
                body=HumanBody(anatomy="human"),
                aware_of=("b",),
            ),
            ActorSetup(
                actor_id="b",
                proposal=CharacterProposal(draft=defender_draft),
                body=HumanBody(anatomy="human"),
                aware_of=("a",),
                held_item_hands=(
                    ("defender-implement", "right-hand"),
                    ("defender-implement", "left-hand"),
                )
                if defender == "staff"
                else (("defender-implement", "right-hand"),)
                if defender == "knife"
                else (),
            ),
        ),
        members=(
            CampaignMember(principal_id="gm", role="gm"),
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            *((CampaignMember(principal_id="watcher", role="spectator"),) if spectator else ()),
        ),
    )
    initial["play_json"] = state.model_dump_json()
    initial = await seed_campaign(play.store, initial)
    await build_runtime(play).submit_json(
        initial["id"],
        ObserveMeleeMana(
            id="mana", actor_id="gm", expected_revision=0, location_id="dock"
        ).model_dump(mode="json"),
        principal_id="gm",
    )
    return initial["id"], play, initial


async def cast(
    play: PlayService,
    cid: str,
    *,
    energy: int = 3,
    hand: Literal["left-hand", "right-hand"] = "right-hand",
) -> None:
    from wayfarer.engine.simulation.magic.hand_melee_spell_state import CastHandDeathtouch
    from wayfarer.engine.simulation.magic.melee_spell_state import HandCarrier

    for operation in ("start", "concentrate", "complete"):
        command = CastHandDeathtouch(
            id="hand-" + operation,
            actor_id="a",
            expected_revision=await revision(play, cid),
            operation=operation,
            cast_id="hand",
            energy=energy,
            carrier=HandCarrier(hand=hand),
        )
        await build_runtime(play).submit_json(
            cid, command.model_dump(mode="json"), principal_id="alice"
        )
