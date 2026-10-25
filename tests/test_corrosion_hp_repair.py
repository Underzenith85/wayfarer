"""A restored canonical corroded checkpoint does not acquire DR healing from B484."""

import secrets
from pathlib import Path

import pytest
from support.armoury_defaults import fixture, revision, wait
from support.runtime import build_play, played
from test_combat_sensory_authority import change
from test_issue_818_armoury_acceptance import begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.equipment.corrosion import protection
from wayfarer.engine.simulation.equipment.corrosion_state import erosion, projected_profile
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resources import Equip, Unequip
from wayfarer.orchestration.combat import CombatService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_hp_repair_preserves_physical_erosion_and_reexecutes(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play = await fixture(
        tmp_path,
        backend,
        "armor",
        "attribute:iq",
        hp=2,
        additional_purchases=(
            Purchase(definition_id="skill:armoury-body-armor", amount=20, technology_level=4),
        ),
    )

    def corroded_checkpoint(state: PlayState) -> PlayState:
        resources = play.engine.resources.apply(
            state.resources,
            Equip(
                id="fixture-equip",
                actor_id="b",
                expected_revision=state.resources.revision,
                item_id="repair-target",
                ready=False,
            ),
        )
        value = protection(
            play.rules_context, state.model_copy(update={"resources": resources}), "b"
        )
        resources, result = apply_injury(
            resources,
            Wound(
                id="fixture-acid",
                actor_id="b",
                expected_revision=resources.revision,
                damage_type="cor",
                basic_damage=5,
                resistance=6,
            ),
            ht=10,
            rng=RecordedDice(()),
            system=True,
            corrosion=value,
        )
        assert result.injury == 0
        resources = play.engine.resources.apply(
            resources,
            Unequip(
                id="fixture-unequip",
                actor_id="b",
                expected_revision=resources.revision,
                item_id="repair-target",
            ),
        )
        return state.model_copy(update={"resources": resources})

    # This narrow repair consumer restores an already resolved canonical source.
    # Separate composed-host tests manufacture the source and erosion from genesis.
    await change(play, cid, corroded_checkpoint)
    initial = await play.store.read(cid)
    before = play._load(initial)
    entry = next(
        e
        for e in catalog(play.rules_context).entries
        if e.definition_id == "equipment:repair-target"
    )
    loss = erosion(before.resources, "repair-target")
    assert loss and loss.total_loss == 1
    play.rng = secrets
    play.seeds = lambda: f"{2:064x}"
    start = begin(await revision(play, cid))
    await CombatService(play).execute(cid, start, principal_id="b")
    await wait(play, cid, 1800)
    finish = begin(await revision(play, cid)).model_copy(
        update={"id": "finish", "stage": "finish", "task_id": "repair"}
    )
    result = await CombatService(play).execute(cid, finish, principal_id="b")
    final = await play.store.read(cid)
    state = play._load(final)
    repaired = next(i for i in state.resources.items if i.id == "repair-target")
    assert repaired.condition and repaired.condition.hp == 12 and not repaired.condition.disabled
    assert tasks(state.resources)[0].restored_hp == 10
    armor = projected_profile(state.resources, repaired, entry).armor
    assert armor and armor.dr == 5 and erosion(state.resources, repaired.id) == loss
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, finish, principal_id="b") == result
    commands = [
        record
        for record in await played(play.store, cid)
        if record.expected_revision >= initial["revision"]
    ]
    names = {record.command_id for record in commands}
    events = [event for event in await play.store.stream(cid) if event.command_id in names]
    replayed, checks = await verify_commands(
        initial,
        commands,
        events,
        configuration_digest=before.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final
