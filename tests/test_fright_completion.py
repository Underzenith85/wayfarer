"""Independent completion evidence for Campaigns B360-B361 fright consequences."""

from pathlib import Path

import pytest
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.fright import FrightEffect, fright_effect
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.fright import apply_effect, effects
from wayfarer.engine.simulation.health.fright_state import PREFIX
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.models import Record
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService

FIXTURE = Path(__file__).parent / "fixtures/gurps/fright_consequences.json"


class FrightCase(Record):
    name: str
    total: int
    ht: int
    dice: tuple[int, ...]
    expected: dict[str, bool | int | str]


class FrightFixture(Record):
    source: str
    cases: tuple[FrightCase, ...]


CASES = FrightFixture.model_validate_json(FIXTURE.read_text()).cases


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_representative_source_derived_consequences(case: FrightCase) -> None:
    effect = fright_effect(case.total, case.ht, rng=RecordedDice(list(case.dice)))
    for field, expected in case.expected.items():
        assert getattr(effect, field) == expected


async def test_collapse_updates_the_authoritative_encounter_and_replays(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")

    def resolve(play: PlayService, state: PlayState, command: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(SocialContext("gurps-basic-set-4e-2004", 10, will=10, ht=10))

    # Failure by four plus a table roll of 13 selects row 17; the last die
    # fixes the faint at two minutes.  No injury roll is involved.
    play.rng = RecordedDice([4, 5, 5, 4, 4, 5, 2])
    command = SocialCommand(
        id="fright-collapse",
        actor_id="b",
        subject_id="a",
        kind="fright",
        trigger_id="encounter-fear",
        expected_revision=1,
    )
    result = await SocialService(play, resolve).execute(cid, command, authenticated_gm_id="gm")
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert result.outcome == "failed"
    assert next(p for p in state.encounters[0].participants if p.actor_id == "a").posture == "prone"
    assert effects(state.resources)[0].effect.collapse

    play.rng = RecordedDice([])
    assert (
        await SocialService(play, resolve).execute(cid, command, authenticated_gm_id="gm") == result
    )
    assert await play.store.read(cid) == saved == await play.store.replay(cid)


async def test_scheduler_occurrence_id_is_independent_of_outer_advance(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    initial = play._load(await play.store.read(cid)).resources
    installed = apply_effect(
        initial,
        FrightEffect(
            table_total=7,
            condition="stunned",
            duration_seconds=1,
            recovery_attribute="will",
            recovery_interval_seconds=1,
        ),
        actor_id="a",
        trigger_id="same-fear",
        command_id="same-occurrence",
        ht=10,
        will=10,
        modified_will=10,
        rng=RecordedDice([]),
    )
    recovered = []
    for command_id in ("advance-a", "advance-b"):
        recovered.append(
            play.engine.resources.apply(
                installed,
                Advance(id=command_id, actor_id="a", expected_revision=1, to=1),
                system=True,
                rng=RecordedDice([1, 1, 1]),
            )
        )
    ids = [tuple(e.id for e in state.events if e.id.startswith(PREFIX)) for state in recovered]
    assert ids[0] == ids[1]
    assert not effects(recovered[0])[0].active
