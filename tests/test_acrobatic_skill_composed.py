"""The independent native composed-defense envelope captures only Acrobatic rules."""

import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from test_acrobatic_condition_checks import recovered_coma
from test_acrobatic_skill_minimum import author_dx, response
from test_combat_sensory_authority import change
from test_composed_attack_host import declare, fixture
from test_gurps_melee import setup as melee_setup

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import generations
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import canonical, combat_intent, replay_payload, stamp
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("dx,enabled", [(6, False), (6, True), (7, True)])
async def test_native_composed_current_final_skill_and_old_envelope_stay_independent(
    tmp_path: Path, backend: str, dx: int, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import test_composed_attack_host

    author_dx(monkeypatch, dx)
    acrobatics = RuleDefinition(
        "skill:acrobatics",
        DefinitionKind.SKILL,
        "Acrobatics",
        "sjg:basic-set-characters-4e-2004",
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.DX, Difficulty.HARD, "B174"),
    )

    async def prepared_setup(
        path: Path,
        profile: Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"],
        *,
        human: bool,
        allow_supernatural: bool,
        trained: bool,
        scene_bound: bool,
        start_encounter: bool,
        extra_definitions: tuple[RuleDefinition, ...],
        trait_runtime_hooks: frozenset[str],
        extra_items: tuple[Item, ...],
        extra_purchases: tuple[Purchase, ...],
    ) -> tuple[str, PlayService]:
        return await melee_setup(
            path,
            profile,
            human=human,
            allow_supernatural=allow_supernatural,
            trained=trained,
            scene_bound=scene_bound,
            start_encounter=start_encounter,
            extra_definitions=(*extra_definitions, acrobatics),
            trait_runtime_hooks=trait_runtime_hooks,
            extra_items=extra_items,
            extra_purchases=(*extra_purchases, Purchase(definition_id=acrobatics.id, amount=1)),
        )

    monkeypatch.setattr(test_composed_attack_host, "setup", prepared_setup)
    cid, play, source = await fixture(tmp_path, backend, skill_points=4, levels=1)
    await declare(play, cid, source)
    await change(play, cid, recovered_coma)
    state = play._load(await play.store.read(cid))
    before, history = await play.store.read(cid), await play.store.history(cid)
    selected = response(state.revision)
    active = generations.ACTIVE
    if not enabled:
        monkeypatch.setattr(generations, "ACTIVE", active - generations.COMPOSED_ACTIVE)
    play.rng = RecordedDice(())
    if enabled and dx == 6:
        with pytest.raises(
            ValidationError, match="skill check requires effective skill at least 3"
        ):
            await ComposedAttackService(play).defend(cid, selected, principal_id="bob")
        assert play.rng.exhausted()
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        # The legitimate paid/declaration state remains; ordinary Dodge can settle it.
        play.rng = RecordedDice((2, 3, 3, 3, 3, 3, 1))
        result = await ComposedAttackService(play).defend(
            cid, response(state.revision, acrobatic=False), principal_id="bob"
        )
        assert result.injury and result.injury.injury == 1 and result.injury.hp_after == 9
        assert play.rng.exhausted()
        return
    # First roll3, second attack8, third dodge9. All are real server-owned rolls.
    for number in range(1000000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        if [sum(rng.randbelow(6) + 1 for _ in range(3)) for _ in range(3)] == [3, 8, 9]:
            break
    else:
        raise AssertionError("No composed eligibility consequence seed")
    play.rng, play.seeds = secrets, lambda: seed
    result = await ComposedAttackService(play).defend(cid, selected, principal_id="bob")
    final = await play.store.read(cid)
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == (3 if enabled else 4)
    assert result.injury and result.injury.defense and result.injury.defense.effective_target == 10
    assert result.injury.injury == 0 and result.injury.hp_after == 10
    saved = await play.store.command_input(cid, selected.id)
    assert saved and saved.text
    payload = replay_payload(saved.text)
    assert isinstance(payload, dict)
    assert payload.get(generations.KEY) == (
        sorted(generations.COMPOSED_ACTIVE) if enabled else None
    )
    monkeypatch.setattr(generations, "ACTIVE", active)
    play.rng = RecordedDice(())
    assert await ComposedAttackService(play).defend(cid, selected, principal_id="bob") == result
    records = (await play.store.history(cid))[len(history) :]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        before,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "composed-seed"),
    )
    assert replayed == final and all(check.folded and check.reexecuted for check in checks)


@pytest.mark.parametrize(
    "bad", [[True], [1], ["future"], ["acrobatic-skill-minimum"] * 2, ["grenade-fuse"]]
)
def test_composed_private_protocol_rejects_unknown_nonstring_duplicate_or_unrelated_flags(
    bad: object,
) -> None:
    text = json.dumps(
        {"operation": "composed-defense", generations.KEY: bad},
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(ValidationError, match="generation"):
        generations.features(CommandInput(payload_digest({"input": text}), text))
    with pytest.raises(ValidationError, match="generation"):
        combat_intent(text)


def test_composed_canonical_features_preserve_exact_intent_and_reject_task_overlap() -> None:
    base = canonical(
        {
            "operation": "composed-defense",
            "principal_id": "player:bob",
            "command": {"kind": "choose_defense"},
        }
    )
    payload = json.loads(base)
    payload[generations.KEY] = sorted(generations.COMPOSED_ACTIVE)
    selected = canonical(payload)
    assert combat_intent(selected) == base
    assert (
        generations.features(CommandInput(payload_digest({"input": selected}), selected))
        == generations.COMPOSED_ACTIVE
    )
    payload["task_combat_protocol_features"] = []
    with pytest.raises(ValidationError, match="generation"):
        combat_intent(canonical(payload))
    replayed = replay_payload(stamp(selected))
    assert isinstance(replayed, dict) and replayed["principal_id"] == "player:bob"
