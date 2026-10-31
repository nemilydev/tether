"""Config, reconfigure and options flow tests."""
from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tether.const import (
    API_GET_QRCODE,
    CONF_CONNECTION_INFO,
    CONF_CONNECTION_TYPE,
    CONF_DEFAULT_TIME_SEC,
    CONF_POLL_INTERVAL,
    CONF_STROKE_CONTROL_TYPE,
    CONF_UTOKEN,
    CONF_VERIFY_SSL,
    CONNECTION_LOCAL,
    CONNECTION_STANDARD,
    DOMAIN,
    PENDING_PAIRINGS,
    STROKE_CONTROL_BOTH,
)

from .conftest import LOCAL_DATA, LOCAL_HOST, LOCAL_URL, STANDARD_DATA, USER_ID, toys_response

LOCAL_INPUT = {"host": LOCAL_HOST, "port": 20010, "use_https": False, "verify_ssl": False}
STANDARD_INPUT = {
    "developer_token": "dev-token",
    "user_id": USER_ID,
    "user_name": "Home Assistant",
    "callback_url": "https://ha.example.com/api/tether/callback",
}
QR_RESPONSE = {"code": 0, "data": {"qr": "https://example.com/qr.png", "code": "AB12"}}


async def _start(hass: HomeAssistant, connection_type: str) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION_TYPE: connection_type}
    )


async def test_local_flow_creates_entry(
    hass: HomeAssistant, mock_local_api: AiohttpClientMocker
) -> None:
    result = await _start(hass, CONNECTION_LOCAL)
    assert result["step_id"] == "local"
    with patch("custom_components.tether.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**LOCAL_INPUT, "host": f" {LOCAL_HOST} "}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Tether ({LOCAL_HOST})"
    assert result["data"] == {CONF_CONNECTION_TYPE: CONNECTION_LOCAL, **LOCAL_INPUT}
    assert result["result"].unique_id == f"local:{LOCAL_HOST}:20010"


async def test_local_flow_rejects_invalid_host(hass: HomeAssistant) -> None:
    result = await _start(hass, CONNECTION_LOCAL)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**LOCAL_INPUT, "host": "http://evil/path"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


async def test_local_flow_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LOCAL_URL, exc=aiohttp.ClientError())
    result = await _start(hass, CONNECTION_LOCAL)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOCAL_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_local_flow_rejects_lovense_error_code(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LOCAL_URL, json={"code": 500, "message": "busy"})
    result = await _start(hass, CONNECTION_LOCAL)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOCAL_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize("connection_type", [CONNECTION_LOCAL, CONNECTION_STANDARD])
@pytest.mark.parametrize("reconfigure", [False, True])
async def test_flows_handle_non_json_responses(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    connection_type: str,
    reconfigure: bool,
) -> None:
    local = connection_type == CONNECTION_LOCAL
    aioclient_mock.post(
        LOCAL_URL if local else API_GET_QRCODE,
        text="<html>Service unavailable</html>",
        headers={"Content-Type": "text/html"},
    )
    if reconfigure:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"local:{LOCAL_HOST}:20010" if local else USER_ID,
            data=LOCAL_DATA if local else STANDARD_DATA,
        )
        entry.add_to_hass(hass)
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONNECTION_TYPE: connection_type}
        )
    else:
        result = await _start(hass, connection_type)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], LOCAL_INPUT if local else STANDARD_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_local_flow_aborts_when_already_configured(
    hass: HomeAssistant, local_entry: MockConfigEntry, mock_local_api: AiohttpClientMocker
) -> None:
    result = await _start(hass, CONNECTION_LOCAL)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOCAL_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_standard_flow_pairs_through_callback(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(API_GET_QRCODE, json=QR_RESPONSE)
    result = await _start(hass, CONNECTION_STANDARD)
    assert result["step_id"] == "standard"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], STANDARD_INPUT)
    assert result["step_id"] == "pair"
    assert "https://example.com/qr.png" in result["description_placeholders"]["qr_code"]
    assert result["description_placeholders"]["pairing_code"] == "AB12"

    utoken = hass.data[DOMAIN][PENDING_PAIRINGS][USER_ID]["utoken"]
    client = await hass_client_no_auth()
    response = await client.post(
        "/api/tether/callback",
        json={"uid": USER_ID, "utoken": utoken, "domain": "192-0-2-44.lovense.club", "httpsPort": 30010},
    )
    assert response.status == 200

    with patch("custom_components.tether.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UTOKEN] == utoken
    assert result["data"][CONF_CONNECTION_INFO] == {
        "domain": "192-0-2-44.lovense.club",
        "httpsPort": 30010,
    }
    assert USER_ID not in hass.data[DOMAIN][PENDING_PAIRINGS]


async def test_standard_flow_can_skip_pairing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(API_GET_QRCODE, json=QR_RESPONSE)
    result = await _start(hass, CONNECTION_STANDARD)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], STANDARD_INPUT)
    with patch("custom_components.tether.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"skip_pairing_check": True}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_CONNECTION_INFO not in result["data"]


async def test_standard_flow_reports_missing_pairing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(API_GET_QRCODE, json=QR_RESPONSE)
    result = await _start(hass, CONNECTION_STANDARD)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], STANDARD_INPUT)
    with patch("custom_components.tether.config_flow.PAIRING_WAIT_TIMEOUT", 0):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": "pairing_not_received"}

    hass.config_entries.flow.async_abort(result["flow_id"])
    assert USER_ID not in hass.data[DOMAIN][PENDING_PAIRINGS]


async def test_standard_flow_validates_callback_and_qr(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    result = await _start(hass, CONNECTION_STANDARD)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**STANDARD_INPUT, "callback_url": "ftp://user:pw@host"}
    )
    assert result["errors"] == {"base": "invalid_callback_url"}

    aioclient_mock.post(API_GET_QRCODE, json={"code": 0, "data": {"qr": "http://insecure"}})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], STANDARD_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_local(
    hass: HomeAssistant, local_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    new_host = "192.0.2.20"
    aioclient_mock.post(f"http://{new_host}:20010/command", json=toys_response())
    result = await local_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_LOCAL}
    )
    assert result["step_id"] == "reconfigure_local"
    with patch("custom_components.tether.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**LOCAL_INPUT, "host": new_host}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert local_entry.unique_id == f"local:{new_host}:20010"
    assert local_entry.data["host"] == new_host


async def test_reconfigure_rejects_another_entries_identity(
    hass: HomeAssistant, local_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    other_host = "192.0.2.30"
    MockConfigEntry(
        domain=DOMAIN, unique_id=f"local:{other_host}:20010", data={**LOCAL_INPUT, "host": other_host}
    ).add_to_hass(hass)
    aioclient_mock.post(f"http://{other_host}:20010/command", json=toys_response())
    result = await local_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_LOCAL}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**LOCAL_INPUT, "host": other_host}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "already_configured"}


async def test_options_flow(hass: HomeAssistant, local_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(local_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.tether.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_POLL_INTERVAL: 60,
                CONF_DEFAULT_TIME_SEC: 5,
                CONF_VERIFY_SSL: True,
                CONF_STROKE_CONTROL_TYPE: STROKE_CONTROL_BOTH,
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert local_entry.options[CONF_STROKE_CONTROL_TYPE] == STROKE_CONTROL_BOTH
    assert local_entry.options[CONF_POLL_INTERVAL] == 60
