"""Source-shaped approved B173/B248 actors and actual spell lifecycle helpers."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

from test_abilities import resources, world
from test_actions import campaign
from test_default_knowledge_hosts import adopt
from test_statistics import gurps_draft, profile_compiler, profile_package

from scripts.replay_fixtures import FixtureExecutor
from support.runtime import build_play, played, seed_campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.magic.healing import package as healing_package
from wayfarer.engine.rules.skills.mundane.medicine import definitions
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, CheckRule, Wait
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.magic.bindings import SpellChannel, SpellRules
from wayfarer.engine.simulation.magic.spell_state import SpellEvent, SpellResult, event_id
from wayfarer.engine.simulation.magic.spells import PROFILE, SpellCommand
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.orchestration.combat import CombatService, StartEncounter
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import verify_commands

Doctor = Literal["default", "point", "trained", "low", "unrelated"]
Healing = Literal["minor-healing", "major-healing"]


async def fixture(
    path: Path, backend: str, doctor: Doctor, *, combat: bool = False, peer: bool = False
) -> tuple[str, PlayService]:
    medical = tuple(
        d
        for d in definitions()
        if d.id in {"skill:first-aid", "skill:physician", "skill:veterinary"}
    )
    package = profile_package(PROFILE, *healing_package().definitions, *medical)
    base = profile_compiler(PROFILE)
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),)),
        replace(base.policy, allow_supernatural=True, technology_level=8, point_budget=200),
        statistics_profile=PROFILE,
    )
    purchases = (
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=3),
        *(
            Purchase(definition_id="spell:" + key, amount=4)
            for key in ("lend-energy", "lend-vitality", "minor-healing", "major-healing")
        ),
    )
    if doctor in {"default", "point", "unrelated"}:
        purchases += (Purchase(definition_id="skill:veterinary", amount=44, technology_level=8),)
    if doctor in {"point", "trained", "low"}:
        purchases += (
            Purchase(
                definition_id="skill:physician",
                amount={"point": 1, "trained": 24, "low": 20}[doctor],
                technology_level=8,
            ),
        )
    if doctor == "unrelated":
        purchases += (Purchase(definition_id="skill:first-aid", amount=1, technology_level=8),)
    rules = ActionRules(
        id="healing-physician",
        version=1,
        fatigue_cost=0,
        maximum_wait=10000,
        checks=(
            CheckRule(
                id="ordinary-physician",
                action="inspect",
                target_id="b",
                definition_id="skill:physician",
                package_id=package.id,
                package_version=package.version,
                reveal_fact_ids=("presence",),
            ),
        ),
        combat=CombatRules(
            id="healing-arena",
            version=1,
            battlefields=(Battlefield(id="room", location_id="room", width=5, height=5),),
            gurps_equipment=EquipmentCatalog(profile_id=PROFILE, entries=()),
        )
        if combat
        else None,
        spells=SpellRules(
            id="healing",
            version=1,
            execution_version=2,
            channels=tuple(
                SpellChannel(
                    id=key + suffix,
                    actor_id=actor,
                    target_id=target,
                    location_id="room",
                    spell_id=key,
                )
                for key in ("minor-healing", "major-healing")
                for suffix, actor, target in (
                    ("", "a", "b"),
                    ("-self-a", "a", "a"),
                    ("-self-b", "b", "b"),
                )
            ),
        ),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="power", version=1), frozenset({"gm"})),
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        rules,
    )
    play = build_play(
        path, engine, backend=backend, seeds=lambda: f"{540:064x}"
    )  # First three dice:6,6,6.
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world(),
        resources(),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=gurps_draft(*purchases)),
                body=HumanBody(anatomy="human"),
            ),
            ActorSetup(
                actor_id="b",
                proposal=CharacterProposal(draft=gurps_draft(*(purchases if peer else ()))),
                body=HumanBody(anatomy="human"),
            ),
        ),
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 1}) if p.id == "hp:b" else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    await adopt(play, initial["id"], ("skill:physician",))
    if combat:
        await CombatService(play).execute(
            initial["id"],
            StartEncounter(
                id="fight",
                actor_id="gm",
                expected_revision=await revision(play, initial["id"]),
                encounter_id="fight",
                battlefield_id="room",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=2, y=1)),
                ),
            ),
            principal_id="gm",
        )
    return initial["id"], play


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def start(
    play: PlayService,
    cid: str,
    spell: Healing,
    cast_id: str = "cast",
    *,
    actor_id: str = "a",
    suffix: str = "",
) -> SpellCommand:
    command = SpellCommand(
        id=cast_id + ":start",
        actor_id=actor_id,
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id=spell,
        cast_id=cast_id,
        channel_id=spell + suffix,
        energy=2,
    )
    await SpellService(play).execute(cid, command, principal_id=actor_id)
    return command


async def complete(play: PlayService, cid: str, command: SpellCommand) -> SpellResult:
    await play.execute(
        cid,
        Wait(
            id=command.cast_id + ":wait",
            actor_id=command.actor_id,
            expected_revision=await revision(play, cid),
            ticks=1,
        ),
        principal_id=command.actor_id,
    )
    finish = command.model_copy(
        update={
            "id": command.cast_id + ":finish",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    await SpellService(play).execute(cid, finish, principal_id=command.actor_id)
    state = play._load(await play.store.read(cid))
    result = SpellEvent.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == event_id(finish.id))
    ).result
    assert await SpellService(play).execute(cid, finish, principal_id=command.actor_id)
    return result


async def reexecute(play: PlayService, cid: str, path: Path) -> None:
    initial = (await play.store.history(cid))[0].state_after
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, path),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert (
        document(replayed)
        == document(await play.store.read(cid))
        == document(await play.store.replay(cid))
    )
