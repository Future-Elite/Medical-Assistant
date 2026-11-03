"""API configuration never falls back to a developer's private gateway."""
import pytest

import ehrworld.llm as llm


def test_api_config_has_no_embedded_endpoint():
    assert llm.APIConfig().base_url == ""


def test_environment_requires_explicit_base_url(monkeypatch):
    monkeypatch.setattr(llm, "dotenv_values", lambda _: {})
    monkeypatch.setattr(llm.os, "environ", {"EHRWORLD_API_KEY": "unit-test-token"})
    with pytest.raises(ValueError, match="EHRWORLD_BASE_URL"):
        llm.APIConfig.from_env()


def test_environment_loads_explicit_endpoint_without_exposing_token(monkeypatch):
    monkeypatch.setattr(llm, "dotenv_values", lambda _: {})
    monkeypatch.setattr(llm.os, "environ", {
        "EHRWORLD_API_KEY": "unit-test-token",
        "EHRWORLD_BASE_URL": "https://example.invalid/v1",
    })
    config = llm.APIConfig.from_env()
    assert config.base_url == "https://example.invalid/v1"
    assert "unit-test-token" not in repr(config)


def test_client_rejects_missing_endpoint_before_opening_connection():
    with pytest.raises(ValueError, match="explicit API base URL"):
        llm.JSONClient(llm.APIConfig(api_key="unit-test-token"))
