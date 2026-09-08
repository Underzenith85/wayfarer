"""Typed failures shared across application boundaries."""

from dataclasses import dataclass


class WayfarerError(Exception):
    code = "wayfarer_error"
    status = 400


class ValidationError(WayfarerError):
    code = "validation_error"


class NotFoundError(WayfarerError):
    code = "not_found"
    status = 404


class ConflictError(WayfarerError):
    code = "conflict"
    status = 409


class AuthenticationError(WayfarerError):
    code = "authentication_required"
    status = 401


class AuthorizationError(WayfarerError):
    code = "forbidden"
    status = 403


class ProviderError(WayfarerError):
    code = "provider_error"
    status = 502
    stage: str | None = None
    upstream_status: int | None = None


class ProviderOutputError(ProviderError):
    code = "invalid_provider_output"
    stage = "response_validation"


class ProviderRequestError(ProviderError):
    code = "provider_request_rejected"


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"
    status = 504


@dataclass(frozen=True)
class ProviderDiagnostic:
    code: str
    message: str
    retryable: bool


def provider_diagnostic(error: ProviderError) -> ProviderDiagnostic:
    """Only fixed, allowlisted diagnostics may cross the player boundary.

    Never publish str(error), SDK messages, account details, paths or model output.
    """
    reasons = {
        "provider_turn_failed": (
            "Codex reported a failed turn. Ask the operator to inspect the provider.",
            True,
        ),
        "provider_connection_failed": (
            "Codex could not maintain its upstream response connection. Retry after checking connectivity.",
            True,
        ),
        "provider_context_limit": (
            "The Codex thread exceeded its context window. Start a fresh session.",
            False,
        ),
        "provider_overloaded": ("Codex reported an overloaded server. Retry later.", True),
        "provider_sandbox_error": (
            "Codex reported a sandbox failure. Ask the operator to check the runtime configuration.",
            False,
        ),
        "provider_stream_error": (
            "The SDK could not read or handle the turn stream. Ask the operator to check SDK/runtime compatibility.",
            True,
        ),
        "provider_request_rejected": (
            "Codex rejected the request. Ask the operator to check the model, output schema and SDK configuration.",
            False,
        ),
        "codex_login_required": (
            "Sign in with wayfarer-codex-login using the server's configured Codex profile, then retry.",
            False,
        ),
        "codex_subscription_limit": (
            "Codex usage limit reached. Wait for your allowance to reset, then retry.",
            True,
        ),
        "codex_cancelled": ("Codex generation was cancelled. You can retry.", True),
        "provider_timeout": (
            "Generation timed out. Retry or ask the operator to check the provider.",
            True,
        ),
        "invalid_provider_output": (
            "The provider returned an invalid structured response. Retry generation.",
            True,
        ),
        "provider_unavailable": (
            "The provider could not complete generation. Ask the operator to check its connection and model configuration.",
            True,
        ),
    }
    stages = {
        "account": "account check / token refresh",
        "thread_start": "thread creation",
        "thread_resume": "thread resume",
        "turn_start": "turn submission",
        "turn_stream": "turn execution / response stream",
        "response_validation": "structured response validation",
    }
    code = error.code if error.code in reasons else "provider_unavailable"
    message, retryable = reasons[code]
    stage = stages.get(error.stage or "")
    if stage:
        message += f" Failed during {stage}."
    if type(error.upstream_status) is int and 100 <= error.upstream_status <= 599:
        message += f" Upstream HTTP status: {error.upstream_status}."
    return ProviderDiagnostic(code, f"{message} Diagnostic: {code}.", retryable)


class StorageError(WayfarerError):
    code = "storage_error"
    status = 503
