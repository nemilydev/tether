"""Config, reconfigure and options flow tests."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tether.const import (
    API_GET_QRCODE,
    CONF_CONNECTION_INFO,
    CONF_CONNECTION_TYPE,
    CONF_DEFAULT_TIME_SEC,
    CONF_POLL_INTERVAL,
    CONF_STROKE_CONTROL_TYPE,
    CONF_USER_ID,
    CONF_UTOKEN,
    CONF_VERIFY_SSL,
    CONNECTION_LOCAL,
    CONNECTION_STANDARD,
    DOMAIN,
    PENDING_PAIRINGS,
    STROKE_CONTROL_BOTH,
)
from custom_components.tether.coordinator import LovenseCoordinator, connection_store
from custom_components.tether.views import LovenseCallbackView, _MAX_CALLBACK_BYTES

from .conftest import (
    LOCAL_DATA,
    LOCAL_HOST,
    LOCAL_URL,
    STANDARD_DATA,
    USER_ID,
    UTOKEN,
    toys_response,
)

LOCAL_INPUT = {"host": LOCAL_HOST, "port": 20010, "use_https": False, "verify_ssl": False}
STANDARD_INPUT = {
    "developer_token": "dev-token",
    "user_id": USER_ID,
    "user_name": "Home Assistant",
    "callback_url": "https://ha.example.com/api/tether/callback",
}
QR_RESPONSE = {"code": 0, "data": {"qr": "https://example.com/qr.png", "code": "AB12"}}
CALLBACK_PAYLOAD = {"uid": USER_ID, "utoken": UTOKEN}
CONNECTION_INFO = {"domain": "192-0-2-44.lovense.club", "httpsPort": 30010}


@pytest.fixture
def pending_pairing(hass: HomeAssistant) -> dict:
    """A pending flow lets validation tests assert that rejection has no effects."""
    pending = {"utoken": UTOKEN, "connection_info": None, "event": asyncio.Event()}
    hass.data.setdefault(DOMAIN, {})[PENDING_PAIRINGS] = {USER_ID: pending}
    return pending


async def _callback(hass: HomeAssistant, body: bytes, *, streamed: bool = False):
    """Feed real bytes in small chunks, including requests without Content-Length."""
    request = Mock()
    request.content_length = None if streamed else len(body)
    offset = 0

    async def read(size: int) -> bytes:
        nonlocal offset
        chunk = body[offset : offset + min(size, 8192)]
        offset += len(chunk)
        return chunk

    request.content.read = AsyncMock(side_effect=read)
    return await LovenseCallbackView(hass).post(request)


@pytest.mark.parametrize(
    "body,status",
    [
        (b"{invalid", 400),
        (b"\xff", 400),
        (b"", 400),
        (b"[" * 2000 + b"]" * 2000, 400),
        *[(json.dumps(value).encode(), 400) for value in (None, [], "text", 1, True)],
        *[
            (json.dumps(value).encode(), 400)
            for value in (
                {}, {"uid": USER_ID}, {"utoken": UTOKEN},
                {**CALLBACK_PAYLOAD, "uid": None},
                {**CALLBACK_PAYLOAD, "uid": 1},
                {**CALLBACK_PAYLOAD, "utoken": []},
                {**CALLBACK_PAYLOAD, "uid": ""},
                {**CALLBACK_PAYLOAD, "utoken": ""},
            )
        ],
        (json.dumps({**CALLBACK_PAYLOAD, "uid": "unknown"}).encode(), 401),
        (json.dumps({**CALLBACK_PAYLOAD, "utoken": "wrong"}).encode(), 401),
        (json.dumps({**CALLBACK_PAYLOAD, "utoken": "\ud800"}).encode(), 401),
    ],
)
async def test_callback_rejects_invalid_body_and_credentials(
    hass: HomeAssistant, pending_pairing: dict, body: bytes, status: int
) -> None:
    response = await _callback(hass, body)
    assert response.status == status
    assert pending_pairing["connection_info"] is None
    assert not pending_pairing["event"].is_set()


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("extra,status", [(0, 200), (1, 413)])
async def test_callback_size_boundary(
    hass: HomeAssistant, pending_pairing: dict, streamed: bool, extra: int, status: int
) -> None:
    body = json.dumps(CALLBACK_PAYLOAD).encode()
    body += b" " * (_MAX_CALLBACK_BYTES - len(body) + extra)
    response = await _callback(hass, body, streamed=streamed)
    assert response.status == status
    assert pending_pairing["event"].is_set() is (status == 200)


async def test_callback_rejects_incomplete_body(
    hass: HomeAssistant, pending_pairing: dict
) -> None:
    request = Mock(content_length=None)
    request.content.read = AsyncMock(side_effect=aiohttp.ClientPayloadError())
    response = await LovenseCallbackView(hass).post(request)
    assert response.status == 400
    assert not pending_pairing["event"].is_set()


@pytest.mark.parametrize(
    "domain",
    [
        "", 123, [], "lovense.club", "evil.example.com", "evil.lovense.club.example.com",
        "https://192-0-2-44.lovense.club", "user@192-0-2-44.lovense.club",
        "192-0-2-44.lovense.club:30010", ".lovense.club", "-bad.lovense.club",
        "bad-.lovense.club", "a" * 64 + ".lovense.club", "a..lovense.club",
        "\u212a.lovense.club", ("a" * 63 + ".") * 4 + "lovense.club",
    ],
)
async def test_callback_rejects_invalid_domain(
    hass: HomeAssistant, pending_pairing: dict, domain
) -> None:
    response = await _callback(hass, json.dumps({**CALLBACK_PAYLOAD, "domain": domain}).encode())
    assert response.status == 400
    assert not pending_pairing["event"].is_set()


@pytest.mark.parametrize("key", ["httpPort", "httpsPort", "wsPort", "wssPort"])
@pytest.mark.parametrize(
    "port",
    [True, False, 0, -1, 65536, "", "bad", "1.5", 1.5, 30010.0, float("inf"), [], {}],
)
async def test_callback_rejects_invalid_ports(
    hass: HomeAssistant, pending_pairing: dict, key: str, port
) -> None:
    response = await _callback(hass, json.dumps({**CALLBACK_PAYLOAD, key: port}).encode())
    assert response.status == 400
    assert not pending_pairing["event"].is_set()


@pytest.mark.parametrize(
    "toys",
    [
        None, [], 1, True, "", "{invalid", "null", "[]", '{"toy": []}',
        {"toy": []}, {"toy": "bad"}, {"good": {"name": "lush"}, "bad": None},
        {"toys": []}, {"toys": '{"good": {}, "bad": 1}'},
    ],
)
async def test_callback_rejects_malformed_toy_collections(
    hass: HomeAssistant, pending_pairing: dict, toys
) -> None:
    response = await _callback(hass, json.dumps({**CALLBACK_PAYLOAD, "toys": toys}).encode())
    assert response.status == 400
    assert not pending_pairing["event"].is_set()


@pytest.mark.parametrize(
    "toys",
    [{}, "{}", {"toys": {}}, '{"toys": {}}', {"toy": {"name": "lush"}}, '{"toy": {"name": "lush"}}'],
)
async def test_callback_accepts_valid_and_empty_toy_collections(
    hass: HomeAssistant, pending_pairing: dict, toys
) -> None:
    payload = {**CALLBACK_PAYLOAD, **CONNECTION_INFO, "toys": toys}
    response = await _callback(hass, json.dumps(payload).encode())
    assert response.status == 200
    assert pending_pairing["event"].is_set()
    assert pending_pairing["connection_info"] == CONNECTION_INFO


@pytest.mark.parametrize("port", [1, 65535, "30010", None])
async def test_callback_accepts_valid_ports_and_case_insensitive_domain(
    hass: HomeAssistant, pending_pairing: dict, port
) -> None:
    payload = {**CALLBACK_PAYLOAD, "domain": "192-0-2-44.LOVENSE.club", "httpsPort": port}
    response = await _callback(hass, json.dumps(payload).encode())
    assert response.status == 200
    assert pending_pairing["event"].is_set()


@pytest.mark.parametrize(
    "uid,token",
    [
        ("caf\u00e9", "cl\u00e9"),
        ("user\x00id", "secret\x00token"),
        (" user ", " token "),
        ("user", "\ud800"),
    ],
)
@pytest.mark.parametrize("target", ["pending", "active", "retry"])
async def test_callback_compares_credentials_exactly(
    hass: HomeAssistant, uid: str, token: str, target: str
) -> None:
    if target == "pending":
        pending = {"utoken": token, "event": asyncio.Event(), "connection_info": None}
        hass.data.setdefault(DOMAIN, {})[PENDING_PAIRINGS] = {uid: pending}
    elif target == "active":
        coordinator = _active_entry(hass, uid, token)
    else:
        entry = _retry_entry(hass, uid, token)
    for bad in (token + "x", token[:-1]):
        response = await _callback(hass, json.dumps({"uid": uid, "utoken": bad}).encode())
        assert response.status == (401 if bad else 400)
    if target == "pending":
        assert not pending["event"].is_set()
    elif target == "active":
        coordinator.update_device_info.assert_not_called()
    else:
        assert await connection_store(hass, entry.entry_id).async_load() is None
    payload = {"uid": uid, "utoken": token, **CONNECTION_INFO}
    response = await _callback(hass, json.dumps(payload).encode())
    assert response.status == 200
    if target == "pending":
        assert pending["event"].is_set()
    elif target == "active":
        coordinator.update_device_info.assert_called_once_with(payload)
    else:
        assert await connection_store(hass, entry.entry_id).async_load() == CONNECTION_INFO


async def test_callback_does_not_normalize_unicode_credentials(
    hass: HomeAssistant, pending_pairing: dict
) -> None:
    pending_pairing["utoken"] = "caf\u00e9"
    body = json.dumps({**CALLBACK_PAYLOAD, "utoken": "cafe\u0301"}).encode()
    assert (await _callback(hass, body)).status == 401
    assert not pending_pairing["event"].is_set()


def _active_entry(hass: HomeAssistant, uid: str, token: str) -> LovenseCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={**STANDARD_DATA, CONF_USER_ID: uid, CONF_UTOKEN: token})
    entry.add_to_hass(hass)
    coordinator = Mock(spec=LovenseCoordinator)
    coordinator.config = entry.data
    entry.runtime_data = coordinator
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return coordinator


def _retry_entry(hass: HomeAssistant, uid: str, token: str) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={**STANDARD_DATA, CONF_USER_ID: uid, CONF_UTOKEN: token})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    return entry


@pytest.mark.parametrize(
    "targets",
    [
        ("active", "active"), ("retry", "retry"), ("active", "retry"),
        ("active", "pending"), ("retry", "pending"),
    ],
)
async def test_callback_rejects_ambiguous_credentials(
    hass: HomeAssistant, targets: tuple[str, str]
) -> None:
    coordinators = []
    entries = []
    pending = None
    for target in targets:
        if target == "active":
            coordinators.append(_active_entry(hass, USER_ID, UTOKEN))
        elif target == "retry":
            entries.append(_retry_entry(hass, USER_ID, UTOKEN))
        else:
            pending = {"utoken": UTOKEN, "event": asyncio.Event(), "connection_info": None}
            hass.data.setdefault(DOMAIN, {})[PENDING_PAIRINGS] = {USER_ID: pending}
    response = await _callback(hass, json.dumps({**CALLBACK_PAYLOAD, **CONNECTION_INFO}).encode())
    assert response.status == 401
    for coordinator in coordinators:
        coordinator.update_device_info.assert_not_called()
    for entry in entries:
        assert await connection_store(hass, entry.entry_id).async_load() is None
    if pending is not None:
        assert not pending["event"].is_set()


@pytest.mark.parametrize("target", ["active", "retry"])
async def test_callback_routes_same_uid_by_token(hass: HomeAssistant, target: str) -> None:
    first = _active_entry(hass, USER_ID, "other-token")
    if target == "active":
        second = _active_entry(hass, USER_ID, UTOKEN)
    else:
        entry = _retry_entry(hass, USER_ID, UTOKEN)
    payload = {**CALLBACK_PAYLOAD, **CONNECTION_INFO}
    response = await _callback(hass, json.dumps(payload).encode())
    assert response.status == 200
    first.update_device_info.assert_not_called()
    if target == "active":
        second.update_device_info.assert_called_once_with(payload)
    else:
        assert await connection_store(hass, entry.entry_id).async_load() == CONNECTION_INFO


@pytest.mark.parametrize(
    "state",
    [ConfigEntryState.NOT_LOADED, ConfigEntryState.SETUP_ERROR, ConfigEntryState.SETUP_RETRY],
)
@pytest.mark.parametrize("connection_type", [CONNECTION_LOCAL, CONNECTION_STANDARD])
async def test_callback_only_authenticates_standard_retry_entries(
    hass: HomeAssistant, state: ConfigEntryState, connection_type: str
) -> None:
    entry = _retry_entry(hass, USER_ID, UTOKEN)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_CONNECTION_TYPE: connection_type})
    entry.mock_state(hass, state)
    response = await _callback(hass, json.dumps({**CALLBACK_PAYLOAD, **CONNECTION_INFO, "toys": {}}).encode())
    authorized = state is ConfigEntryState.SETUP_RETRY and connection_type == CONNECTION_STANDARD
    assert response.status == (200 if authorized else 401)
    assert await connection_store(hass, entry.entry_id).async_load() == (CONNECTION_INFO if authorized else None)
    assert getattr(entry, "runtime_data", None) is None


async def test_retry_callback_without_address_does_not_overwrite_store(hass: HomeAssistant) -> None:
    entry = _retry_entry(hass, USER_ID, UTOKEN)
    await connection_store(hass, entry.entry_id).async_save(CONNECTION_INFO)
    response = await _callback(hass, json.dumps({**CALLBACK_PAYLOAD, "toys": {}}).encode())
    assert response.status == 200
    assert await connection_store(hass, entry.entry_id).async_load() == CONNECTION_INFO


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


@pytest.mark.parametrize("finish", ["abort", "skip", "pair"])
async def test_finished_pairing_rejects_late_callbacks(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, finish: str
) -> None:
    aioclient_mock.post(API_GET_QRCODE, json=QR_RESPONSE)
    result = await _start(hass, CONNECTION_STANDARD)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], STANDARD_INPUT)
    pending = hass.data[DOMAIN][PENDING_PAIRINGS][USER_ID]
    body = json.dumps({**CALLBACK_PAYLOAD, "utoken": pending["utoken"], **CONNECTION_INFO}).encode()
    if finish == "pair":
        assert (await _callback(hass, body)).status == 200
    if finish == "abort":
        hass.config_entries.flow.async_abort(result["flow_id"])
    else:
        with patch("custom_components.tether.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"skip_pairing_check": finish == "skip"}
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    assert USER_ID not in hass.data[DOMAIN][PENDING_PAIRINGS]
    assert (await _callback(hass, body)).status == 401


async def test_replaced_pairing_rejects_stale_callback_and_preserves_new_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(API_GET_QRCODE, json=QR_RESPONSE)
    result = await _start(hass, CONNECTION_STANDARD)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], STANDARD_INPUT)
    old_token = hass.data[DOMAIN][PENDING_PAIRINGS][USER_ID]["utoken"]
    # A new QR request replaces the previous pending credentials for this user.
    # The flow is on the pair step, so async_configure validates against _PAIR_SCHEMA.
    # Call the standard step directly on the handler to re-request a QR code.
    handler = next(
        h for h in hass.config_entries.flow._progress.values()
        if h.flow_id == result["flow_id"]
    )
    await handler.async_step_standard(STANDARD_INPUT)
    pending = hass.data[DOMAIN][PENDING_PAIRINGS][USER_ID]
    assert pending["utoken"] != old_token
    body = json.dumps({**CALLBACK_PAYLOAD, "utoken": old_token}).encode()
    assert (await _callback(hass, body)).status == 401
    assert not pending["event"].is_set()
    body = json.dumps({**CALLBACK_PAYLOAD, "utoken": pending["utoken"], **CONNECTION_INFO}).encode()
    assert (await _callback(hass, body)).status == 200
    with patch("custom_components.tether.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"][CONF_CONNECTION_INFO] == CONNECTION_INFO


@pytest.mark.parametrize(
    "qr_response",
    [
        None, [], "bad", {"code": 500, "message": "offline"},
        {"code": 0}, {"code": 0, "data": []}, {"code": 0, "data": "bad"},
        {"code": 0, "data": {"qr": "https://"}},
        {"code": 0, "data": {"qr": "https://example.com:bad/qr"}},
        {"code": 0, "data": {"qr": None, "code": "AB12"}},
    ],
)
async def test_standard_flow_rejects_malformed_qr_responses(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, qr_response
) -> None:
    aioclient_mock.post(API_GET_QRCODE, text=json.dumps(qr_response))
    result = await _start(hass, CONNECTION_STANDARD)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], STANDARD_INPUT)
    assert result["step_id"] == "standard"
    assert result["errors"] == {"base": "cannot_connect"}
    assert USER_ID not in hass.data.get(DOMAIN, {}).get(PENDING_PAIRINGS, {})


@pytest.mark.parametrize("code", [0, "0", 200, "200"])
async def test_standard_flow_uses_qr_fallback_with_missing_pairing_code(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, code
) -> None:
    aioclient_mock.post(API_GET_QRCODE, json={
        "code": code,
        "data": {"qr": "https://", "qrcodeUrl": "https://example.com/qr.png", "code": False},
    })
    result = await _start(hass, CONNECTION_STANDARD)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], STANDARD_INPUT)
    assert result["step_id"] == "pair"
    assert "https://example.com/qr.png" in result["description_placeholders"]["qr_code"]
    assert result["description_placeholders"]["pairing_code"] == "-"
    hass.config_entries.flow.async_abort(result["flow_id"])


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
