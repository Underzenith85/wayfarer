"""Independent B437-444 onset/resistance/aging source branches for #837."""

from pathlib import Path

import pytest
from test_disease_aging import contact, flu
from test_disease_aging import state as health_state
from test_toxins import arsenic
from test_toxins import state as toxin_state

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.disease import YEAR_SECONDS, AgingRules
from wayfarer.engine.rules.types.toxin import DeliveryEvidence
from wayfarer.engine.simulation.health.disease import (
    EnrollAging,
    ExposeDisease,
    ResolveAging,
    ResolveDisease,
    apply_aging,
    apply_disease,
    disease_episodes,
    permanent_changes,
)
from wayfarer.engine.simulation.health.toxins import ToxinCommand, apply_toxin
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError


@pytest.mark.parametrize(
    ("age", "interval"),
    [(50, 31536000), (69, 31536000), (70, 15768000), (89, 15768000), (90, 7884000)],
)
def test_aging_cadence_at_named_source_boundaries(age: int, interval: int) -> None:
    # B444: begin50 yearly,70 half yearly,90 quarter yearly, four rolls.
    enrolled, _ = apply_aging(
        health_state(),
        EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="aging"),
        rules=AgingRules(enabled=True),
        age_seconds=age * YEAR_SECONDS,
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    command = ResolveAging(id="roll", actor_id="a", expected_revision=1, schedule_id="aging")
    updated, result = apply_aging(enrolled, command, rng=RecordedDice([1, 1, 1] * 4), system=True)
    assert result.due == interval and len(result.checks) == 4
    assert permanent_changes(updated) == ()
    restored = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_aging(restored, command, rng=RecordedDice([]), system=True) == (updated, result)
    with pytest.raises(ConflictError):
        apply_aging(
            restored, command.model_copy(update={"id": "stale"}), rng=RecordedDice([]), system=True
        )


@pytest.mark.parametrize(("tl", "target"), [(0, 7), (3, 10), (5, 12), (7, 14), (8, 15)])
def test_aging_medical_tl_adjusts_every_attribute_roll(tl: int, target: int) -> None:
    # B444: modified HT = HT + world's medical TL -3.
    enrolled, _ = apply_aging(
        health_state(),
        EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="aging"),
        rules=AgingRules(enabled=True, technology_level=tl),
        age_seconds=50 * YEAR_SECONDS,
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    _, result = apply_aging(
        enrolled,
        ResolveAging(id="roll", actor_id="a", expected_revision=1, schedule_id="aging"),
        rng=RecordedDice([1, 1, 1] * 4),
        system=True,
    )
    assert [check.effective_target for check in result.checks] == [target] * 4


@pytest.mark.parametrize(
    ("roll", "loss"), [([3, 3, 3], 0), ([4, 4, 5], 1), ([5, 6, 6], 2), ([6, 6, 6], 2)]
)
def test_aging_loss_threshold_is_not_hp_damage(roll: list[int], loss: int) -> None:
    # B444: ordinary failure loses one attribute level;17/18 lose two.
    enrolled, _ = apply_aging(
        health_state(),
        EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="aging"),
        rules=AgingRules(enabled=True),
        age_seconds=50 * YEAR_SECONDS,
        ht=12,
        rng=RecordedDice([]),
        system=True,
    )
    updated, _ = apply_aging(
        enrolled,
        ResolveAging(id="roll", actor_id="a", expected_revision=1, schedule_id="aging"),
        rng=RecordedDice(roll * 4),
        system=True,
    )
    assert updated.pools[0].current == 10
    changes = permanent_changes(updated)
    assert len(changes) == int(loss > 0)
    if loss:
        assert changes[0].losses.model_dump() == {"st": loss, "dx": loss, "iq": loss, "ht": loss}


@pytest.mark.parametrize(
    ("dice", "resisted", "hp", "remaining"), [([2, 3, 3], True, 10, 0), ([3, 3, 3, 4], False, 6, 7)]
)
def test_arsenic_onset_resistance_and_first_cycle(
    dice: list[int], resisted: bool, hp: int, remaining: int
) -> None:
    # B439: arsenic HT-2,1h onset,1d toxic per hourly cycle,8 cycles.
    exposed, result = apply_toxin(
        toxin_state(),
        ToxinCommand(
            id="dose", actor_id="a", expected_revision=0, kind="expose", exposure_id="arsenic"
        ),
        profile=arsenic(),
        evidence=DeliveryEvidence(swallowed=True),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.due == 3600 and exposed.pools[0].current == 10
    command = ToxinCommand(
        id="first-cycle", actor_id="a", expected_revision=1, kind="resolve", exposure_id="arsenic"
    )
    updated, result = apply_toxin(
        exposed.model_copy(update={"game_time": 3600}), command, rng=RecordedDice(dice), system=True
    )
    assert result.check is not None and result.check.effective_target == 8
    assert result.resisted is resisted and updated.pools[0].current == hp
    assert updated.toxins[0].remaining == remaining
    assert result.active is (not resisted)
    if not resisted:
        assert result.due == 7200
    restored = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_toxin(restored, command, rng=RecordedDice([]), system=True) == (updated, result)


def test_disease_resistance_precedes_authored_incubation_and_cycle() -> None:
    # B442-443: HT resistance modified by contact; failed exposure has delay,
    # then a recovery roll each authored cycle. Story disease values are explicit
    # scenario construction, not claimed as a named book disease.
    enrolled, _ = apply_disease(
        health_state(),
        ExposeDisease(id="expose", actor_id="a", expected_revision=0, relationship_id="contact-1"),
        profile=flu(),
        relationship=contact(),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    episode = disease_episodes(enrolled)[0]
    settled, result = apply_disease(
        enrolled.model_copy(update={"game_time": 4 * 86400}),
        ResolveDisease(id="settle", actor_id="a", expected_revision=1, episode_id=episode.id),
        rng=RecordedDice([6, 6, 6, 5, 5, 5, 1, 1, 1]),
        system=True,
    )
    assert [check.effective_target for check in result.checks] == [10, 8, 8]
    assert settled.pools[0].current == 9 and result.hp_lost == 1
    assert disease_episodes(settled)[0].immune and disease_episodes(settled)[0].stage == "recovered"


@pytest.mark.parametrize(
    ("temperature", "wind", "interval", "target"),
    [(0, 0, 1800, 10), (-10, 0, 1800, 9), (0, 10, 900, 10), (0, 30, 600, 10)],
)
async def test_cold_interval_and_resistance_produce_fatigue(
    tmp_path: Path, temperature: int, wind: int, interval: int, target: int
) -> None:
    # B430: winter clothing, each complete10 degrees below0 gives -1;
    # ordinary/10mph/30mph checks every30/15/10 minutes. A failure costs1FP.
    from test_environmental_hazards import _resolve

    from wayfarer.engine.rules.environment import ambient_spec
    from wayfarer.engine.rules.types.hazard import HazardSpec

    spec = ambient_spec(
        HazardSpec(
            id="cold",
            scene_id="dock",
            kind="cold",
            delay=0,
            interval=1800,
            cycles=2,
            reference="B430",
        ),
        temperature=temperature,
        ht=10,
        wind=wind,
    )
    assert spec.delay == spec.interval == interval
    updated, result = await _resolve(tmp_path, spec, [4, 4, 4])
    assert result.fp_lost == 1
    assert next(pool.current for pool in updated.pools if pool.id == "fp:a") == 4
    assert result.check is not None and result.check.effective_target == target
