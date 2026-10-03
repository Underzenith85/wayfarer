"""B168/B173/B178 defaults resolve into actual B484 durability transactions."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

from test_gurps_melee import setup as melee_setup

from support.runtime import build_play, seed_campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.magic_craft import package as magic_craft_package
from wayfarer.engine.rules.skills.mundane import candidate_package
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import Armor, EquipmentProfile
from wayfarer.engine.simulation.equipment.firearm_repair_profile import TOOLKIT, profile
from wayfarer.engine.simulation.equipment.repair_defaults import (
    DeclareArmouryTraining,
    RepairDefaultSelection,
    SelectRepairDefault,
)
from wayfarer.engine.simulation.resources import Item
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService, EndEncounter
from wayfarer.orchestration.play import PlayService

Kind = Literal["armor", "firearm"]
TARGETS = ("skill:armoury-body-armor", "skill:armoury-melee-weapons", "skill:armoury-small-arms")


def references(definition: RuleDefinition) -> tuple[str, ...]:
    spec = definition.skill
    assert spec is not None
    return tuple(
        str(ref)
        for ref in (*[d.target for d in spec.defaults], *[p.target for p in spec.prerequisites])
        if str(ref).startswith("skill:")
    )


async def fixture(
    path: Path,
    backend: str,
    kind: Kind,
    source: str,
    *,
    tl: int = 4,
    hp: int = 0,
    additional_purchases: tuple[Purchase, ...] = (),
) -> tuple[str, PlayService]:
    canonical_skills = {
        d.id: d
        for d in (*candidate_package().definitions, *magic_craft_package().definitions)
        if d.skill is not None
    }
    needed = set((*TARGETS, "skill:engineer-small-arms", "skill:guns-pistol"))
    while True:
        expanded = needed | {
            str(ref) for key in needed for ref in references(canonical_skills[key])
        }
        if expanded == needed:
            break
        needed = expanded
    definitions = tuple(
        replace(
            d,
            status=ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", "check.target"),
        )
        for key, d in canonical_skills.items()
        if key in needed
    )
    source_provenance = next(
        e.provenance
        for e in BASIC_EQUIPMENT.entries
        if e.definition_id == "equipment:flintlock-pistol-51"
    )
    parts = EquipmentProfile(
        definition_id="equipment:spare-parts",
        provenance=source_provenance,
        weight_millipounds=10,
        price=10,
        technology_level=4,
    )
    if kind == "firearm":
        canonical = next(
            e for e in BASIC_EQUIPMENT.entries if e.definition_id == "equipment:flintlock-pistol-51"
        )
        entry = canonical.model_copy(update={"durability": profile(canonical)})
        tool = next(e for e in BASIC_EQUIPMENT.entries if e.definition_id == TOOLKIT)
    else:
        tool = EquipmentProfile(
            definition_id="equipment:armoury-tools",
            provenance=source_provenance,
            weight_millipounds=1000,
            price=20,
            technology_level=4,
        )
        entry = EquipmentProfile(
            definition_id="equipment:repair-target",
            provenance=source_provenance,
            weight_millipounds=3000,
            price=500,
            technology_level=tl,
            slot="body",
            armor=Armor(locations=("torso",), dr=6),
            durability=ObjectProfile(
                construction="homogenous",
                hp=12,
                dr=6,
                ht=12,
                repair_skill_id="skill:armoury-body-armor",
                repair_tools_definition=tool.definition_id,
                repair_parts_definition=parts.definition_id,
            ),
        )
    ammunition = (
        tuple(
            e
            for e in BASIC_EQUIPMENT.entries
            if e.definition_id == "equipment:flintlock-pistol-51-round"
        )
        if kind == "firearm"
        else ()
    )
    purchases = (
        ()
        if source == "attribute:iq"
        else (
            Purchase(
                definition_id=source,
                amount=20 if source == "skill:engineer-small-arms" else 4,
                technology_level=4,
            ),
        )
    )
    cid, initial_play = await melee_setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        campaign_technology_level=max(tl, 4),
        free_defender_hand=True,
        extra_definitions=definitions,
        extra_purchases=purchases + additional_purchases,
        extra_equipment=(entry, tool, parts, *ammunition),
        preserve_extra_equipment=True,
        extra_items=(
            Item(
                id="repair-target",
                owner_id="b",
                definition_id=entry.definition_id,
                condition=ObjectCondition(hp=hp, disabled=hp <= 0),
            ),
            Item(id="tool-b", owner_id="b", definition_id=tool.definition_id),
            Item(id="parts-b", owner_id="b", definition_id=parts.definition_id, quantity=30),
            *(
                Item(id="ammo-b", owner_id="b", definition_id=e.definition_id, quantity=1)
                for e in ammunition
            ),
        ),
    )
    await CombatService(initial_play).execute(
        cid,
        EndEncounter(
            id="end", actor_id="gm", expected_revision=1, encounter_id="fight", reason="workshop"
        ),
        principal_id="gm",
    )
    initial = await initial_play.store.read(cid)
    play = build_play(path / "actual", initial_play.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, initial)
    return cid, play


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def declare(play: PlayService, cid: str) -> None:
    state = play._load(await play.store.read(cid))
    await ArmouryService(play).execute(
        cid,
        DeclareArmouryTraining(
            id="training",
            actor_id="gm",
            performer_id="b",
            build_revision=build(play.rules_context, state, "b").revision,
            personal_technology_level=4,
            society_known_skills=TARGETS,
            expected_revision=state.revision,
        ),
        principal_id="gm",
    )


async def select(play: PlayService, cid: str, source: str) -> RepairDefaultSelection:
    return await ArmouryService(play).execute(
        cid,
        SelectRepairDefault(
            id="default",
            actor_id="b",
            item_id="repair-target",
            start_command_id="repair",
            source_id=source,
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )


async def wait(play: PlayService, cid: str, seconds: int) -> None:
    for n in range(0, seconds, 100):
        await play.execute(
            cid,
            Wait(
                id=f"work-{n}",
                actor_id="b",
                ticks=min(100, seconds - n),
                expected_revision=await revision(play, cid),
            ),
            principal_id="b",
        )
