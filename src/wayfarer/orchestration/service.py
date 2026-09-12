"""Compatibility seed-row creation and read access; gameplay uses PlayService."""

import copy
import uuid

from wayfarer import validation
from wayfarer.config import Settings
from wayfarer.engine.character import builder
from wayfarer.engine.rules import catalog
from wayfarer.engine.simulation.campaign.scenario import validate_scenario
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, PublicCampaign
from wayfarer.orchestration.llm import LLMClient
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


class GameService:
    def __init__(self, settings: Settings, llm: LLMClient) -> None:
        self.store: AsyncSQLiteStore | AsyncPostgresStore
        if settings.database_url is None:
            self.store = AsyncSQLiteStore(settings.db, settings.db_timeout_seconds)
        else:
            self.store = AsyncPostgresStore(
                settings.database_url.get_secret_value(), settings.db_timeout_seconds
            )
        self.llm = llm

    async def create(self, character: object, scenario: object) -> PublicCampaign:
        verdict = builder.validate(character)
        if not verdict["valid"]:
            raise ValidationError("; ".join(verdict["errors"]))
        c = validation.character(character)
        s = validation.scenario(scenario)
        validate_scenario(s)
        cid = str(uuid.uuid4())
        state: Campaign = {
            "id": cid,
            "revision": 0,
            "rules": catalog.VERSION,
            "rules_ref": catalog.reference(catalog.DEFAULT_RULES),
            "character": c,
            "scenario": s,
            "hp": c["attributes"]["ST"],
            "fp": c["attributes"]["HT"],
            "minutes": 0,
            "location": s["location"],
            "inventory": ["Travel clothes", "Rations", "Waterskin"],
            "discoveries": [],
            "flags": [],
            "complete": False,
            "messages": [{"role": "gm", "text": s["premise"]}],
        }
        await self.store.insert(state)
        return public(state)

    async def read(self, cid: str) -> Campaign:
        return await self.store.read(cid)

    async def listing(self) -> list[dict[str, str]]:
        return await self.store.listing()


def public(state: Campaign) -> PublicCampaign:
    result = PublicCampaign(**copy.deepcopy(state), validation=builder.validate(state["character"]))
    result.pop("resources_json", None)
    result.pop("play_json", None)
    result["scenario"].pop("secret", None)
    result["scenario"].pop("clue", None)
    return result
