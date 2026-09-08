"""Credential-free Codex contracts and explicitly opted-in authenticated smoke."""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import Draft202012Validator
from openai_codex import ApprovalMode, AsyncCodex, Sandbox
from openai_codex.generated.v2_all import GetAccountResponse, ItemCompletedNotification
from openai_codex.models import Notification
from openai_codex.types import TurnCompletedNotification
from test_wave9 import prepare

from wayfarer.errors import (
    ProviderError,
    ProviderOutputError,
    ProviderRequestError,
    ProviderTimeoutError,
    provider_diagnostic,
)
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.codex import (
    CodexAuthenticationError,
    CodexCancelledError,
    CodexLimitError,
    CodexProvider,
    CodexSettings,
    ProviderStatus,
    SDKBackend,
    codex_output_schema,
    codex_turn_failure,
    decode_codex_output,
)
from wayfarer.orchestration.providers import (
    Intent,
    Orchestrator,
    ProviderReply,
    ProviderRequest,
    Usage,
)


def request(session: str = "actor-session") -> ProviderRequest:
    return ProviderRequest(
        operation="intent",
        session_id=session,
        context_json="{}",
        prompt="Look around",
        output_schema=Intent.model_json_schema(),
    )


class FakeBackend:
    def __init__(self, failure: Exception | None = None, wait: bool = False) -> None:
        self.failure, self.wait = failure, wait
        self.calls: list[str | None] = []
        self.closed = False
        self.started = asyncio.Event()
        self.reply = ProviderReply(payload_json='{"kind":"wait","ticks":1}', usage=Usage())

    async def generate(
        self,
        request: ProviderRequest,
        thread_id: str | None,
        bind: Callable[[str], Awaitable[None]],
        status: Callable[[ProviderStatus], None],
    ) -> tuple[str, ProviderReply]:
        self.calls.append(thread_id)
        await bind(thread_id or "thread-1")
        self.started.set()
        if self.wait:
            await asyncio.Event().wait()
        if self.failure:
            raise self.failure
        status(
            ProviderStatus(
                session_id=request.session_id, operation=request.operation, status="working"
            )
        )
        return thread_id or "thread-1", self.reply

    async def close(self) -> None:
        self.closed = True


async def test_restart_thread_binding_isolation_and_status(tmp_path: Path) -> None:
    settings = CodexSettings(home=tmp_path / "profile", sessions=tmp_path / "map.db")
    backend = FakeBackend()
    statuses: list[ProviderStatus] = []
    first = CodexProvider(settings, backend=backend, status=statuses.append)
    await first.complete(request())
    await first.close()
    second = CodexProvider(settings, backend=backend)
    await second.complete(request())
    await second.complete(request("different-actor"))
    assert backend.calls == [None, "thread-1", None]
    assert [s.status for s in statuses] == ["started", "working", "completed"]
    assert b"Look around" not in settings.sessions.read_bytes()


@pytest.mark.parametrize(
    "error",
    [
        CodexAuthenticationError("Run codex login"),
        CodexLimitError("Limit"),
        CodexCancelledError("Cancelled"),
        RuntimeError("SECRET_TOKEN"),
    ],
)
async def test_safe_typed_failures(tmp_path: Path, error: Exception) -> None:
    backend = FakeBackend(failure=error)
    provider = CodexProvider(CodexSettings(sessions=tmp_path / "map.db"), backend=backend)
    with pytest.raises(ProviderError) as caught:
        await provider.complete(request())
    assert "SECRET_TOKEN" not in str(caught.value)
    if isinstance(error, ProviderError):
        assert type(caught.value) is type(error)
    else:
        assert caught.value.__suppress_context__
        assert backend.closed
    # Thread binding is durable even when a turn fails after thread creation.
    assert await provider.sessions.get("actor-session") == "thread-1"


async def test_timeout_and_cancellation_supervise_process(tmp_path: Path) -> None:
    backend = FakeBackend(wait=True)
    provider = CodexProvider(
        CodexSettings(timeout=0.03, sessions=tmp_path / "map.db"), backend=backend
    )
    with pytest.raises(ProviderTimeoutError):
        await provider.complete(request())
    assert backend.closed
    backend = FakeBackend(wait=True)
    provider = CodexProvider(CodexSettings(sessions=tmp_path / "map.db"), backend=backend)
    task = asyncio.create_task(provider.complete(request()))
    await backend.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.closed


async def test_codex_proposals_cannot_forge_rolls_and_narration_failure_keeps_commit(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path)
    backend = FakeBackend()
    provider = CodexProvider(CodexSettings(sessions=tmp_path / "map.db"), backend=backend)
    orchestrator = Orchestrator(CampaignAccess(play), provider, attempts=1)
    backend.reply = ProviderReply(payload_json='{"kind":"wait","ticks":1,"roll":3}', usage=Usage())
    with pytest.raises(ProviderError):
        await orchestrator.interpret_and_execute(
            cid, principal_id="alice", actor_id="a", command_id="bad", text="Win"
        )
    assert (await play.store.read(cid))["revision"] == 0
    backend.reply = ProviderReply(payload_json='{"kind":"wait","ticks":1}', usage=Usage())
    result = await orchestrator.interpret_and_execute(
        cid, principal_id="alice", actor_id="a", command_id="ok", text="Wait"
    )
    assert result.committed and not result.narration_available
    assert (await play.store.read(cid))["revision"] == 1


def sdk_fake(status: str = "completed", info: str | None = None) -> MagicMock:
    client = MagicMock(spec=AsyncCodex)
    client.account = AsyncMock(
        return_value=GetAccountResponse.model_validate(
            {
                "account": {"type": "chatgpt", "email": None, "planType": "plus"},
                "requiresOpenaiAuth": True,
            }
        )
    )
    thread = MagicMock()
    thread.id = "sdk-thread"
    client.thread_start = AsyncMock(return_value=thread)
    client.thread_resume = AsyncMock(return_value=thread)
    client.close = AsyncMock()
    turn = MagicMock()
    turn.id = "turn-1"
    turn.interrupt = AsyncMock()
    thread.turn = AsyncMock(return_value=turn)

    async def stream() -> AsyncIterator[Notification]:
        item = ItemCompletedNotification.model_validate(
            {
                "threadId": "sdk-thread",
                "turnId": "turn-1",
                "completedAtMs": 1,
                "item": {
                    "id": "message",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": '{"result":{"kind":"wait","ticks":1}}',
                },
            }
        )
        yield Notification(method="item/completed", payload=item)
        completed = TurnCompletedNotification.model_validate(
            {
                "threadId": "sdk-thread",
                "turn": {
                    "id": "turn-1",
                    "status": status,
                    "items": [],
                    "error": {"message": "SECRET_TOKEN", "codexErrorInfo": info} if info else None,
                },
            }
        )
        yield Notification(method="turn/completed", payload=completed)

    turn.stream = stream
    return client


@pytest.mark.parametrize(
    "state,info,error",
    [
        ("completed", None, None),
        ("failed", "unauthorized", CodexAuthenticationError),
        ("failed", "usageLimitExceeded", CodexLimitError),
        ("interrupted", None, CodexCancelledError),
        ("failed", "other", ProviderError),
        ("failed", "badRequest", ProviderRequestError),
    ],
)
async def test_sdk_structured_stream_and_failure_mapping(
    tmp_path: Path, state: str, info: str | None, error: type[ProviderError] | None
) -> None:
    backend = SDKBackend(CodexSettings(home=tmp_path / "codex"))
    fake = sdk_fake(state, info)
    backend.client = cast(AsyncCodex, fake)
    bound: list[str] = []

    async def bind(value: str) -> None:
        bound.append(value)

    if error:
        with pytest.raises(error) as caught:
            await backend.generate(request(), None, bind, lambda _: None)
        assert "SECRET_TOKEN" not in str(caught.value)
        fake.thread_start.return_value.turn.return_value.interrupt.assert_awaited_once()
    else:
        thread_id, reply = await backend.generate(request(), None, bind, lambda _: None)
        assert thread_id == "sdk-thread" and json.loads(reply.payload_json)["kind"] == "wait"
        await backend.generate(request(), thread_id, bind, lambda _: None)
        fake.thread_resume.assert_awaited_once()
    assert bound[0] == "sdk-thread"
    assert fake.thread_start.call_args.kwargs["sandbox"] is Sandbox.read_only
    assert fake.thread_start.call_args.kwargs["approval_mode"] is ApprovalMode.deny_all
    assert fake.thread_start.return_value.turn.call_args.kwargs[
        "output_schema"
    ] == codex_output_schema(Intent.model_json_schema())


async def test_sdk_missing_authentication_and_safe_runtime_config(tmp_path: Path) -> None:
    backend = SDKBackend(CodexSettings(home=tmp_path / "codex"))
    config = backend.config
    assert config.env is not None and "OPENAI_API_KEY" not in config.env
    assert "features.shell_tool=false" in config.config_overrides
    assert "features.view_image=false" in config.config_overrides
    assert "features.plugins=false" in config.config_overrides
    fake = sdk_fake()
    fake.account = AsyncMock(
        return_value=GetAccountResponse.model_validate(
            {"account": None, "requiresOpenaiAuth": True}
        )
    )
    backend.client = cast(AsyncCodex, fake)

    async def bind(_: str) -> None:
        raise AssertionError("Unauthenticated thread must not start")

    with pytest.raises(CodexAuthenticationError, match="codex login"):
        await backend.generate(request(), None, bind, lambda _: None)
    fake.thread_start.assert_not_called()


@pytest.mark.parametrize(
    "stage", ["account", "thread_start", "thread_resume", "turn_start", "turn_stream"]
)
async def test_sdk_failure_stage_is_public_but_raw_details_are_not(
    tmp_path: Path, stage: str
) -> None:
    backend = SDKBackend(CodexSettings(home=tmp_path / "profile"))
    fake = sdk_fake()
    backend.client = cast(AsyncCodex, fake)
    failure = RuntimeError("SECRET_TOKEN private prompt /private/profile")
    if stage == "turn_start":
        fake.thread_start.return_value.turn.side_effect = failure
    elif stage == "turn_stream":

        async def broken_stream() -> AsyncIterator[Notification]:
            yield Notification(
                method="item/completed",
                payload=ItemCompletedNotification.model_validate(
                    {
                        "threadId": "sdk-thread",
                        "turnId": "turn-1",
                        "completedAtMs": 1,
                        "item": {"id": "message", "type": "agentMessage", "text": "{}"},
                    }
                ),
            )
            raise failure

        fake.thread_start.return_value.turn.return_value.stream = broken_stream
    else:
        getattr(fake, stage).side_effect = failure
    provider = CodexProvider(CodexSettings(sessions=tmp_path / "map.db"), backend=backend)
    if stage == "thread_resume":
        await provider.sessions.get("actor-session")
        await provider.sessions.put("actor-session", "existing-thread")
    with pytest.raises(ProviderError) as caught:
        await provider.complete(request())
    assert caught.value.stage == stage
    diagnostic = provider_diagnostic(caught.value)
    assert "Failed during" in diagnostic.message
    assert "SECRET_TOKEN" not in diagnostic.message
    assert "/private" not in diagnostic.message
    assert diagnostic.code == (
        "provider_stream_error" if stage == "turn_stream" else "provider_unavailable"
    )


def test_diagnostics_allowlist_reasons_and_stage() -> None:
    error = CodexAuthenticationError("SECRET_TOKEN")
    error.stage = "account"
    diagnostic = provider_diagnostic(error)
    assert diagnostic.code == "codex_login_required"
    assert not diagnostic.retryable
    assert "wayfarer-codex-login" in diagnostic.message
    assert "account check" in diagnostic.message
    error.code = "SECRET_TOKEN"
    error.stage = "SECRET_TOKEN"
    diagnostic = provider_diagnostic(error)
    assert diagnostic.code == "provider_unavailable"
    assert "SECRET_TOKEN" not in diagnostic.message


async def test_authenticated_codex_smoke(tmp_path: Path) -> None:
    from wayfarer.transport.v1.common import validate
    from wayfarer.transport.v1.provider import interpretation_schema

    if os.environ.get("WAYFARER_CODEX_SMOKE") != "1":
        pytest.skip("Enable WAYFARER_CODEX_SMOKE=1 after Codex login into a dedicated profile")
    home = os.environ.get("WAYFARER_CODEX_HOME")
    if not home:
        pytest.skip("Set WAYFARER_CODEX_HOME to the logged-in dedicated profile")
    provider = CodexProvider(CodexSettings(home=Path(home), sessions=tmp_path / "map.db"))
    proposal_request = request().model_copy(
        update={
            "output_schema": interpretation_schema(),
            "prompt": "Propose exactly the wait intent with ticks equal to 1.",
        }
    )
    try:
        try:
            reply = ProviderReply.model_validate(await provider.complete(proposal_request))
        except CodexAuthenticationError:
            pytest.skip("No suitable Codex subscription login")
        Intent.model_validate_json(reply.payload_json)
        validate("Intent", json.loads(reply.payload_json))
        # A second call resumes the same persisted campaign/actor session.
        Intent.model_validate_json(
            ProviderReply.model_validate(await provider.complete(proposal_request)).payload_json
        )
    finally:
        await provider.close()


def test_real_play_schema_has_object_root_and_resolves_only_needed_definitions() -> None:
    from wayfarer.transport.v1.provider import interpretation_schema

    original = interpretation_schema()
    before = json.dumps(original, sort_keys=True)
    schema = codex_output_schema(original)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["result"]
    assert "oneOf" not in json.dumps(schema)
    definitions = schema["$defs"]
    assert isinstance(definitions, dict) and "Action" not in definitions
    for name in ("TextIntent", "InspectIntent", "MoveIntent", "UseItemIntent", "WaitIntent"):
        definition = definitions[name]
        assert isinstance(definition, dict)
        properties = definition["properties"]
        assert isinstance(properties, dict)
        kind = properties["kind"]
        assert isinstance(kind, dict) and kind["type"] == "string"
    Draft202012Validator.check_schema(schema)
    for value in (
        {"kind": "wait", "ticks": 1},
        {
            "clarification": {
                "id": "choose",
                "prompt": "Which way?",
                "choices": [{"id": "dock", "label": "Dock"}],
                "allows_text": False,
            }
        },
    ):
        envelope = {"result": value}
        Draft202012Validator(schema).validate(envelope)
        assert json.loads(decode_codex_output(json.dumps(envelope), original)) == value
    assert json.dumps(original, sort_keys=True) == before


def test_tuple_arrays_are_lowered_to_provider_supported_items() -> None:
    schema = codex_output_schema(
        {
            "type": "array",
            "prefixItems": [{"type": "string"}, {"enum": ["left", "right"]}],
            "minItems": 2,
            "maxItems": 2,
        }
    )
    properties = schema["properties"]
    assert isinstance(properties, dict)
    result = properties["result"]
    assert isinstance(result, dict)
    assert "prefixItems" not in result
    assert result["items"] == {
        "anyOf": [
            {"type": "string"},
            {"enum": ["left", "right"], "type": "string"},
        ]
    }


@pytest.mark.parametrize(
    "text",
    [
        '{"kind":"wait","ticks":1}',
        '{"result":{"kind":"wait","ticks":1,"roll":20}}',
        '{"result":{"kind":"wait","ticks":0}}',
        '{"result":{"kind":"wait","ticks":1},"secret":"private"}',
        "not json",
    ],
)
def test_invalid_codex_envelope_or_proposal_is_rejected(text: str) -> None:
    from wayfarer.transport.v1.provider import interpretation_schema

    with pytest.raises(ProviderOutputError):
        decode_codex_output(text, interpretation_schema())


def test_normalizing_union_does_not_weaken_original_exclusivity() -> None:
    schema: dict[str, object] = {"oneOf": [{"type": "object"}, {"type": "object"}]}
    # The provider-facing anyOf accepts this, but original oneOf must reject it.
    Draft202012Validator(codex_output_schema(schema)).validate({"result": {}})
    with pytest.raises(ProviderOutputError):
        decode_codex_output('{"result":{}}', schema)


def test_scenario_schema_normalizes_nested_tagged_unions() -> None:
    from wayfarer.orchestration.catalog import GeneratedScenarioGraph

    schema = codex_output_schema(GeneratedScenarioGraph.model_json_schema())
    Draft202012Validator.check_schema(schema)
    encoded = json.dumps(schema)
    assert '"oneOf"' not in encoded
    assert '"discriminator"' not in encoded
    assert "(?!" not in encoded
    assert schema["type"] == "object"


def test_provider_schema_drops_unsupported_lookaround_but_keeps_original_validation() -> None:
    original: dict[str, object] = {
        "type": "string",
        "pattern": r"^(?!forbidden$).+$",
    }
    schema = codex_output_schema(original)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    result = properties["result"]
    assert isinstance(result, dict) and "pattern" not in result
    Draft202012Validator(schema).validate({"result": "forbidden"})
    with pytest.raises(ProviderOutputError):
        decode_codex_output('{"result":"forbidden"}', original)


async def test_sdk_uses_actual_play_schema_and_unwraps_response(tmp_path: Path) -> None:
    from wayfarer.transport.v1.provider import interpretation_schema

    backend = SDKBackend(CodexSettings(home=tmp_path / "profile"))
    fake = sdk_fake()
    backend.client = cast(AsyncCodex, fake)
    provider = CodexProvider(CodexSettings(sessions=tmp_path / "map.db"), backend=backend)
    reply = ProviderReply.model_validate(
        await provider.complete(
            request().model_copy(update={"output_schema": interpretation_schema()})
        )
    )
    assert json.loads(reply.payload_json) == {"kind": "wait", "ticks": 1}
    schema = fake.thread_start.return_value.turn.call_args.kwargs["output_schema"]
    Draft202012Validator(schema).validate({"result": json.loads(reply.payload_json)})


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "provider_request_rejected"),
        (401, "codex_login_required"),
        (429, "codex_subscription_limit"),
        (503, "provider_connection_failed"),
    ],
)
def test_typed_upstream_status_is_preserved_without_raw_error(status: int, code: str) -> None:
    from openai_codex.generated.v2_all import TurnCompletedNotification

    event = TurnCompletedNotification.model_validate(
        {
            "threadId": "thread",
            "turn": {
                "id": "turn",
                "status": "failed",
                "items": [],
                "error": {
                    "message": "SECRET_TOKEN",
                    "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": status}},
                },
            },
        }
    )
    assert event.turn.error is not None and event.turn.error.codex_error_info is not None
    error = codex_turn_failure(event.turn.error.codex_error_info.root)
    diagnostic = provider_diagnostic(error)
    assert diagnostic.code == code
    assert f"HTTP status: {status}" in diagnostic.message
    assert "SECRET_TOKEN" not in diagnostic.message


async def test_configured_provider_http_path_and_scoped_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aiohttp
    from aiohttp import web

    from wayfarer.config import Settings
    from wayfarer.transport.campaign_api import create_campaign_app

    backend = FakeBackend()

    def factory(
        settings: CodexSettings, *, status: Callable[[ProviderStatus], None] | None = None
    ) -> CodexProvider:
        return CodexProvider(settings, backend=backend, status=status)

    monkeypatch.setattr("wayfarer.orchestration.provider_runtime.CodexProvider", factory)
    cid, play = await prepare(tmp_path)
    app = create_campaign_app(
        CampaignAccess(play),
        {"alice-token": "alice", "bob-token": "bob"},
        legacy_routes=True,
        settings=Settings(
            llm_provider="codex", codex_home=tmp_path / "codex", codex_sessions=tmp_path / "map.db"
        ),
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}/campaigns/{cid}"
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                url + "/interpret",
                headers={"Authorization": "Bearer alice-token"},
                json={"actor_id": "a", "command_id": "http-intent", "text": "Wait"},
            ) as response:
                assert response.status == 200
                result = await response.json()
                assert result["committed"] and not result["narration_available"]
            async with client.get(
                url + "/provider-status?actor_id=a", headers={"Authorization": "Bearer alice-token"}
            ) as response:
                assert response.status == 200 and "completed" in await response.text()
            async with client.get(
                url + "/provider-status?actor_id=a", headers={"Authorization": "Bearer bob-token"}
            ) as response:
                assert response.status == 400
    finally:
        await runner.cleanup()
    assert backend.closed
