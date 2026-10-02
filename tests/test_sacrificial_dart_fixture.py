"""Current B279 tranquilizer target facts and authoritative pending carrier fixture."""

from decimal import Decimal
from pathlib import Path

from support.runtime import build_play, open_store, seed_campaign
from test_combat_sensory_authority import change
from test_firearm_malfunctions import firearm
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.ranged_equipment import FollowUpSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.equipment.catalog import (
    LITE_SOURCE,
    Armor,
    Damage,
    EquipmentProfile,
)
from wayfarer.engine.simulation.resources import Item
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService


async def pending_dart(
    path: Path, backend: str, *, dr: int = 0, payload: FollowUpSpec | None = None
) -> tuple[str, PlayService, ChooseDefense]:
    dart = firearm().model_copy(
        update={
            "damage": Damage(
                basis="fixed", dice=1, damage_type="pi-", armor_divisor=Decimal("0.2")
            ),
            "linked_follow_up": payload
            or FollowUpSpec(
                payload_id="equipment:tranquilizer-dose",
                kind="drug",
                resistance_penalty=-3,
                condition="unconsciousness",
                duration_minutes_per_margin=1,
            ),
        }
    )
    armor = EquipmentProfile(
        definition_id="equipment:protector-armor",
        provenance=LITE_SOURCE,
        weight_millipounds=1000,
        price=10,
        technology_level=1,
        slot="body",
        armor=Armor(locations=("torso",), dr=dr),
    )
    cid, play = await setup(
        path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        ranged_mode=dart,
        ranged_scene=scene(),
        extra_equipment=(armor,),
        extra_items=(Item(id="protector-armor", definition_id=armor.definition_id, owner_id="c"),),
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    for _ in range(2):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="ranged",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
        await turn(cid, play, "c", "do_nothing")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3])
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="shoot",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="ranged",
            target_id="b",
            shots=1,
        ),
        principal_id="a",
    )

    def current(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "c")
        proposal = actor.proposal.model_copy(
            update={
                "draft": actor.proposal.draft.model_copy(
                    update={
                        "purchases": tuple(
                            p.model_copy(update={"amount": 8})
                            if p.definition_id == "attribute:ht"
                            else p
                            for p in actor.proposal.draft.purchases
                        )
                        + (Purchase(definition_id="secondary:basic-speed", amount=20),)
                    }
                )
            }
        )
        approval = play.engine.reviewer.approve(
            proposal,
            campaign_id=state.campaign_id,
            actor_id="c",
            revision=state.revision,
            approver_id="gm",
            reason="Current protector HT",
        )
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"proposal": proposal, "approval": approval})
                    if a.actor_id == "c"
                    else a
                    for a in state.actors
                ),
                "approvals": state.approvals + (approval,),
                "resources": state.resources.model_copy(
                    update={
                        "owners": tuple(
                            o.model_copy(
                                update={"definitions": o.definitions + ("secondary:basic-speed",)}
                            )
                            if o.actor_id == "c"
                            else o
                            for o in state.resources.owners
                        ),
                        "pools": tuple(
                            p.model_copy(update={"current": 8, "maximum": 8})
                            if p.id == "fp:c"
                            else p
                            for p in state.resources.pools
                        ),
                        "items": tuple(
                            i.model_copy(update={"equipped": True})
                            if i.id == "protector-armor"
                            else i
                            for i in state.resources.items
                        ),
                    }
                ),
            }
        )

    await change(play, cid, current)
    state = play._load(await play.store.read(cid))
    return (
        cid,
        play,
        ChooseDefense(
            id="protect",
            actor_id="c",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            sacrificial_for="b",
        ),
    )
