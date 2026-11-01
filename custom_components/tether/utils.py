"""Pure helpers for Lovense payloads; no Home Assistant imports."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

from .const import CMD_FUNCTION, CONF_STROKE_CONTROL_TYPE, STROKE_CONTROL_LIGHTS

_LOGGER = logging.getLogger(__name__)


def stroke_control_type(entry: Any) -> str:
    """Return how stroke toys are exposed: options win over setup-time data."""
    return str(
        entry.options.get(
            CONF_STROKE_CONTROL_TYPE,
            entry.data.get(CONF_STROKE_CONTROL_TYPE, STROKE_CONTROL_LIGHTS),
        )
    )

# Actuator state keys and the Lovense Function action each one maps to.
ACTUATOR_ACTIONS: tuple[tuple[str, str], ...] = (
    ("vibration", "Vibrate"),
    ("stroke_range", "Stroke"),
    ("thrusting", "Thrusting"),
    ("pump", "Pump"),
)
_ACTION_TO_ACTUATOR = {action: key for key, action in ACTUATOR_ACTIONS}


def _overlaps_tracked_actuator(name: str) -> bool:
    """Return whether an untracked action, such as All or Vibrate1, moves a tracked one."""
    if name in _ACTION_TO_ACTUATOR:
        return False
    return name == "All" or name.rstrip("0123456789") in _ACTION_TO_ACTUATOR


def normalize_toys(payload: Any, *, strict: bool = False) -> dict[str, dict[str, Any]]:
    """Normalize toy collections, optionally rejecting malformed callback snapshots."""
    while True:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, RecursionError) as err:
                if strict:
                    raise ValueError("Invalid toy JSON") from err
                return {}
        if isinstance(payload, dict) and "toys" in payload:
            payload = payload["toys"]
            continue
        break
    if not isinstance(payload, dict):
        if strict:
            raise ValueError("Expected a toy mapping")
        return {}
    if strict and any(not isinstance(info, dict) for info in payload.values()):
        raise ValueError("Expected toy objects")
    return {
        str(toy_id): toy_info
        for toy_id, toy_info in payload.items()
        if isinstance(toy_info, dict)
    }


def merge_callback_toys(
    current: Mapping[str, Mapping[str, Any]],
    incoming: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Apply a callback toy snapshot without dropping fields only GetToys reports.

    The callback lists every toy known to Lovense Remote, so it decides which toys
    exist, but it omits metadata such as battery and function names.
    """
    return {
        toy_id: {**current.get(toy_id, {}), **toy_info}
        for toy_id, toy_info in incoming.items()
    }


_QR_IMAGE_KEYS = (
    "qr",
    "qrcode",
    "qrCode",
    "qrUrl",
    "qr_url",
    "qrCodeUrl",
    "qrcodeUrl",
    "qr_code",
    "qrcode_url",
    "url",
)
# Callback fields needed to reach Lovense Remote again; everything else is volatile.
CONNECTION_INFO_KEYS = (
    "domain",
    "httpPort",
    "httpsPort",
    "wsPort",
    "wssPort",
    "platform",
    "appType",
    "appVersion",
    "version",
)


def extract_qr_image_url(qr_data: Mapping[str, Any]) -> str:
    """Return the QR image URL from a getQrCode ``data`` object, if it is HTTPS."""
    for key in _QR_IMAGE_KEYS:
        value = qr_data.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            continue
        try:
            parsed = urlparse(value)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme == "https"
            and hostname
            and not any(char.isspace() for char in hostname)
            and parsed.username is None
            and parsed.password is None
            and (port is None or port > 0)
        ):
            return value
    return ""


def extract_pairing_code(qr_data: Mapping[str, Any]) -> str:
    """Return the code Lovense Remote for PC accepts instead of scanning the QR."""
    code = qr_data.get("code")
    if (
        not isinstance(code, bool)
        and isinstance(code, (str, int))
        and str(code).strip().isalnum()
    ):
        return str(code).strip()
    return ""


def qr_markdown(image_url: str) -> str:
    """Render the QR image so Home Assistant's flow dialog displays it inline."""
    if not image_url:
        return ""
    safe_url = image_url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
    return f"![Lovense pairing QR code]({safe_url})\n\n[Open the QR code in a new tab]({safe_url})"


def connection_info_from_callback(data: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the callback fields needed to reconnect to Lovense Remote."""
    return {
        key: data[key]
        for key in CONNECTION_INFO_KEYS
        if data.get(key) not in (None, "")
    }


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_function_action(
    state: Mapping[str, Any], changed: Iterable[str] = ()
) -> str:
    """Build a Function action that keeps running actuators and stops changed ones.

    Stroke only shapes an active thrust, so it is never sent without Thrusting.
    """
    changed = set(changed)
    thrusting = _int_or_zero(state.get("thrusting"))
    parts: list[str] = []
    for key, action in ACTUATOR_ACTIONS:
        value = state.get(key)
        if key == "stroke_range":
            if value is not None and thrusting:
                parts.append(f"{action}:{value}")
        elif level := _int_or_zero(value):
            parts.append(f"{action}:{level}")
        elif key in changed:
            parts.append(f"{action}:0")
    return ",".join(parts)


def next_actuator_state(
    state: Mapping[str, Any], command: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the actuator levels still running after a successful command.

    Anything that cannot be modelled is forgotten: an under-reported level is only
    not re-sent, while an over-reported one would restart a stopped actuator the
    next time combined state is sent.
    """
    if command != CMD_FUNCTION:
        return {}
    action = str(payload.get("action", "")).strip()
    if action == "Stop":
        return {}
    parts = [part.strip().partition(":") for part in action.split(",")]
    if any(_overlaps_tracked_actuator(name) for name, _, _ in parts):
        return {}
    keeps_previous = payload.get("stopPrevious", 1) in (0, "0")
    next_state = dict(state) if keeps_previous else {}
    transient = (
        _int_or_zero(payload.get("timeSec")) > 0
        or _int_or_zero(payload.get("loopRunningSec")) > 0
    )
    for name, separator, value in parts:
        key = _ACTION_TO_ACTUATOR.get(name)
        if key is None or not separator:
            continue
        if transient:
            next_state.pop(key, None)
        elif key == "stroke_range":
            next_state[key] = value
        else:
            next_state[key] = _int_or_zero(value)
    return next_state


def parse_toy_functions(toy_info: dict[str, Any]) -> list[str]:
    """Parse available functions from toy information."""
    functions = []
    
    # Get short function names (v, r, p, etc.)
    short_names = toy_info.get("shortFunctionNames", [])
    full_names = toy_info.get("fullFunctionNames", [])
    if isinstance(short_names, str):
        try:
            short_names = json.loads(short_names)
        except json.JSONDecodeError:
            short_names = [name.strip() for name in short_names.split(",")]
    if isinstance(full_names, str):
        try:
            full_names = json.loads(full_names)
        except json.JSONDecodeError:
            full_names = [name.strip() for name in full_names.split(",")]
    
    # Map short names to standard actions
    function_map = {
        "v": "Vibrate",
        "r": "Rotate", 
        "p": "Pump",
        "t": "Thrusting",
        "f": "Fingering",
        "s": "Suction",
        "d": "Depth",
        "o": "Oscillate",
    }
    canonical_names = {
        name.lower(): name
        for name in (
            "Vibrate",
            "Vibrate1",
            "Vibrate2",
            "Vibrate3",
            "Rotate",
            "Pump",
            "Thrusting",
            "Fingering",
            "Suction",
            "Depth",
            "Oscillate",
            "Stroke",
            "Position",
        )
    }
    
    # Convert short names
    for short_name in short_names if isinstance(short_names, list) else []:
        mapped = function_map.get(str(short_name).lower())
        if mapped:
            functions.append(mapped)
    
    # Add full names
    if isinstance(full_names, list):
        functions.extend(
            canonical_names.get(str(name).strip().lower(), str(name).strip())
            for name in full_names
            if name
        )
    
    # Remove duplicates and return
    return list(dict.fromkeys(functions))


def supports_position_control(toy_info: dict[str, Any]) -> bool:
    """Check if device supports position control (like Solace Pro)."""
    toy_type = str(toy_info.get("toyType", "")).lower()
    toy_name = str(toy_info.get("name", "")).lower()
    
    # Check for Solace Pro specifically
    if "solace" in toy_type or "solace" in toy_name:
        return True
    
    # Check for position-related functions
    functions = parse_toy_functions(toy_info)
    position_keywords = ["position", "stroke", "linear"]
    
    return any(
        keyword in " ".join(functions).lower() 
        for keyword in position_keywords
    )


def format_device_name(toy_info: dict[str, Any]) -> str:
    """Format a user-friendly device name."""
    name = str(toy_info.get("name") or "Lovense Device")
    nickname = str(toy_info.get("nickName") or "")
    
    return nickname or name


def is_device_connected(toy_info: dict[str, Any]) -> bool:
    """Check if device is currently connected."""
    # Check status field
    status = toy_info.get("status")
    if status is not None:
        return str(status).lower() in {"1", "true", "connected"}
    
    # Check connected field
    connected = toy_info.get("connected")
    if connected is not None:
        return connected is True or str(connected).lower() in {"1", "true", "connected"}
    
    # Default to unknown/disconnected
    return False


def get_battery_level(toy_info: dict[str, Any]) -> int | None:
    """Get battery level from toy info."""
    battery = toy_info.get("battery")
    if battery is not None:
        try:
            return max(0, min(100, int(battery)))
        except (TypeError, ValueError):
            _LOGGER.debug("Ignoring invalid Lovense battery value")
    
    return None


def get_device_version(toy_info: Mapping[str, Any]) -> str | None:
    """Return the toy firmware version reported by Lovense Remote, if any."""
    for key in ("version", "fVersion"):
        if (value := toy_info.get(key)) not in (None, ""):
            return str(value)
    return None
