"""Target-aware services for Tether."""
from __future__ import annotations

import asyncio
import re
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_extract_entity_ids

from .const import (
    CMD_FUNCTION,
    CMD_PATTERN,
    CMD_PRESET,
    CONF_DEFAULT_TIME_SEC,
    DOMAIN,
    FUNCTION_LIMITS,
    PRESETS,
    VIBRATE_MAX,
)
from .coordinator import LovenseCoordinator, active_coordinator
from .entity import toy_device_identifier

SERVICE_PLAY_PATTERN = "play_pattern"
SERVICE_PLAY_PRESET = "play_preset"
SERVICE_SEND_FUNCTION = "send_function"
SERVICE_STOP_ALL = "stop_all"
SERVICE_RAMP = "ramp"

MAX_PATTERN_STEPS = 50
_ACTION_RE = re.compile(r"^([A-Za-z]+[0-9]?):([0-9]+(?:-[0-9]+)?)$")
_LEVEL_LIMITS = {**FUNCTION_LIMITS, "All": VIBRATE_MAX}
_SECONDS = vol.All(vol.Coerce(int), vol.Range(min=0, max=3600))
_RAMP_SECONDS = vol.All(vol.Coerce(int), vol.Range(min=0, max=300))


def validate_action(action: str) -> str:
    """Validate a Lovense Function action such as ``Vibrate:10,Rotate:5``."""
    action = action.strip()
    if not action or len(action) > 256:
        raise vol.Invalid("Action must be a non-empty Lovense function string")
    if action == "Stop":
        return action
    functions: set[str] = set()
    for part in action.split(","):
        match = _ACTION_RE.fullmatch(part.strip())
        if not match:
            raise vol.Invalid(f"Invalid function action: {part}")
        name, raw_value = match.groups()
        functions.add(name)
        if name == "Stroke":
            if "-" in raw_value:
                lower, upper = (int(value) for value in raw_value.split("-", 1))
                if lower < 0 or upper > 100 or upper - lower < 20:
                    raise vol.Invalid("Stroke range must be 0-100 and span at least 20")
            elif int(raw_value) > 100:
                raise vol.Invalid("Stroke must be between 0 and 100")
            continue
        if "-" in raw_value or name not in _LEVEL_LIMITS or int(raw_value) > _LEVEL_LIMITS[name]:
            raise vol.Invalid(f"Unsupported function or out-of-range value: {part}")
    if "Stroke" in functions and "Thrusting" not in functions:
        raise vol.Invalid("Stroke commands must include a Thrusting action")
    return ",".join(part.strip() for part in action.split(","))


def validate_pattern(pattern: str) -> str:
    """Validate a semicolon-separated list of 0-20 strength steps."""
    values = [value.strip() for value in pattern.strip().strip(";").split(";")]
    if not values or len(values) > MAX_PATTERN_STEPS:
        raise vol.Invalid(f"Pattern must contain 1 to {MAX_PATTERN_STEPS} steps")
    if any(not value.isdigit() or int(value) > VIBRATE_MAX for value in values):
        raise vol.Invalid(f"Pattern steps must be whole numbers from 0 to {VIBRATE_MAX}")
    return ";".join(values)


SCHEMAS: dict[str, vol.Schema] = {
    SERVICE_PLAY_PATTERN: cv.make_entity_service_schema(
        {
            vol.Required("pattern"): vol.All(cv.string, validate_pattern),
            vol.Optional("interval", default=1000): vol.All(
                vol.Coerce(int), vol.Range(min=100, max=5000)
            ),
            vol.Optional("time_sec"): _SECONDS,
            vol.Optional("loop_running_sec", default=0): _SECONDS,
            vol.Optional("loop_pause_sec", default=0): _SECONDS,
        }
    ),
    SERVICE_PLAY_PRESET: cv.make_entity_service_schema(
        {
            vol.Required("name"): vol.In(PRESETS),
            vol.Optional("time_sec"): _SECONDS,
        }
    ),
    SERVICE_SEND_FUNCTION: cv.make_entity_service_schema(
        {
            vol.Required("action"): vol.All(cv.string, validate_action),
            vol.Optional("time_sec"): _SECONDS,
            vol.Optional("loop_running_sec", default=0): _SECONDS,
            vol.Optional("loop_pause_sec", default=0): _SECONDS,
            vol.Optional("stop_previous", default=False): vol.All(cv.boolean, int),
        }
    ),
    SERVICE_STOP_ALL: cv.make_entity_service_schema({}),
    SERVICE_RAMP: cv.make_entity_service_schema(
        {
            vol.Optional("peak", default=VIBRATE_MAX): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=VIBRATE_MAX)
            ),
            vol.Optional("climb", default=30): _RAMP_SECONDS,
            vol.Optional("hold", default=30): _RAMP_SECONDS,
            vol.Optional("decay", default=30): _RAMP_SECONDS,
        }
    ),
}


async def _resolve_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[LovenseCoordinator, str]]:
    """Resolve the service target to unique (coordinator, toy ID) pairs."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    resolved: dict[tuple[str, str], tuple[LovenseCoordinator, str]] = {}
    for entity_id in await async_extract_entity_ids(hass, call):
        entity = entity_registry.async_get(entity_id)
        if entity is None or entity.platform != DOMAIN:
            continue
        coordinator = active_coordinator(
            hass.config_entries.async_get_entry(entity.config_entry_id or "")
        )
        device = device_registry.async_get(entity.device_id) if entity.device_id else None
        if coordinator is None or device is None:
            continue
        entry_id = coordinator.config_entry.entry_id
        prefix = toy_device_identifier(entry_id, "")
        toy_id = next(
            (
                identifier.removeprefix(prefix)
                for domain, identifier in device.identifiers
                if domain == DOMAIN and identifier.startswith(prefix)
            ),
            None,
        )
        if toy_id:
            resolved[(entry_id, toy_id)] = (coordinator, toy_id)
    if not resolved:
        raise ServiceValidationError(
            "Select at least one entity of a loaded Tether toy",
            translation_domain=DOMAIN,
            translation_key="no_toy_targets",
        )
    return list(resolved.values())


def _time_sec(call: ServiceCall, coordinator: LovenseCoordinator) -> int:
    return int(call.data.get("time_sec", coordinator.options.get(CONF_DEFAULT_TIME_SEC, 0)))


async def _ramp_toy(
    coordinator: LovenseCoordinator, toy_id: str, peak: int, climb: int, hold: int, decay: int
) -> None:
    """Step vibration up to ``peak``, hold it, then step back down to zero."""
    steps = min(10, peak)

    async def vibrate(level: int) -> None:
        await coordinator.send_command_local(
            CMD_FUNCTION, action=f"Vibrate:{level}", timeSec=0, stopPrevious=0, toy=toy_id
        )

    for step in range(1, steps + 1):
        await vibrate(round(peak * step / steps))
        if climb:
            await asyncio.sleep(climb / steps)
    if hold:
        await asyncio.sleep(hold)
    for step in range(steps - 1, 0, -1):
        await vibrate(round(peak * step / steps))
        if decay:
            await asyncio.sleep(decay / max(steps - 1, 1))
    await vibrate(0)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Tether services; called once from ``async_setup``."""

    async def play_pattern(call: ServiceCall) -> None:
        interval = call.data["interval"]
        for coordinator, toy_id in await _resolve_targets(hass, call):
            await coordinator.send_command_local(
                CMD_PATTERN,
                rule=f"V:1;F:v;S:{interval}#",
                strength=call.data["pattern"],
                timeSec=_time_sec(call, coordinator),
                loopRunningSec=call.data["loop_running_sec"],
                loopPauseSec=call.data["loop_pause_sec"],
                toy=toy_id,
                apiVer=2,
            )

    async def play_preset(call: ServiceCall) -> None:
        for coordinator, toy_id in await _resolve_targets(hass, call):
            await coordinator.send_command_local(
                CMD_PRESET,
                name=call.data["name"],
                timeSec=_time_sec(call, coordinator),
                toy=toy_id,
                apiVer=1,
            )

    async def send_function(call: ServiceCall) -> None:
        for coordinator, toy_id in await _resolve_targets(hass, call):
            await coordinator.send_command_local(
                CMD_FUNCTION,
                action=call.data["action"],
                timeSec=_time_sec(call, coordinator),
                loopRunningSec=call.data["loop_running_sec"],
                loopPauseSec=call.data["loop_pause_sec"],
                stopPrevious=call.data["stop_previous"],
                toy=toy_id,
                apiVer=1,
            )

    async def stop_all(call: ServiceCall) -> None:
        for coordinator, toy_id in await _resolve_targets(hass, call):
            await coordinator.send_command_local(
                CMD_FUNCTION, action="Stop", timeSec=0, toy=toy_id, apiVer=1
            )

    async def ramp(call: ServiceCall) -> None:
        targets = await _resolve_targets(hass, call)
        results = await asyncio.gather(
            *(
                _ramp_toy(
                    coordinator,
                    toy_id,
                    call.data["peak"],
                    call.data["climb"],
                    call.data["hold"],
                    call.data["decay"],
                )
                for coordinator, toy_id in targets
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    handlers: dict[str, Any] = {
        SERVICE_PLAY_PATTERN: play_pattern,
        SERVICE_PLAY_PRESET: play_preset,
        SERVICE_SEND_FUNCTION: send_function,
        SERVICE_STOP_ALL: stop_all,
        SERVICE_RAMP: ramp,
    }
    for service, handler in handlers.items():
        hass.services.async_register(DOMAIN, service, handler, schema=SCHEMAS[service])
