"""B10/B235/B251 Haste construction and approved personal-cast history."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from test_haste_effects import command, prepare
from test_statistics import gurps_draft, profile_compiler, profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.magic.movement import package
from wayfarer.engine.rules.types.skill import Difficulty
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.melee.values import score_defense
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import PROFILE
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


def test_haste_iq_hard_construction_rejects_purchase_beyond_approved_budget() -> None:
    spells = package()
    rules = profile_package(PROFILE, *spells.definitions)
    original = profile_compiler(PROFILE, package=rules)
    compiler = CharacterCompiler(
        RulesCatalog((rules,)),
        original.rules,
        replace(original.policy, point_budget=69, allow_supernatural=True),
        statistics_profile=PROFILE,
    )
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=2),
        Purchase(definition_id="spell:haste", amount=4),
    )
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 12}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    valid = compiler.compile(draft)
    assert valid.build is not None, valid.diagnostics
    # B14 IQ12 costs40; B66 Magery0 costs5, two levels cost20; B170 IQ/Hard at4 points = IQ.
    assert valid.spent == 40 + 5 + 20 + 4 == 69
    assert next(v.value for v in valid.build.sheet.values if v.target == "spell:haste") == 14
    definition = next(d for d in spells.definitions if d.id == "spell:haste")
    assert definition.skill is not None and definition.skill.difficulty is Difficulty.HARD
    assert definition.skill.prerequisites == ()
    # The same complete Haste construction is four points beyond the GM's budget.
    too_small = CharacterCompiler(
        RulesCatalog((rules,)),
        original.rules,
        replace(original.policy, point_budget=65, allow_supernatural=True),
        statistics_profile=PROFILE,
    ).compile(draft)
    assert too_small.spent == 69 and too_small.build is None
    assert [d.code for d in too_small.diagnostics] == ["budget.overspent"]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_personal_haste_approved_host_authority_retry_and_seeded_expiry(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: "00" * 32
    service = HasteService(play)
    start = command("personal-start", 1, "start")
    with pytest.raises(AuthorizationError):
        await service.execute(cid, start, principal_id="bob")
    assert await play.store.read(cid) == initial
    await service.execute(cid, start, principal_id="alice")
    started = await play.store.read(cid)
    with pytest.raises(ConflictError):
        await service.execute(cid, command("stale-start", 1, "start"), principal_id="alice")
    assert await play.store.read(cid) == started
    await play.execute(
        cid, Wait(id="personal-one", actor_id="a", expected_revision=2, ticks=1), principal_id="a"
    )
    await service.execute(
        cid, command("personal-concentrate", 3, "concentrate"), principal_id="alice"
    )
    await play.execute(
        cid, Wait(id="personal-two", actor_id="a", expected_revision=4, ticks=1), principal_id="a"
    )
    complete = command("personal-complete", 5, "complete")
    receipt = await service.execute(cid, complete, principal_id="alice")
    assert receipt.outcome == "active" and receipt.energy_spent == 6
    active_campaign = await play.store.read(cid)
    state = play._load(active_campaign)
    participant = Combatant(
        actor_id="a",
        initiative=5,
        initiative_dx=10,
        position=GridPoint(x=0, y=0),
        reach=1,
        movement_allowance=5,
    )
    assert latest(state.resources)["haste"].expires_at == 62
    assert movement(play.rules_context, state, "a") == 8
    defense = score_defense(play.rules_context, state, participant, "dodge", targeted_weapon=False)[
        0
    ]
    assert defense is not None and defense.value == 11
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 4
    restarted = PlayService(play.store, play.engine, seeds=play.seeds)
    assert await HasteService(restarted).execute(cid, complete, principal_id="alice") == receipt
    assert await play.store.read(cid) == active_campaign
    with pytest.raises(ConflictError):
        await HasteService(restarted).execute(
            cid, complete.model_copy(update={"energy": 2}), principal_id="alice"
        )
    assert await play.store.read(cid) == active_campaign
    await restarted.execute(
        cid,
        Wait(id="personal-expiry", actor_id="a", expected_revision=6, ticks=60),
        principal_id="a",
    )
    final_campaign = await play.store.read(cid)
    final_state = play._load(final_campaign)
    assert final_state.resources.game_time == 62
    assert movement(play.rules_context, final_state, "a") == 5
    final_defense = score_defense(
        play.rules_context, final_state, participant, "dodge", targeted_weapon=False
    )[0]
    assert final_defense is not None and final_defense.value == 8
    records = [
        r
        for r in await play.store.history(cid)
        if r.expected_revision >= initial["revision"] and r.command_id != "setup:seed"
    ]
    identifiers = {r.command_id for r in records}
    folded, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "personal-reexecuted"),
    )
    assert len(checks) == len(records) and all(c.folded and c.reexecuted for c in checks)
    assert document(folded) == document(final_campaign)
