import pytest
from pydantic import SecretStr, ValidationError

from wayfarer.config import Settings
from wayfarer.errors import ProviderTimeoutError
from wayfarer.logging import redact_secrets


def test_configuration_requires_complete_provider_pair() -> None:
    with pytest.raises(ValidationError):
        Settings(openai_api_key=SecretStr("secret"), openai_model=None)


def test_configuration_rejects_bad_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(port=70000)
    with pytest.raises(ValidationError):
        Settings(model_timeout_seconds=0)


def test_log_redaction() -> None:
    event: dict[str, object] = {"event": "call", "api_key": "secret", "campaign_id": "safe"}
    assert redact_secrets(None, "info", event) == {
        "event": "call",
        "api_key": "[REDACTED]",
        "campaign_id": "safe",
    }


def test_error_taxonomy_distinguishes_timeout() -> None:
    error = ProviderTimeoutError("timed out")
    assert error.code == "provider_timeout" and error.status == 504
