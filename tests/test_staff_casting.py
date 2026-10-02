"""Actual B239-240 Staff targeting, current authority and exact roll consequences."""

import asyncio
import secrets
from copy import copy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_enchanting_projects import setup
from test_spell_bindings import draft
from test_statistics import profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.protocols import MagicItemInstance, ManaLevel
from wayfarer.engine.rules.skills.mundane.melee import definitions as melee_definitions
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.basic.melee import WEAPONS
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
from wayfarer.engine.simulation.magic.bindings import SpellChannel, SpellRules
from wayfarer.engine.simulation.magic.effects import dazed
from wayfarer.engine.simulation.magic.spell_transitions import (
    SpellExecutionContext,
    approved_context,
    reduce_spell,
)
from wayfarer.engine.simulation.magic.spells import PROFILE, SpellCommand, latest
from wayfarer.engine.simulation.magic.staff_casting import (
    touching,
)
from wayfarer.engine.simulation.magic.staff_casting_state import (
    DeclareStaffIntent,
    ObserveStaffTouch,
    checkpoint,
    intents,
    invalidated,
    observations,
)
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.engine.world import Fact
from wayfarer.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.staff_casting import StaffCastingService


async def prepare(
    path: Path,
    backend: str = "sqlite",
    *,
    length: Fraction = Fraction(2),
    distance: int = 5,
    pointing: bool = True,
    enchanted: bool = True,
    visible: bool = True,
    combat: bool = False,
    spell: str = "light",
    power: int = 20,
    mana: ManaLevel = "normal",
    seeded: bool = False,
) -> tuple[str, PlayService, SpellCommand]:
    original, _, foundation = setup(path, hold_target=True)
    profile = next(p for p in WEAPONS if p.definition_id == "equipment:quarterstaff").model_copy(
        update={
            "definition_id": "equipment:sword",
            "durability": ObjectProfile(construction="homogenous", hp=12, dr=2, ht=12),
        }
    )
    assert profile.durability
    source_package = profile_package(PROFILE)
    package = replace(
        source_package,
        definitions=tuple(original.reviewer.compiler.definitions.values())
        + tuple(
            d for d in melee_definitions() if d.id in ("skill:staff", "skill:two-handed-sword")
        ),
        sources=tuple(
            {s.id: s for s in (*source_package.sources, *enchantment_package().sources)}.values()
        ),
    )
    base = profile_compiler(PROFILE, package=package)
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        base.rules,
        original.reviewer.compiler.policy,
        statistics_profile=PROFILE,
    )
    reviewer = PowerReviewer(compiler, original.reviewer.policy, original.reviewer.gm_ids)
    resources = copy(original.resources)
    resources.rules = compiler.rules
    resources.specs = {profile.definition_id: profile.inventory_spec()}
    engine = ActionEngine(
        reviewer,
        resources,
        original.rules.model_copy(
            update={
                "enchanting": None,
                "spells": SpellRules(
                    id="staff-test",
                    version=1,
                    execution_version=2,
                    channels=tuple(
                        SpellChannel(
                            id=key,
                            actor_id="a",
                            target_id="b",
                            location_id="forge",
                            spell_id=key,
                            distance_yards=distance,
                            mana=mana,
                        )
                        for key in ("light", "daze", "create-fire")
                    ),
                ),
                "combat": CombatRules(
                    id="staff-test",
                    version=1,
                    battlefields=(
                        Battlefield(id="forge", location_id="forge", width=10, height=10),
                    ),
                    gurps_equipment=EquipmentCatalog(profile_id=PROFILE, entries=(profile,)),
                )
                if combat
                else None,
            }
        ),
    )
    item = foundation.resources.items[0]
    staff = MagicItemInstance(
        id="completed-staff",
        item_id="blade",
        spell_id="staff",
        power=power,
        requires_magery=True,
        always_on=True,
        project_id="completed-project",
        recipe_id="staff",
        effect_id="effect:staff",
        activation="always-on",
        runtime_family="staff",
        owner_id="a",
        created_at=0,
        method="slow-and-sure",
    )
    item = item.model_copy(
        update={
            "enchantments": (staff,) if enchanted else (),
            "condition": ObjectCondition(hp=profile.durability.hp),
        }
    )
    play = build_play(path, engine, backend=backend, rng=secrets if seeded else RecordedDice(()))
    initial = campaign(engine)
    world = replace(
        foundation.world,
        facts=(Fact("visible-b", "b", "visible", "yes"),) if visible else (),
        knowledge=(("a", "visible-b"),) if visible else (),
    )
    state = play.initial_state(
        initial,
        world,
        foundation.resources.model_copy(
            update={
                "items": (item,),
                "owners": tuple(
                    o.model_copy(update={"capacity": 100000}) for o in foundation.resources.owners
                ),
            }
        ),
        tuple(
            ActorSetup(
                actor_id=a.actor_id,
                proposal=a.proposal.model_copy(update={"draft": draft()}),
                held_item_hands=(("blade", "right-hand"),) if a.actor_id == "a" else (),
            )
            for a in foundation.actors
        ),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    if combat:
        await CombatService(play).execute(
            cid,
            StartEncounter(
                id="fight-start",
                actor_id="gm",
                expected_revision=0,
                encounter_id="fight",
                battlefield_id="forge",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=1 + distance, y=1)),
                ),
            ),
            principal_id="gm",
        )
    revision = (await play.store.read(cid))["revision"]
    await EnchantmentService(play).declare_staff(
        cid,
        DeclareStaffConstruction(
            id="staff-construction",
            actor_id="a",
            expected_revision=revision,
            construction=StaffConstruction(
                item_id="blade",
                definition_id="equipment:sword",
                form="full-staff",
                material="wood",
                once_living=True,
                length_yards=length,
            ),
        ),
        principal_id="gm",
    )
    await StaffCastingService(play).execute(
        cid,
        DeclareStaffIntent.model_validate(
            dict(
                id="staff-intent",
                actor_id="a",
                expected_revision=revision + 1,
                cast_id="cast",
                spell_id=spell,
                channel_id=spell,
                item_id="blade",
                pointing=pointing,
            )
        ),
        principal_id="a",
    )
    command = SpellCommand.model_validate(
        dict(
            id="cast-start",
            actor_id="a",
            expected_revision=revision + 2,
            cast_id="cast",
            spell_id=spell,
            channel_id=spell,
            kind="start",
        )
    )
    return cid, play, command


async def finish(cid: str, play: PlayService, command: SpellCommand) -> object:
    await SpellService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)[command.cast_id]
    while state.resources.game_time < effect.ready_at:
        await play.execute(
            cid,
            Wait(
                id=f"wait-{state.revision}", actor_id="a", expected_revision=state.revision, ticks=1
            ),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        if state.resources.game_time < effect.ready_at:
            await SpellService(play).execute(
                cid,
                command.model_copy(
                    update={
                        "id": f"concentrate-{state.revision}",
                        "kind": "concentrate",
                        "expected_revision": state.revision,
                    }
                ),
                principal_id="a",
            )
            state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    return await SpellService(play).execute(
        cid,
        command.model_copy(
            update={"id": "cast-complete", "kind": "complete", "expected_revision": state.revision}
        ),
        principal_id="a",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("length", "pointing", "enchanted", "target"),
    [
        (Fraction(2), True, True, 11),
        (Fraction(1), True, True, 10),
        (Fraction(1, 2), True, True, 9),
        (Fraction(2), False, True, 9),
        (Fraction(2), True, False, 9),
    ],
)
async def test_actual_personal_roll_uses_exact_staff_length(
    tmp_path: Path, backend: str, length: Fraction, pointing: bool, enchanted: bool, target: int
) -> None:
    cid, play, command = await prepare(
        tmp_path, backend, length=length, pointing=pointing, enchanted=enchanted
    )
    await finish(cid, play, command)
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["cast"]
    assert (effect.skill, effect.cost, effect.maintenance, effect.phase) == (target, 1, 1, "active")
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pointing_is_bound_before_start_and_touch_requires_current_gm(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend, distance=1)
    service = StaffCastingService(play)
    observe = ObserveStaffTouch(id="touch", actor_id="a", expected_revision=2, cast_id="cast")
    before = await play.store.read(cid)
    for principal in ("a", "b"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await service.execute(cid, observe, principal_id=principal)
    with pytest.raises(ValueError):
        DeclareStaffIntent.model_validate(
            dict(
                id="forged",
                actor_id="a",
                expected_revision=2,
                cast_id="fake",
                spell_id="light",
                channel_id="light",
                item_id="blade",
                touching=True,
            )
        )
    assert await play.store.read(cid) == before
    results = await asyncio.gather(
        *(service.execute(cid, observe, principal_id="gm") for _ in range(3))
    )
    assert all(r == results[0] for r in results)
    assert len(observations(play._load(await play.store.read(cid)).resources)) == 1
    with pytest.raises(ConflictError):
        await service.execute(
            cid, observe.model_copy(update={"id": "stale-touch"}), principal_id="gm"
        )
    with pytest.raises(ConflictError, match="replaced"):
        await service.execute(
            cid,
            DeclareStaffIntent(
                id="switch",
                actor_id="a",
                expected_revision=3,
                cast_id="cast",
                spell_id="light",
                channel_id="light",
                item_id="blade",
            ),
            principal_id="a",
        )
    await SpellService(play).execute(
        cid, command.model_copy(update={"expected_revision": 3}), principal_id="a"
    )
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["cast"].skill == 14
    # The accepted start pins the channel even if another channel has the same actor/target.
    with pytest.raises(ConflictError, match="bound"):
        approved_context(
            play.rules_context, state, command.model_copy(update={"channel_id": "daze"})
        )
    # Current authority is evaluated before a duplicate receipt is disclosed.
    revoked = state.model_copy(
        update={"members": tuple(m for m in state.members if m.principal_id != "gm")}
    )
    plan = service.plan(play, state, observe, principal_id="gm")
    from wayfarer.orchestration.pipeline import authorized

    with pytest.raises((AuthorizationError, ValidationError, NotFoundError)):
        authorized(plan, "gm", revoked)
    assert not touching(play.rules_context, revoked, intents(state.resources)[0])
    expired = checkpoint(revoked, before=state)
    restored = expired.model_copy(update={"members": state.members})
    assert not touching(play.rules_context, restored, intents(state.resources)[0])


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unseen_trusted_contact_persists_through_stationary_cast(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(
        tmp_path, backend, distance=1, visible=False, pointing=False, spell="daze"
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="not perceived"):
        await SpellService(play).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before
    await StaffCastingService(play).execute(
        cid,
        ObserveStaffTouch(id="touch", actor_id="a", expected_revision=2, cast_id="cast"),
        principal_id="gm",
    )
    await finish(cid, play, command.model_copy(update={"expected_revision": 3}))
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["cast"].skill == 14
    assert dazed(state.resources, "b")
    assert len(observations(state.resources)) == 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_combat_completion_refreshes_target_distance(tmp_path: Path, backend: str) -> None:
    cid, play, command = await prepare(tmp_path, backend, distance=2, combat=True, spell="daze")
    await SpellService(play).execute(cid, command, principal_id="a")
    started = play._load(await play.store.read(cid))
    original = latest(started.resources)["cast"]
    assert original.skill == 14
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="move-target",
            actor_id="b",
            expected_revision=started.revision,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=6, y=1),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    result = await SpellService(play).execute(
        cid,
        command.model_copy(
            update={
                "id": "last-concentration",
                "kind": "concentrate",
                "expected_revision": state.revision,
            }
        ),
        principal_id="a",
    )
    after = play._load(await play.store.read(cid))
    effect = latest(after.resources)["cast"]
    assert result.outcome == "active" and result.energy_spent == 3
    assert effect.skill == 11
    assert (effect.cost, effect.hp_energy, effect.required_turns) == (
        original.cost,
        original.hp_energy,
        original.required_turns,
    )
    assert effect.position == (6, 1)
    assert dazed(after.resources, "b")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_staff_loss_before_roll_removes_benefit_without_ending_personal_effect(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend)
    await SpellService(play).execute(cid, command, principal_id="a")
    before = play._load(await play.store.read(cid))
    item = before.resources.items[0]
    lost = before.model_copy(
        update={
            "resources": before.resources.model_copy(
                update={
                    "items": (
                        item.model_copy(update={"ready": False}),
                        *before.resources.items[1:],
                    ),
                    "game_time": 1,
                }
            )
        }
    )
    # Pure reducer injection isolates loss between accepted start and exact roll time.
    runtime = replace(play.rules_context, rng=RecordedDice((3, 3, 3)))
    completed, result = reduce_spell(
        lost,
        command.model_copy(
            update={"id": "complete", "kind": "complete", "expected_revision": lost.revision}
        ),
        SpellExecutionContext(runtime),
    )
    effect = latest(completed.resources)["cast"]
    assert result.energy_spent == 1 and effect.skill == 9 and effect.phase == "active"
    assert effect.cost == 1 and effect.ready_at == 1
    moved_world = replace(
        completed.world,
        entities=tuple(
            replace(e, location_id="elsewhere") if e.id in ("a", "b") else e
            for e in completed.world.entities
        )
        + (replace(completed.world.entities[0], id="elsewhere", name="Elsewhere"),),
    )
    maintenance_state = completed.model_copy(
        update={
            "world": moved_world,
            "resources": completed.resources.model_copy(update={"game_time": effect.expires_at}),
        }
    )
    maintained, result = reduce_spell(
        maintenance_state,
        command.model_copy(
            update={
                "id": "maintain",
                "kind": "maintain",
                "expected_revision": maintenance_state.revision,
            }
        ),
        SpellExecutionContext(runtime),
    )
    assert result.energy_spent == 1 and latest(maintained.resources)["cast"].phase == "active"
    cancelled, result = reduce_spell(
        maintained,
        command.model_copy(
            update={"id": "cancel", "kind": "cancel", "expected_revision": maintained.revision}
        ),
        SpellExecutionContext(runtime),
    )
    assert result.outcome == "cancelled" and latest(cancelled.resources)["cast"].phase == "ended"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(("destination", "target"), [(2, 13), (6, 9)])
async def test_plain_regular_cast_also_uses_roll_time_range(
    tmp_path: Path, backend: str, destination: int, target: int
) -> None:
    cid, play, original = await prepare(
        tmp_path, backend, distance=2, combat=True, spell="daze", enchanted=False
    )
    command = original.model_copy(update={"cast_id": "plain"})  # No Staff intention for this cast.
    await SpellService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["plain"].skill == 12
    with pytest.raises(ConflictError, match="before the accepted"):
        await StaffCastingService(play).execute(
            cid,
            DeclareStaffIntent(
                id="late",
                actor_id="a",
                expected_revision=state.revision,
                cast_id="plain",
                spell_id="daze",
                channel_id="daze",
                item_id="blade",
                pointing=True,
            ),
            principal_id="a",
        )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="move-target",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=destination, y=1),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    result = await SpellService(play).execute(
        cid,
        command.model_copy(
            update={"id": "finish", "kind": "concentrate", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    assert result.checks[0].effective_target == target
    assert result.energy_spent == 3


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_contact_does_not_resurrect_after_custody_returns(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend, distance=1, pointing=False)
    await StaffCastingService(play).execute(
        cid,
        ObserveStaffTouch(id="contact", actor_id="a", expected_revision=2, cast_id="cast"),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    intent = intents(state.resources)[0]
    assert touching(play.rules_context, state, intent)
    released = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"held_item_hands": ()}) if a.actor_id == "a" else a
                for a in state.actors
            )
        }
    )
    assert approved_context(play.rules_context, released, command).distance == 1
    retired = checkpoint(released, before=state)
    returned = retired.model_copy(update={"actors": state.actors})
    assert not touching(play.rules_context, returned, intent)
    assert approved_context(play.rules_context, returned, command).distance == 1
    assert invalidated(returned.resources) == {"contact"}
    stationary = state.model_copy(
        update={"resources": state.resources.model_copy(update={"game_time": 1})}
    )
    assert touching(play.rules_context, checkpoint(stationary, before=state), intent)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "change",
    ["unready", "unequipped", "not-held", "contained", "transferred", "broken", "low-power"],
)
async def test_current_physical_eligibility_is_required(
    tmp_path: Path, backend: str, change: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    item = state.resources.items[0]
    possibilities: dict[str, dict[str, object]] = {
        "unready": {"ready": False},
        "unequipped": {"ready": False, "equipped": False},
        "contained": {"container_id": "forge-tools"},
        "transferred": {"owner_id": "b"},
        "broken": {"condition": ObjectCondition(hp=0, disabled=True)},
        "low-power": {"enchantments": (item.enchantments[0].model_copy(update={"power": 14}),)},
        "not-held": {},
    }
    changes = possibilities[change]
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"items": (item.model_copy(update=changes), *state.resources.items[1:])}
            )
        }
    )
    if change == "not-held":
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"held_item_hands": ()}) if a.actor_id == "a" else a
                    for a in state.actors
                )
            }
        )
    assert approved_context(play.rules_context, state, command).distance == 5


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_staff_requires_explicit_area_and_rejects_missile_casting(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await prepare(tmp_path, backend)
    before = await play.store.read(cid)
    for spell in ("create-fire", "fireball"):
        with pytest.raises(ValidationError, match="Regular"):
            await StaffCastingService(play).execute(
                cid,
                DeclareStaffIntent.model_validate(
                    dict(
                        id="bad-" + spell,
                        actor_id="a",
                        expected_revision=2,
                        cast_id=spell,
                        spell_id=spell,
                        channel_id=spell,
                        item_id="blade",
                        pointing=True,
                    )
                ),
                principal_id="a",
            )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_staff_power_magery_and_permanent_loss_use_item_lifecycle(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.item_state import checkpoint as item_checkpoint
    from wayfarer.engine.simulation.magic.staff_casting import usable_staff

    cid, play, command = await prepare(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    intent = intents(state.resources)[0]
    assert usable_staff(state, intent, "normal", -1) is None
    assert usable_staff(state, intent, "none", 2) is None
    assert usable_staff(state, intent, "low", 2) is not None  # Power20 ->15.
    item = state.resources.items[0]
    low = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": (
                        item.model_copy(
                            update={
                                "enchantments": (
                                    item.enchantments[0].model_copy(update={"power": 19}),
                                )
                            }
                        ),
                    )
                }
            )
        }
    )
    assert usable_staff(low, intent, "low", 2) is None
    broken = state.resources.model_copy(
        update={
            "items": (
                item.model_copy(
                    update={"ready": False, "condition": ObjectCondition(hp=0, disabled=True)}
                ),
            )
        }
    )
    permanent = item_checkpoint(broken, before=state.resources)
    repaired = state.model_copy(
        update={"resources": permanent.model_copy(update={"items": state.resources.items})}
    )
    assert approved_context(play.rules_context, repaired, command).distance == 5
    assert usable_staff(repaired, intent, "high", 2) is None
    fresh = item.enchantments[0].model_copy(
        update={"id": "fresh-staff", "project_id": "new-project"}
    )
    reenchanted = repaired.model_copy(
        update={
            "resources": repaired.resources.model_copy(
                update={
                    "items": (
                        item.model_copy(update={"enchantments": item.enchantments + (fresh,)}),
                    )
                }
            )
        }
    )
    assert usable_staff(reenchanted, intent, "normal", 2) is not None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_power_enchantment_does_not_discount_personal_staff_cast(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    item = state.resources.items[0]
    power = item.enchantments[0].model_copy(
        update={
            "id": "completed-power",
            "spell_id": "power",
            "runtime_family": "power",
            "power_reduction": 5,
        }
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": (
                        item.model_copy(update={"enchantments": item.enchantments + (power,)}),
                    )
                }
            )
        }
    )
    runtime = replace(play.rules_context, rng=RecordedDice((3, 3, 3)))
    started, _ = reduce_spell(state, command, SpellExecutionContext(runtime))
    effect = latest(started.resources)["cast"]
    assert (effect.cost, effect.maintenance, effect.ready_at) == (1, 1, 1)
    ready = started.model_copy(
        update={"resources": started.resources.model_copy(update={"game_time": 1})}
    )
    finished, result = reduce_spell(
        ready,
        command.model_copy(
            update={"id": "complete", "kind": "complete", "expected_revision": ready.revision}
        ),
        SpellExecutionContext(runtime),
    )
    assert result.energy_spent == 1
    assert next(p.current for p in finished.resources.pools if p.id == "fp:a") == 9
