"""What a provider is handed and what a provider caller may reach.

These records and protocols sit below every service so that a module needing to
ask a provider a question does not have to import the orchestrator, and the
orchestrator does not have to import the runtime it drives (#635).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field

from wayfarer.engine.simulation.actions import ActionRules, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember, StreamEvent
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.models import Record
from wayfarer.orchestration.jobs import ProviderJobs
from wayfarer.persistence.events import CommandOrigin, CommandRecord

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


class Usage(Record):
    input_tokens: int = Field(default=0, ge=0, le=1000000)
    output_tokens: int = Field(default=0, ge=0, le=1000000)
    reported: bool = True


class ProviderReply(Record):
    payload_json: str = Field(max_length=32000)
    usage: Usage
    provider: str = Field(default="custom", min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=100)


class ProviderRequest(Record):
    operation: Literal["intent", "character_draft", "scenario_draft", "narration"]
    session_id: str
    context_json: str = Field(max_length=24000)
    prompt: str = Field(min_length=1, max_length=4000)
    output_schema: dict[str, object]


class StructuredProvider(Protocol):
    async def complete(self, request: ProviderRequest) -> object: ...


class CampaignContext(Protocol):
    """The campaign surface a provider caller needs, named rather than reached into.

    `CampaignRuntime` satisfies it; the orchestrator holds only this, so
    `providers` no longer imports the runtime that composes it.
    """

    jobs: ProviderJobs
    play: PlayService

    @property
    def rules(self) -> ActionRules: ...

    async def checkpoint(self, cid: str) -> PlayState: ...

    def member(self, state: PlayState, principal_id: str) -> CampaignMember: ...

    def control(self, member: CampaignMember, actor_id: str) -> None: ...

    def view(
        self, state: PlayState, member: CampaignMember, rules: CombatRules | None = None
    ) -> dict[str, object]: ...

    async def read(self, cid: str, *, principal_id: str) -> dict[str, object]: ...

    async def history(self, cid: str) -> list[CommandRecord]: ...

    async def events(
        self, cid: str, *, principal_id: str, after: int = 0, limit: int = 100
    ) -> tuple[StreamEvent, ...]: ...

    async def submit_json(
        self, cid: str, value: object, *, principal_id: str, origin: CommandOrigin | None = None
    ) -> dict[str, object]: ...
