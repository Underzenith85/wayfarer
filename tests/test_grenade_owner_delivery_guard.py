"""An armed grenade's NONE delivery settles; it does not open an owner-damage pause."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_direct_grenade_delivery import pending_delivery

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.explosions import blasts, save
from wayfarer.engine.simulation.combat.ranged_target_policy import enabled, require_owner_damage
from wayfarer.errors import ValidationError
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService


class NoDraw:
    def __init__(self) -> None:
        self.calls = 0

    def randbelow(self, upper: int) -> int:
        self.calls += 1
        raise AssertionError("Refused grenade delivery must not request random dice")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_armed_none_delivery_never_opens_an_owner_damage_pause(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original, _, command = await pending_delivery(tmp_path, backend)
    assert isinstance(command.response, ChooseDefense)
    before = await play.store.read(cid)
    state = play._load(before)
    encounter = state.encounters[0]
    pending = encounter.pending_defense
    assert pending is not None
    assert enabled(play.rules_context, state, encounter, pending.attacker_id, pending_id=pending.id)
    play.rng = RecordedDice((3, 3, 3))
    result = await TaskService(play).execute(cid, command, principal_id="a")
    final = play._load(await play.store.read(cid))
    assert result.status == "completed" and result.pending_id is None
    assert snapshot(final).pending is None
    assert final.encounters[0].pending_defense is None
    values = blasts(final.resources)
    assert len(values) == 1
    assert (values[0].id, values[0].payload, values[0].due, values[0].fuse_dice) == (
        original.id,
        original.payload,
        original.due,
        original.fuse_dice,
    )
    assert not values[0].resolved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_other_current_grenade_defenses_refuse_without_entropy_or_mutation(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, _, command = await pending_delivery(tmp_path, backend)
    assert isinstance(command.response, ChooseDefense)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    rng = NoDraw()
    play.rng = rng
    for changes in (
        {"defense": "dodge"},
        {"catch_thrown": True},
        {"second_defense": "block", "second_item_id": "shield-b"},
    ):
        response = command.response.model_copy(update=changes)
        refused = command.model_copy(update={"response": response})
        with pytest.raises(ValidationError, match="Thrown attacks do not support"):
            await TaskService(play).execute(cid, refused, principal_id="a")
        assert rng.calls == 0
        assert await play.store.read(cid) == before
        assert await play.store.history(cid) == history
        assert await play.store.stream(cid) == events
        assert await play.store.command_input(cid, command.id) is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_nonpausing_admission_requires_the_actual_valid_armed_cause(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original, _, command = await pending_delivery(tmp_path, backend)
    assert isinstance(command.response, ChooseDefense)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    state = play._load(before)
    encounter = state.encounters[0]
    rng = NoDraw()
    play.rng = rng
    cases = (
        (original.model_copy(update={"resolved": True}), "Thrown attacks do not support"),
        (
            original.model_copy(update={"due": state.resources.game_time}),
            "original encounter and unexpired fuse",
        ),
        (
            original.model_copy(update={"encounter_id": "other-encounter"}),
            "original encounter and unexpired fuse",
        ),
    )
    for index, (cause, message) in enumerate(cases):
        resources = save(state.resources, cause, f"guard-case:{index}")
        candidate = state.model_copy(update={"resources": resources})
        with pytest.raises(ValidationError, match=message):
            require_owner_damage(
                play.rules_context, candidate, encounter, response=command.response
            )
    duplicate = original.model_copy(update={"id": original.id + ":duplicate"})
    candidate = state.model_copy(
        update={"resources": save(state.resources, duplicate, "duplicate-live-cause")}
    )
    with pytest.raises(ValidationError, match="ambiguous armed blast causes"):
        require_owner_damage(play.rules_context, candidate, encounter, response=command.response)
    # A future defense choice is not a truthful NONE response at the original-roll boundary.
    with pytest.raises(ValidationError, match="Thrown attacks do not support"):
        require_owner_damage(play.rules_context, state, encounter)
    assert rng.calls == 0
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == events
    assert await play.store.command_input(cid, command.id) is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_grenade_cannot_borrow_qualification_or_defense_identity(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, _, command = await pending_delivery(tmp_path, backend)
    assert isinstance(command.response, ChooseDefense)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    state = play._load(before)
    encounter = state.encounters[0]
    pending = encounter.pending_defense
    assert pending is not None
    response = command.response
    runtime = play.rules_context
    rng = NoDraw()
    runtime = replace(runtime, rng=rng)
    # The actual admitted native facts are lawful before varying one qualifier.
    require_owner_damage(runtime, state, encounter, response=response)
    unavailable_none = encounter.model_copy(
        update={"pending_defense": pending.model_copy(update={"allowed": ("dodge",)})}
    )
    with pytest.raises(ValidationError, match="Thrown attacks do not support"):
        require_owner_damage(runtime, state, unavailable_none, response=response)
    for changes in (
        {"actor_id": pending.attacker_id},
        {"encounter_id": "other-encounter"},
        {"item_id": "shield-b"},
        {"sacrificial_for": "b"},
        {"acrobatic_dodge": True},
        {"dodge_and_drop": True},
        {"basic_retreat": True},
        {"parry_mode_id": "ordinary"},
        {"second_parry_mode_id": "ordinary"},
    ):
        with pytest.raises(ValidationError, match="Thrown attacks do not support"):
            require_owner_damage(
                runtime, state, encounter, response=response.model_copy(update=changes)
            )
    original_catalog = catalog(runtime)
    source = next(item for item in state.resources.items if item.id == pending.weapon_id)
    entry = next(
        item for item in original_catalog.entries if item.definition_id == source.definition_id
    )
    # A physical throw, or a grenade label without an explosive profile, cannot
    # inherit the actual native grenade's exemption.
    nongrenade = entry.model_copy(
        update={
            "modes": tuple(
                mode.model_copy(update={"firearm": None}) if mode.id == pending.mode_id else mode
                for mode in entry.modes
            )
        }
    )
    no_warhead = entry.model_copy(update={"warhead": None})
    for changed in (nongrenade, no_warhead):
        replacement = original_catalog.model_copy(
            update={
                "entries": tuple(
                    changed if value.definition_id == changed.definition_id else value
                    for value in original_catalog.entries
                )
            }
        )
        combat = runtime.rules.combat
        assert combat is not None
        altered = replace(
            runtime,
            rules=runtime.rules.model_copy(
                update={"combat": combat.model_copy(update={"gurps_equipment": replacement})}
            ),
        )
        with pytest.raises(ValidationError, match="Thrown attacks do not support"):
            require_owner_damage(altered, state, encounter, response=response)
    for changes in ({"owner_id": pending.defender_id}, {"ready": False}, {"equipped": False}):
        changed_source = source.model_copy(update=changes)
        resources = state.resources.model_copy(
            update={
                "items": tuple(
                    changed_source if item.id == source.id else item
                    for item in state.resources.items
                )
            }
        )
        with pytest.raises(ValidationError, match="owned, equipped, ready"):
            require_owner_damage(
                runtime,
                state.model_copy(update={"resources": resources}),
                encounter,
                response=response,
            )
    assert rng.calls == 0
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == events
    assert await play.store.command_input(cid, command.id) is None
