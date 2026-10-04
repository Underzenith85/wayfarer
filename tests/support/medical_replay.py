"""Original approved genesis, real Wither injury and opaque registered bandaging."""

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from test_wither_limb_host import prepare_contact

from support.runtime import build_runtime
from support.wither_limb import fixture as wither_fixture
from support.wither_limb import revision
from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.wither_spell_state import contact_results
from wayfarer.orchestration.combat import ChooseDefense, CombatService, EndEncounter
from wayfarer.orchestration.medical import CareEnvironment
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.runtime import CampaignRuntime

CARE = CareEnvironment(
    technology_level=7, food=True, water=True, sleep=True, equipment_quality_modifier=1
)


@dataclass
class TrustedCare:
    environment: CareEnvironment = field(default_factory=lambda: CARE)
    calls: int = 0

    def __call__(self, _play: PlayService, _state: PlayState, target: str) -> CareEnvironment:
        if target not in ("a", "b"):
            raise AssertionError("Unexpected care target")
        self.calls += 1
        return self.environment


async def fixture(
    path: Path, backend: str, *, first_aid: bool = False
) -> tuple[str, PlayService, Campaign, TrustedCare]:
    cid, play, original = await wither_fixture(path, backend, defender_first_aid=first_aid)
    await prepare_contact(play, cid)
    play.rng = secrets
    play.seeds = lambda: f"{43:064x}"
    await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    result = contact_results(state.resources)[0]
    assert result.outcome == "withered" and result.injury == 2
    assert result.hp_before == 9 and result.hp_after == 7
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end-fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            reason="The actual Wither contact has resolved",
        ),
        principal_id="gm",
    )
    return cid, play, original, TrustedCare()


def runtime(play: PlayService, care: TrustedCare) -> CampaignRuntime:
    return build_runtime(play, medical_environment=care)


def choice(projection: dict[str, object], kind: str) -> str:
    offered = cast(list[dict[str, object]], projection["gurps_recovery_choices"])
    return cast(str, next(c["id"] for c in offered if c["kind"] == kind))


async def begin(
    play: PlayService, cid: str, care: TrustedCare, *, identity: str = "bandage"
) -> dict[str, object]:
    access = runtime(play, care)
    offered = await access.read(cid, principal_id="bob")
    command: dict[str, object] = {
        "id": identity,
        "actor_id": "b",
        "expected_revision": await revision(play, cid),
        "kind": "gurps_recovery",
        "choice_id": choice(offered, "bandage"),
    }
    await access.submit_json(cid, command, principal_id="bob")
    return command


async def finish(
    play: PlayService, cid: str, care: TrustedCare, *, identity: str = "bandage-finish"
) -> dict[str, object]:
    access = runtime(play, care)
    offered = await access.read(cid, principal_id="bob")
    command: dict[str, object] = {
        "id": identity,
        "actor_id": "b",
        "expected_revision": await revision(play, cid),
        "kind": "gurps_recovery",
        "choice_id": choice(offered, "finish-recovery"),
    }
    await access.submit_json(cid, command, principal_id="bob")
    return command
