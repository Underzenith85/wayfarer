"""Byte/entropy oracles captured from unchanged rescue source 40c191f2.

These literal pre-continuation outputs protect ordinary campaign procedures;
no existing fixture is regenerated and none proves command-host replay.
"""

import hashlib
from typing import Literal

import pytest
from test_actions import seed
from test_campaign_administration import configured as administration_engine
from test_economics import configured as economics_engine
from test_economics import state as economics_state

from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.campaign.administration import AcquireKnowledge, ResolveReaction
from wayfarer.engine.simulation.campaign.economics import ExecuteTrade, FindHireling
from wayfarer.engine.simulation.campaign.law import LawCase, ResolveLawCase
from wayfarer.engine.simulation.campaign.procedures import CampaignCommand


@pytest.mark.parametrize(
    "role,digest,tail",
    [
        ("hire", "920576ac0c051c00d8dab8672691080bbdc18d65225e7341a38f34fbb6ce232e", 460393),
        ("trade", "bef050331b06598f6f8d4035ea18622e9430fabd218fb74981b1e92649eb763c", 740003),
        ("knowledge", "773123a828a67e1fadaf360caf84eabbf9ae2649f45451145e6ba64018b1e588", 740003),
        ("reaction", "45b627ca40ac2c5b17e1a927d99daf3b5dcc07963f3cf714ac201fea32823204", 460393),
        ("law", "7f9c6733b84a9bf89e490fc956bb9252cc55735715a43ace8c715f2da2e7799c", 460393),
    ],
)
def test_ordinary_seeded_campaign_histories_remain_byte_stable(
    role: Literal["hire", "trade", "knowledge", "reaction", "law"],
    digest: str,
    tail: int,
) -> None:
    engine = economics_engine() if role in ("hire", "trade") else administration_engine()
    state = economics_state(engine) if role in ("hire", "trade") else seed(engine)
    command: CampaignCommand
    if role == "hire":
        command = FindHireling(
            id="legacy-command", actor_id="a", expected_revision=0, hireling_rule_id="guide"
        )
    elif role == "trade":
        command = ExecuteTrade(
            id="legacy-command", actor_id="a", expected_revision=0, offer_id="buy-sword"
        )
    elif role == "knowledge":
        command = AcquireKnowledge(
            id="legacy-command", actor_id="a", expected_revision=0, source_id="read-letter"
        )
    elif role == "reaction":
        command = ResolveReaction(
            id="legacy-command", actor_id="a", expected_revision=0, context_id="dock-warden"
        )
    else:
        command = ResolveLawCase(
            id="legacy-command",
            actor_id="a",
            expected_revision=0,
            case_id="case",
            procedure_id="trial",
        )
        state = state.model_copy(
            update={
                "law": state.law.model_copy(
                    update={
                        "cases": (
                            LawCase(
                                id="case",
                                crime_id="theft",
                                subject_id="a",
                                jurisdiction_id="city",
                                status="arrested",
                            ),
                        )
                    }
                )
            }
        )
    rng = SeededRandom("5b" * 32)
    after, _ = engine.campaign.apply(state, command, rng=rng, system=True)
    assert hashlib.sha256(after.model_dump_json().encode()).hexdigest() == digest
    assert rng.randbelow(1_000_000) == tail
