"""A real held B249 missile and distinct current protector/friend state."""

from pathlib import Path

from test_spell_bindings import command, idle, setup
from test_statistics import gurps_draft

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.catalog import LITE_SOURCE, Armor, EquipmentProfile
from wayfarer.orchestration.combat import ChooseDefense, CombatService, StartEncounter
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService


async def pending_fireball(
    path: Path,
    backend: str,
    *,
    luck: bool = False,
    attack_dice: tuple[int, int, int] = (3, 3, 3),
    legacy_release: bool = False,
) -> tuple[str, PlayService, ChooseDefense]:
    from test_opponent_attack_routes import luck_source

    from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS

    definition, purchase = luck_source()
    base = gurps_draft()
    protector = base.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 20 if p.definition_id == "attribute:st" else 8})
                if p.definition_id in {"attribute:st", "attribute:ht"}
                else p
                for p in base.purchases
            )
        }
    )
    armor = EquipmentProfile(
        definition_id="equipment:protector-armor",
        provenance=LITE_SOURCE.model_copy(update={"source_id": "sjg:basic-set-characters-4e-2004"}),
        weight_millipounds=1,
        price=10,
        technology_level=1,
        slot="body",
        armor=Armor(locations=("torso",), dr=4),
    )
    cid, play = await setup(
        path,
        backend=backend,
        combat=True,
        execution_version=2,
        reserve=True,
        reserve_draft=protector,
        human_targets=True,
        equipment=(armor,),
        extra_definitions=(definition,) if luck else (),
        trait_runtime_hooks=SUPPORTED_HOOKS if luck else frozenset(),
        extra_purchases=(Purchase(definition_id="skill:innate-attack-projectile", amount=4),)
        + ((purchase,) if luck else ()),
    )
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="room",
            placements=tuple(
                Placement(actor_id=a, position=GridPoint(x=x, y=1))
                for a, x in (("a", 1), ("b", 2), ("c", 3))
            ),
        ),
        principal_id="gm",
    )
    play.rng = RecordedDice([3, 3, 3])
    start = command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    await SpellService(play).execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    await idle(cid, play, "c")
    state = play._load(await play.store.read(cid))
    release = start.model_copy(
        update={"id": "release", "kind": "release", "expected_revision": state.revision}
    )
    play.rng = RecordedDice(attack_dice)
    if legacy_release:
        from wayfarer.orchestration.membership import member_for
        from wayfarer.orchestration.pipeline import submit

        plan = SpellService(play).plan(
            play,
            member_for(state, "a"),
            release,
            principal_id="a",
            missile_attack=False,
            state=state,
        )
        await submit(play, cid, plan, principal_id="a")
    else:
        await SpellService(play).execute(cid, release, principal_id="a")
    state = play._load(await play.store.read(cid))
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "encounters": (
                state.encounters[0].model_copy(
                    update={
                        "participants": tuple(
                            p.model_copy(
                                update={
                                    "ready_item_ids": ("target-0",) if p.actor_id == "c" else ()
                                }
                            )
                            for p in state.encounters[0].participants
                        )
                    }
                ),
            ),
            "resources": state.resources.model_copy(
                update={
                    "revision": state.revision + 1,
                    "items": tuple(
                        i.model_copy(update={"owner_id": "c"}) for i in state.resources.items
                    ),
                    "pools": tuple(
                        p.model_copy(update={"current": 20, "maximum": 20}) if p.id == "hp:c" else p
                        for p in state.resources.pools
                    ),
                }
            ),
        }
    )

    def install(campaign: Campaign) -> CommandReceipt:
        play.engine.validate(updated)
        campaign["play_json"], campaign["revision"] = updated.model_dump_json(), updated.revision
        return CommandReceipt(action="combat", outcome="current-protector-fixture")

    await play.store.commit_turn(
        cid, "current-protector", state.revision, "current-protector", install
    )
    return (
        cid,
        play,
        ChooseDefense(
            id="protect-fireball",
            actor_id="c",
            expected_revision=updated.revision,
            encounter_id="fight",
            defense="dodge",
            sacrificial_for="b",
        ),
    )
