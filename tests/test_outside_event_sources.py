"""Natural provenance refers to the real exposure and its immutable receipt."""

from pathlib import Path

import pytest
from test_outside_event_damage import flame
from test_outside_event_host import exposed_host

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.outside_event_records import (
    NaturalExposureDeclaration,
    PrepareOutsideEvent,
)
from wayfarer.orchestration.outside_event_sources import (
    bind_outside_event_source,
    validate_natural_exposure,
)


@pytest.mark.parametrize("source_id", ["exposed", "resume", "missing"])
async def test_natural_source_requires_original_hazard_admission(
    tmp_path: Path, source_id: str
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path)
    state = play._load(await play.store.read(cid))
    command = PrepareOutsideEvent(
        id="natural",
        actor_id="a",
        expected_revision=state.revision,
        schedule_id=schedule_id,
        source=NaturalExposureDeclaration(
            exposure_command_id=source_id, circumstances="Accidental natural fire"
        ),
    )
    if source_id != "exposed":
        with pytest.raises(ValidationError):
            await bind_outside_event_source(play, cid, command)
        return
    bound = await bind_outside_event_source(play, cid, command)
    assert isinstance(bound, PrepareOutsideEvent) and bound.exposure_command is not None
    assert bound.exposure_command.kind == "enter" and bound.exposure_command.id == source_id
    validate_natural_exposure(
        state, "a", state.resources.hazards[0], bound.source, bound.exposure_command
    )
    with pytest.raises(ValidationError, match="receipt"):
        validate_natural_exposure(
            state.model_copy(
                update={"resources": state.resources.model_copy(update={"receipts": ()})}
            ),
            "a",
            state.resources.hazards[0],
            bound.source,
            bound.exposure_command,
        )
    with pytest.raises(ConflictError, match="differs"):
        await bind_outside_event_source(
            play,
            cid,
            bound.model_copy(
                update={
                    "exposure_command": bound.exposure_command.model_copy(
                        update={"kind": "resolve"}
                    )
                }
            ),
        )


@pytest.mark.parametrize("prefix", ["spell-fire:", "spell-crossing:", "sprayer-fire:"])
async def test_known_attack_cause_cannot_be_relabelled_natural(tmp_path: Path, prefix: str) -> None:
    cid, play, schedule_id = await exposed_host(
        tmp_path, spec=flame().model_copy(update={"id": prefix + "source", "delay": 1})
    )
    state = play._load(await play.store.read(cid))
    command = PrepareOutsideEvent(
        id="natural",
        actor_id="a",
        expected_revision=state.revision,
        schedule_id=schedule_id,
        source=NaturalExposureDeclaration(
            exposure_command_id="exposed", circumstances="Claimed natural fire"
        ),
    )
    bound = await bind_outside_event_source(play, cid, command)
    assert isinstance(bound, PrepareOutsideEvent)
    with pytest.raises(ValidationError, match="Attack or spell"):
        validate_natural_exposure(
            state, "a", state.resources.hazards[0], bound.source, bound.exposure_command
        )
