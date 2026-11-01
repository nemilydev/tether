"""Shared fixtures for Tether tests."""
from __future__ import annotations

from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant

from custom_components.tether.const import (
    API_COMMAND_SERVER,
    CONF_CALLBACK_URL,
    CONF_CONNECTION_INFO,
    CONF_CONNECTION_TYPE,
    CONF_DEVELOPER_TOKEN,
    CONF_LOCAL_HOST,
    CONF_LOCAL_PORT,
    CONF_USE_HTTPS,
    CONF_USER_ID,
    CONF_USER_NAME,
    CONF_UTOKEN,
    CONF_VERIFY_SSL,
    CONNECTION_LOCAL,
    CONNECTION_STANDARD,
    DOMAIN,
)

LOCAL_HOST = "192.0.2.10"
LOCAL_URL = f"http://{LOCAL_HOST}:20010/command"
USER_ID = "user-1"
UTOKEN = "secret-utoken"
TOY_ID = "toy1"

LOCAL_DATA: dict[str, Any] = {
    CONF_CONNECTION_TYPE: CONNECTION_LOCAL,
    CONF_LOCAL_HOST: LOCAL_HOST,
    CONF_LOCAL_PORT: 20010,
    CONF_USE_HTTPS: False,
    CONF_VERIFY_SSL: False,
}
STANDARD_DATA: dict[str, Any] = {
    CONF_CONNECTION_TYPE: CONNECTION_STANDARD,
    CONF_DEVELOPER_TOKEN: "dev-token",
    CONF_USER_ID: USER_ID,
    CONF_USER_NAME: "Home Assistant",
    CONF_CALLBACK_URL: "https://ha.example.com/api/tether/callback",
    CONF_UTOKEN: UTOKEN,
    CONF_CONNECTION_INFO: {"domain": "192-0-2-44.lovense.club", "httpsPort": 30010},
}
STANDARD_LAN_URLS = (
    "https://192-0-2-44.lovense.club:30010/command",
    "https://192.0.2.44:30010/command",
)


def toy(**overrides: Any) -> dict[str, Any]:
    """Return a connected vibrating toy as Lovense reports it."""
    return {
        "id": TOY_ID,
        "name": "lush",
        "nickName": "My Lush",
        "status": "1",
        "battery": 80,
        "version": "3",
        "fullFunctionNames": ["Vibrate"],
        **overrides,
    }


def toys_response(*toys: dict[str, Any]) -> dict[str, Any]:
    return {"code": 200, "data": {item["id"]: item for item in toys}}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/tether."""


@pytest.fixture
def local_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Tether ({LOCAL_HOST})",
        unique_id=f"local:{LOCAL_HOST}:20010",
        data=LOCAL_DATA,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def standard_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title=f"Tether ({USER_ID})", unique_id=USER_ID, data=STANDARD_DATA
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_local_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer every local command, including GetToys, with one connected toy."""
    aioclient_mock.post(LOCAL_URL, json=toys_response(toy()))
    return aioclient_mock


@pytest.fixture
def mock_standard_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Make the paired LAN endpoints unreachable so commands fall back to the cloud."""
    for url in STANDARD_LAN_URLS:
        aioclient_mock.post(url, exc=aiohttp.ClientError())
    aioclient_mock.post(API_COMMAND_SERVER, json=toys_response(toy()))
    return aioclient_mock
