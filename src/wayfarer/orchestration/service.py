"""Async application orchestration: validate, interpret, commit, then narrate."""

import copy
import uuid

import structlog

from wayfarer import validation
from wayfarer.character import builder
from wayfarer.config import Settings
from wayfarer.errors import ConflictError, ProviderError, ValidationError
from wayfarer.models import Action, Campaign, PublicCampaign
from wayfarer.orchestration.llm import ACTION_SCHEMA, NARRATION_SCHEMA, LLMClient
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules import catalog
from wayfarer.simulation.resolution import resolve
from wayfarer.simulation.scenario import validate_scenario

log = structlog.get_logger()


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

    async def interpret(self, text: str, state: Campaign) -> Action:
        if self.llm.enabled:
            proposal = await self.llm.generate(
                "Classify intent into one action. Hypothetical questions are ask. Never infer success.",
                {"input": text, "scene": public(state)},
                ACTION_SCHEMA,
            )
            return validation.action(proposal["action"])
        lowered = text.lower().strip()
        if "?" in lowered or lowered.startswith(("could ", "can ", "would ")):
            return "ask"
        words: dict[Action, tuple[str, ...]] = {
            "observe": ("look", "search", "observe", "inspect"),
            "talk": ("talk", "ask iven", "persuade", "speak"),
            "sneak": ("sneak", "slip", "customs", "follow"),
            "rest": ("rest", "sleep"),
        }
        return next(
            (action for action, terms in words.items() if any(term in lowered for term in terms)),
            "ask",
        )

    async def turn(
        self, cid: str, request_id: object, revision: object, text: object
    ) -> PublicCampaign:
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValidationError("A request ID is required")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or not isinstance(text, str)
            or not 1 <= len(text.strip()) <= 2000
        ):
            raise ValidationError("Invalid turn")
        duplicate = await self.store.duplicate(cid, request_id, text)
        if duplicate is not None:
            return public(duplicate)
        initial = await self.store.read(cid)
        if initial["revision"] != revision:
            raise ConflictError("Campaign changed. Refresh before retrying.")
        action = await self.interpret(text, initial)
        committed = await self.store.commit_turn(
            cid, request_id, revision, text, lambda state: resolve(state, action, text)
        )
        if committed["kind"] == "committed" and self.llm.enabled:
            state, event = committed["state"], committed["event"]
            try:
                narration = await self.llm.generate(
                    "Narrate only the committed outcome in 2 short atmospheric sentences. Do not add facts or rewards.",
                    {"outcome": event["outcome"], "location": state["location"], "player": text},
                    NARRATION_SCHEMA,
                )
                value = validation.string(narration["text"])
                if len(value) > 4000:
                    raise ValidationError("Narration is too long")
                await self.store.save_narration(cid, state["revision"], value)
            except ProviderError as exc:
                await log.awarning("narration_failed", campaign_id=cid, error_code=exc.code)
        return public(await self.store.read(cid))


def public(state: Campaign) -> PublicCampaign:
    result = PublicCampaign(**copy.deepcopy(state), validation=builder.validate(state["character"]))
    result["scenario"].pop("secret", None)
    result["scenario"].pop("clue", None)
    return result
