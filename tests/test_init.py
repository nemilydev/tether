"""Entry lifecycle, entity, service, callback view and diagnostics tests."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import service as service_helper
from homeassistant.util.yaml import load_yaml_dict

from custom_components.tether.const import API_COMMAND_SERVER, DOMAIN
from custom_components.tether.coordinator import active_coordinator, connection_store
from custom_components.tether.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    LOCAL_DATA,
    LOCAL_HOST,
    LOCAL_URL,
    STANDARD_LAN_URLS,
    TOY_ID,
    USER_ID,
    UTOKEN,
    toy,
    toys_response,
)

CALLBACK = "/api/tether/callback"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, entry: MockConfigEntry, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{TOY_ID}_{key}"
    )
    assert entity_id is not None, f"{platform} {key} was not created"
    return entity_id


def _commands(aioclient_mock: AiohttpClientMocker) -> list[dict[str, Any]]:
    return [call[2] for call in aioclient_mock.mock_calls if call[2]["command"] != "GetToys"]


async def test_setup_creates_toy_entities(
    hass: HomeAssistant, local_entry: MockConfigEntry, mock_local_api: AiohttpClientMocker
) -> None:
    await _setup(hass, local_entry)
    assert local_entry.state is ConfigEntryState.LOADED

    light = hass.states.get(_entity_id(hass, "light", local_entry, "vibration"))
    assert light.name.endswith("Vibration")
    assert hass.states.get(_entity_id(hass, "number", local_entry, "vibrate")) is not None
    assert hass.states.get(_entity_id(hass, "select", local_entry, "preset")) is not None
    assert hass.states.get(_entity_id(hass, "button", local_entry, "stop")) is not None
    battery = hass.states.get(_entity_id(hass, "sensor", local_entry, "battery"))
    assert battery.state == "80"
    assert battery.name.endswith("Battery")
    connectivity = hass.states.get(
        _entity_id(hass, "binary_sensor", local_entry, "connectivity")
    )
    assert connectivity.state == "on"

    registry = er.async_get(hass)
    for key, platform in (("battery", "sensor"), ("connectivity", "binary_sensor")):
        entry = registry.async_get(_entity_id(hass, platform, local_entry, key))
        assert entry.entity_category == "diagnostic"

    devices = dr.async_get(hass)
    hub = devices.async_get_device(identifiers={(DOMAIN, local_entry.entry_id)})
    toy_device = devices.async_get_device(
        identifiers={(DOMAIN, f"{local_entry.entry_id}_{TOY_ID}")}
    )
    assert toy_device.via_device_id == hub.id
    assert toy_device.manufacturer == "Lovense"
    # Toys without Pump or Thrusting do not get those controls.
    assert registry.async_get_entity_id("light", DOMAIN, f"{local_entry.entry_id}_{TOY_ID}_pump") is None


@pytest.mark.parametrize(
    ("stroke_type", "light", "number"),
    [("lights", True, False), ("numbers", False, True), ("both", True, True)],
)
async def test_stroke_control_option_selects_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    stroke_type: str,
    light: bool,
    number: bool,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"local:{LOCAL_HOST}:20010",
        data=LOCAL_DATA,
        options={"stroke_control_type": stroke_type},
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(
        LOCAL_URL,
        json=toys_response(toy(name="solace pro", fullFunctionNames=["Thrusting", "Stroke"])),
    )
    await _setup(hass, entry)
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_{TOY_ID}"
    assert bool(registry.async_get_entity_id("light", DOMAIN, f"{prefix}_stroke")) is light
    assert bool(registry.async_get_entity_id("number", DOMAIN, f"{prefix}_stroke")) is number
    assert registry.async_get_entity_id("number", DOMAIN, f"{prefix}_thrusting")
    assert registry.async_get_entity_id("number", DOMAIN, f"{prefix}_position")
    assert registry.async_get_entity_id("light", DOMAIN, f"{prefix}_vibration") is None


async def test_setup_retries_when_remote_is_unreachable(
    hass: HomeAssistant, local_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LOCAL_URL, exc=aiohttp.ClientError())
    await hass.config_entries.async_setup(local_entry.entry_id)
    await hass.async_block_till_done()
    assert local_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_and_reload_keep_services(
    hass: HomeAssistant, local_entry: MockConfigEntry, mock_local_api: AiohttpClientMocker
) -> None:
    await _setup(hass, local_entry)
    assert hass.services.has_service(DOMAIN, "send_function")

    assert await hass.config_entries.async_reload(local_entry.entry_id)
    await hass.async_block_till_done()
    assert local_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(local_entry.entry_id)
    await hass.async_block_till_done()
    assert local_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.services.has_service(DOMAIN, "stop_all")


async def test_multiple_entries_load_side_by_side(
    hass: HomeAssistant,
    local_entry: MockConfigEntry,
    standard_entry: MockConfigEntry,
    mock_local_api: AiohttpClientMocker,
    mock_standard_api: AiohttpClientMocker,
) -> None:
    # Setting up the integration loads every Tether entry.
    await _setup(hass, local_entry)
    assert local_entry.state is ConfigEntryState.LOADED
    assert standard_entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    for entry in (local_entry, standard_entry):
        assert registry.async_get_entity_id(
            "light", DOMAIN, f"{entry.entry_id}_{TOY_ID}_vibration"
        )

    assert await hass.config_entries.async_unload(local_entry.entry_id)
    assert standard_entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "send_function")


async def test_standard_entry_falls_back_to_cloud_commands(
    hass: HomeAssistant, standard_entry: MockConfigEntry, mock_standard_api: AiohttpClientMocker
) -> None:
    await _setup(hass, standard_entry)
    await hass.services.async_call(
        DOMAIN,
        "play_preset",
        {"entity_id": _entity_id(hass, "light", standard_entry, "vibration"), "name": "pulse"},
        blocking=True,
    )
    last_url, last_payload = mock_standard_api.mock_calls[-1][1:3]
    assert str(last_url) == API_COMMAND_SERVER
    assert last_payload["command"] == "Preset"
    assert last_payload["name"] == "pulse"
    assert last_payload["token"] == "dev-token"


def test_services_yaml_matches_home_assistant_schema() -> None:
    services = load_yaml_dict(
        str(Path(__file__).parents[1] / "custom_components" / "tether" / "services.yaml")
    )
    validated = service_helper._SERVICES_SCHEMA(services)
    assert set(validated) == {"play_pattern", "play_preset", "send_function", "stop_all", "ramp"}
    assert "target" in validated["stop_all"]
    assert validated["send_function"]["fields"]["action"]["required"] is True


async def test_services_send_commands_to_targeted_toys(
    hass: HomeAssistant, local_entry: MockConfigEntry, mock_local_api: AiohttpClientMocker
) -> None:
    await _setup(hass, local_entry)
    light = _entity_id(hass, "light", local_entry, "vibration")

    await hass.services.async_call(
        DOMAIN,
        "send_function",
        {"entity_id": light, "action": "Vibrate:10", "time_sec": 5},
        blocking=True,
    )
    await hass.services.async_call(DOMAIN, "stop_all", {"entity_id": light}, blocking=True)

    commands = _commands(mock_local_api)
    assert commands[0]["action"] == "Vibrate:10"
    assert commands[0]["toy"] == TOY_ID
    assert commands[0]["timeSec"] == 5
    assert commands[1]["action"] == "Stop"


async def test_services_reject_calls_without_tether_targets(
    hass: HomeAssistant, local_entry: MockConfigEntry, mock_local_api: AiohttpClientMocker
) -> None:
    await _setup(hass, local_entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "stop_all", {"entity_id": "light.not_tether"}, blocking=True
        )
    assert err.value.translation_key == "no_toy_targets"


async def test_ramp_waits_for_sibling_targets_before_reporting_failure(
    hass: HomeAssistant,
    local_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.post(LOCAL_URL, json=toys_response(toy(), toy(id="toy2")))
    await _setup(hass, local_entry)
    failed = asyncio.Event()
    sibling_started = asyncio.Event()
    release_sibling = asyncio.Event()
    sibling_actions: list[str] = []

    async def send(command: str, **kwargs: Any) -> dict[str, Any]:
        if kwargs["toy"] == TOY_ID:
            failed.set()
            raise HomeAssistantError("Toy command failed")
        await failed.wait()
        sibling_started.set()
        await release_sibling.wait()
        sibling_actions.append(kwargs["action"])
        return {"code": 200}

    with patch.object(local_entry.runtime_data, "send_command_local", side_effect=send):
        task = asyncio.create_task(
            hass.services.async_call(
                DOMAIN,
                "ramp",
                {
                    "entity_id": [
                        _entity_id(hass, "light", local_entry, "vibration"),
                        er.async_get(hass).async_get_entity_id(
                            "light", DOMAIN, f"{local_entry.entry_id}_toy2_vibration"
                        ),
                    ],
                    "peak": 2,
                    "climb": 0,
                    "hold": 0,
                    "decay": 0,
                },
                blocking=True,
            )
        )
        try:
            await asyncio.wait_for(sibling_started.wait(), timeout=1)
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release_sibling.set()
            results = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(results[0], HomeAssistantError)
    assert sibling_actions == ["Vibrate:1", "Vibrate:2", "Vibrate:1", "Vibrate:0"]


async def test_services_validate_actions(
    hass: HomeAssistant, local_entry: MockConfigEntry, mock_local_api: AiohttpClientMocker
) -> None:
    await _setup(hass, local_entry)
    light = _entity_id(hass, "light", local_entry, "vibration")
    for action in ("Vibrate:99", "Explode:1", "Stroke:0-50"):
        with pytest.raises(Exception):
            await hass.services.async_call(
                DOMAIN, "send_function", {"entity_id": light, "action": action}, blocking=True
            )
    assert _commands(mock_local_api) == []


async def test_light_turns_toy_on_and_off(
    hass: HomeAssistant, local_entry: MockConfigEntry, mock_local_api: AiohttpClientMocker
) -> None:
    await _setup(hass, local_entry)
    light = _entity_id(hass, "light", local_entry, "vibration")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": light, "brightness": 255}, blocking=True
    )
    await hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True)
    commands = _commands(mock_local_api)
    assert len(commands) == 2
    assert all(command["toy"] == TOY_ID for command in commands)


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b"{not json", 400),
        (b"[1, 2]", 400),
        (json.dumps({"uid": USER_ID}).encode(), 400),
        (json.dumps({"uid": USER_ID, "utoken": "wrong"}).encode(), 401),
        (json.dumps({"uid": USER_ID, "utoken": UTOKEN, "domain": "evil.example.com"}).encode(), 400),
        (json.dumps({"uid": USER_ID, "utoken": UTOKEN, "httpsPort": 70000}).encode(), 400),
        (json.dumps({"uid": USER_ID, "utoken": UTOKEN, "toys": [1]}).encode(), 400),
        (b" " * (1024 * 1024 + 1), 413),
    ],
    ids=[
        "malformed-json",
        "non-object",
        "missing-utoken",
        "wrong-utoken",
        "foreign-domain",
        "invalid-port",
        "invalid-toys",
        "oversized",
    ],
)
async def test_callback_rejects_bad_requests(
    hass: HomeAssistant,
    hass_client_no_auth,
    standard_entry: MockConfigEntry,
    mock_standard_api: AiohttpClientMocker,
    body: bytes,
    status: int,
) -> None:
    await _setup(hass, standard_entry)
    client = await hass_client_no_auth()
    response = await client.post(CALLBACK, data=body)
    assert response.status == status


async def test_callback_reads_large_bodies_completely(
    hass: HomeAssistant,
    hass_client_no_auth,
    standard_entry: MockConfigEntry,
    mock_standard_api: AiohttpClientMocker,
) -> None:
    await _setup(hass, standard_entry)
    client = await hass_client_no_auth()
    padded = {"uid": USER_ID, "utoken": UTOKEN, "padding": "x" * 300_000}
    response = await client.post(CALLBACK, json=padded)
    assert response.status == 200

    async def oversized_stream():
        for _ in range(20):
            yield b" " * 65536

    response = await client.post(CALLBACK, data=oversized_stream())
    assert response.status == 413


async def test_callback_updates_coordinator(
    hass: HomeAssistant,
    hass_client_no_auth,
    standard_entry: MockConfigEntry,
    mock_standard_api: AiohttpClientMocker,
) -> None:
    await _setup(hass, standard_entry)
    client = await hass_client_no_auth()
    response = await client.post(
        CALLBACK,
        json={
            "uid": USER_ID,
            "utoken": UTOKEN,
            "domain": "192-0-2-55.lovense.club",
            "httpsPort": 30010,
            "toys": {TOY_ID: toy(battery=42)},
        },
    )
    assert response.status == 200
    coordinator = standard_entry.runtime_data
    assert coordinator.device_info["domain"] == "192-0-2-55.lovense.club"
    assert "utoken" not in coordinator.device_info
    assert coordinator.data["toys"][TOY_ID]["battery"] == 42


async def test_callback_rejected_after_unload(
    hass: HomeAssistant,
    hass_client_no_auth,
    standard_entry: MockConfigEntry,
    mock_standard_api: AiohttpClientMocker,
) -> None:
    await _setup(hass, standard_entry)
    await hass.config_entries.async_unload(standard_entry.entry_id)
    client = await hass_client_no_auth()
    response = await client.post(CALLBACK, json={"uid": USER_ID, "utoken": UTOKEN})
    assert response.status == 401


async def test_retrying_entry_persists_authenticated_callback_for_next_setup(
    hass: HomeAssistant,
    hass_client_no_auth,
    standard_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    for url in STANDARD_LAN_URLS:
        aioclient_mock.post(url, exc=aiohttp.ClientError())
    aioclient_mock.post(API_COMMAND_SERVER, exc=aiohttp.ClientError())
    assert not await hass.config_entries.async_setup(standard_entry.entry_id)
    await hass.async_block_till_done()
    assert standard_entry.state is ConfigEntryState.SETUP_RETRY
    assert active_coordinator(standard_entry) is None

    client = await hass_client_no_auth()
    connection = {"domain": "192-0-2-55.lovense.club", "httpsPort": 30010}
    payload = {
        "uid": USER_ID,
        "utoken": UTOKEN,
        **connection,
        "toys": {TOY_ID: toy()},
    }
    response = await client.post(CALLBACK, json={**payload, "utoken": "wrong"})
    assert response.status == 401
    response = await client.post(CALLBACK, json={**payload, "domain": "evil.example.com"})
    assert response.status == 400
    assert await connection_store(hass, standard_entry.entry_id).async_load() is None

    response = await client.post(CALLBACK, json=payload)
    assert response.status == 200
    assert await connection_store(hass, standard_entry.entry_id).async_load() == connection
    assert standard_entry.state is ConfigEntryState.SETUP_RETRY
    assert active_coordinator(standard_entry) is None
    assert not _commands(aioclient_mock)

    aioclient_mock.post(
        "https://192-0-2-55.lovense.club:30010/command",
        json=toys_response(toy()),
    )
    async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=60))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert standard_entry.state is ConfigEntryState.LOADED
    assert standard_entry.runtime_data.device_info == connection


async def test_diagnostics_redact_sensitive_data(
    hass: HomeAssistant, standard_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    for url in STANDARD_LAN_URLS:
        aioclient_mock.post(url, exc=aiohttp.ClientError())
    aioclient_mock.post(API_COMMAND_SERVER, json=toys_response(toy(mac="AA:BB")))
    await _setup(hass, standard_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, standard_entry)
    dumped = json.dumps(diagnostics)
    for secret in (UTOKEN, "dev-token", USER_ID, "ha.example.com", "192-0-2-44", TOY_ID, "AA:BB", "My Lush"):
        assert secret not in dumped, secret
    assert diagnostics["connection"]["httpsPort"] == 30010
    assert diagnostics["coordinator"]["toys"]["toy_1"]["battery"] == 80


async def test_local_diagnostics_redact_host(
    hass: HomeAssistant, local_entry: MockConfigEntry, mock_local_api: AiohttpClientMocker
) -> None:
    await _setup(hass, local_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, local_entry)
    assert LOCAL_HOST not in json.dumps(diagnostics)
