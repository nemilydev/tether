"""Lovense API data coordinator and command transport."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_COMMAND_SERVER,
    CALLBACK_RECONCILE_COOLDOWN,
    CMD_FUNCTION,
    CMD_POSITION,
    CONF_CONNECTION_INFO,
    CONF_CONNECTION_TYPE,
    CONF_DEFAULT_TIME_SEC,
    CONF_DEVELOPER_TOKEN,
    CONF_LOCAL_HOST,
    CONF_LOCAL_PORT,
    CONF_POLL_INTERVAL,
    CONF_USER_ID,
    CONF_USER_NAME,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    CONNECTION_LOCAL,
    DOMAIN,
    REQUEST_TIMEOUT,
    SCAN_INTERVAL,
    STORAGE_VERSION,
    X_PLATFORM,
)
from .utils import (
    ACTUATOR_ACTIONS,
    build_function_action,
    connection_info_from_callback,
    merge_callback_toys,
    next_actuator_state,
    normalize_toys,
)

_LOGGER = logging.getLogger(__name__)
_UNIFIED_SETTINGS = frozenset({"position", *(key for key, _ in ACTUATOR_ACTIONS)})
_DASHED_IP_DOMAIN = re.compile(r"^(?P<ip>[0-9-]+)\.lovense\.club$", re.IGNORECASE)
_ERROR_MESSAGES = {
    "400": "Invalid command",
    "401": "Toy not found",
    "402": "Toy not connected",
    "403": "Toy does not support this command",
    "404": "Invalid parameter",
    "500": "Lovense HTTP server is stopped or disabled",
    "501": "Invalid developer token",
    "502": "Developer account has no permission to use this API",
    "503": "Invalid user ID",
    "506": "Lovense server error; restart Lovense Remote",
    "507": "Lovense app is offline",
}


def _is_success_code(code: Any) -> bool:
    return str(code) in {"0", "200"}


def connection_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the per-entry store holding the last Lovense Remote address."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.connection")


type TetherConfigEntry = ConfigEntry[LovenseCoordinator]

_ACTIVE_STATES = (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)


@callback
def active_coordinator(entry: ConfigEntry | None) -> LovenseCoordinator | None:
    """Return the coordinator of a Tether entry that is set up or loading."""
    if entry is None or entry.domain != DOMAIN or entry.state not in _ACTIVE_STATES:
        return None
    coordinator = getattr(entry, "runtime_data", None)
    return coordinator if isinstance(coordinator, LovenseCoordinator) else None


@callback
def active_coordinators(hass: HomeAssistant) -> list[LovenseCoordinator]:
    """Return the coordinators of every active Tether config entry."""
    return [
        coordinator
        for entry in hass.config_entries.async_entries(DOMAIN)
        if (coordinator := active_coordinator(entry)) is not None
    ]


class LovenseCommandError(UpdateFailed):
    """Lovense returned a command error in an otherwise successful HTTP response."""

    def __init__(self, code: int | str, message: str | None = None) -> None:
        self.code = code
        super().__init__(
            f"Lovense error {code}: {message or _ERROR_MESSAGES.get(str(code), 'command failed')}"
        )


class LovenseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll Lovense and send commands through local or cloud API transports."""

    config_entry: TetherConfigEntry

    def __init__(self, hass: HomeAssistant, entry: TetherConfigEntry) -> None:
        config = entry.data
        self.session = async_get_clientsession(hass)
        self.config = dict(config)
        self.options = dict(entry.options)
        self.connection_type = config.get(CONF_CONNECTION_TYPE, CONNECTION_LOCAL)
        self.developer_token = config.get(CONF_DEVELOPER_TOKEN, "")
        self.user_id = config.get(CONF_USER_ID, "")
        self.user_name = config.get(CONF_USER_NAME, "")
        self.verify_ssl = self.options.get(
            CONF_VERIFY_SSL, config.get(CONF_VERIFY_SSL, True)
        )
        self.device_info: dict[str, Any] = {}
        self.toy_data: dict[str, dict[str, Any]] = {}
        self.toy_settings: dict[str, dict[str, Any]] = {}
        # Bumped whenever toy state is learned outside GetToys; reads that began
        # before the latest bump are older than that state and are discarded.
        self._toy_revision = 0
        self._reconcile_requested = False
        self._reconcile_task: asyncio.Task[None] | None = None
        self._store = connection_store(hass, entry.entry_id)
        if self.connection_type == CONNECTION_LOCAL:
            self.device_info = {
                "host": config[CONF_LOCAL_HOST],
                "port": config[CONF_LOCAL_PORT],
                "use_https": config.get(CONF_USE_HTTPS, False),
            }
        elif isinstance(config.get(CONF_CONNECTION_INFO), dict):
            self.device_info = dict(config[CONF_CONNECTION_INFO])

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=self.options.get(CONF_POLL_INTERVAL, SCAN_INTERVAL)
            ),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch current toys, using the configured connection mode."""
        try:
            if not self.device_info:
                return {"status": "awaiting_pairing", "toys": self.toy_data}
            revision = self._toy_revision
            toys = await self._async_fetch_toys()
            if revision == self._toy_revision:
                self.toy_data = toys
            # Otherwise a callback landed mid-read and its reconcile read wins.
            return {"status": "connected", "toys": self.toy_data}
        except UpdateFailed:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Error communicating with Lovense: {err}") from err

    async def _async_fetch_toys(self) -> dict[str, dict[str, Any]]:
        """Read toys locally, falling back to the cloud API for Standard entries."""
        try:
            return await self._get_toys_local()
        except (aiohttp.ClientError, TimeoutError, UpdateFailed) as local_error:
            if self.connection_type == CONNECTION_LOCAL:
                raise
            _LOGGER.debug("Local Lovense API unavailable; trying cloud API")
            try:
                return await self._get_toys_server()
            except (aiohttp.ClientError, TimeoutError, UpdateFailed) as cloud_error:
                raise UpdateFailed(
                    f"Local and cloud APIs unavailable: {cloud_error}"
                ) from local_error

    async def async_load_connection_info(self) -> None:
        """Restore the last Lovense Remote address learned from a callback.

        The stored address is newer than the one captured during pairing, so it
        wins; without it, Standard entries would wait for a fresh callback after
        every Home Assistant restart.
        """
        if self.connection_type == CONNECTION_LOCAL:
            return
        stored = await self._store.async_load()
        if isinstance(stored, dict) and stored.get("domain"):
            self.device_info = dict(stored)

    async def async_shutdown(self) -> None:
        """Stop callback reconciliation along with scheduled polling."""
        self._reconcile_requested = False
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            self._reconcile_task = None
        await super().async_shutdown()

    def _local_candidates(self) -> list[tuple[str, bool | None]]:
        """Build ordered local endpoint candidates, including the LAN DNS fallback."""
        info = self.device_info
        if "host" in info:
            host = str(info["host"]).strip().strip("[]")
            host_for_url = f"[{host}]" if ":" in host else host
            port = int(info["port"])
            if info.get("use_https"):
                return [(f"https://{host_for_url}:{port}/command", self.verify_ssl)]
            return [(f"http://{host_for_url}:{port}/command", None)]

        candidates: list[tuple[str, bool | None]] = []
        domain = info.get("domain")
        https_port = info.get("httpsPort")
        http_port = info.get("httpPort")
        if domain and https_port:
            candidates.append((f"https://{domain}:{int(https_port)}/command", self.verify_ssl))
        if isinstance(domain, str):
            match = _DASHED_IP_DOMAIN.fullmatch(domain)
            if match:
                try:
                    ip_address = ipaddress.ip_address(match.group("ip").replace("-", "."))
                except ValueError:
                    ip_address = None
                if ip_address is not None:
                    if https_port:
                        candidates.append((f"https://{ip_address}:{int(https_port)}/command", False))
                    if http_port:
                        candidates.append((f"http://{ip_address}:{int(http_port)}/command", None))
        return candidates

    async def _post_command(
        self,
        url: str,
        payload: dict[str, Any],
        ssl: bool | None,
    ) -> dict[str, Any]:
        """POST one command and validate Lovense's application-level response."""
        async with self.session.post(
            url,
            json=payload,
            headers={"X-platform": X_PLATFORM},
            timeout=aiohttp.ClientTimeout(total=self.options.get("request_timeout", REQUEST_TIMEOUT)),
            ssl=ssl,
        ) as response:
            response.raise_for_status()
            result = await response.json()
        if not isinstance(result, dict):
            raise UpdateFailed("Lovense returned a non-object response")
        code = result.get("code")
        if not _is_success_code(code):
            raise LovenseCommandError(code, result.get("message"))
        return result

    async def _get_toys_local(self) -> dict[str, dict[str, Any]]:
        """Get toys from Lovense Remote over the local API."""
        candidates = self._local_candidates()
        if not candidates:
            raise UpdateFailed("No local Lovense endpoint is available")
        errors: list[Exception] = []
        for url, ssl in candidates:
            try:
                result = await self._post_command(url, {"command": "GetToys"}, ssl)
                return normalize_toys(result.get("data", {}))
            except LovenseCommandError:
                raise
            except (aiohttp.ClientError, TimeoutError, UpdateFailed, ValueError) as err:
                errors.append(err)
        raise UpdateFailed(
            "All local Lovense endpoints failed: "
            + "; ".join(str(error) for error in errors)
        )

    async def _get_toys_server(self) -> dict[str, dict[str, Any]]:
        """Get toys using the Standard API cloud command endpoint."""
        if not self.developer_token or not self.user_id:
            raise UpdateFailed("Cloud API credentials are not configured")
        payload = {
            "token": self.developer_token,
            "uid": self.user_id,
            "command": "GetToys",
        }
        async with self.session.post(
            API_COMMAND_SERVER,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=self.options.get("request_timeout", REQUEST_TIMEOUT)),
        ) as response:
            response.raise_for_status()
            result = await response.json()
        if not isinstance(result, dict) or not _is_success_code(result.get("code")):
            code = result.get("code", "unknown") if isinstance(result, dict) else "invalid"
            raise LovenseCommandError(code, result.get("message") if isinstance(result, dict) else None)
        return normalize_toys(result.get("data", {}))

    async def send_command_local(self, command: str, **kwargs: Any) -> dict[str, Any]:
        """Send a command over local LAN, falling back to the cloud API if configured."""
        payload = {"command": command, "apiVer": 1, **kwargs}
        payload.setdefault("timeSec", self.options.get(CONF_DEFAULT_TIME_SEC, 0))
        toy_id = str(kwargs["toy"]) if kwargs.get("toy") else None
        try:
            result = await self._async_dispatch_command(payload, toy_id)
        except (Exception, asyncio.CancelledError) as err:
            # The command may or may not have run, so stop assuming any levels.
            self._forget_actuator_state(toy_id)
            if isinstance(err, (UpdateFailed, aiohttp.ClientError, TimeoutError)):
                raise HomeAssistantError(f"Lovense command failed: {err}") from err
            raise
        self._track_actuator_state(toy_id, command, payload)
        return result

    async def _async_dispatch_command(
        self, payload: dict[str, Any], toy_id: str | None
    ) -> dict[str, Any]:
        """Deliver one command payload through the first transport that answers."""
        cloud_available = bool(
            self.connection_type != CONNECTION_LOCAL
            and self.developer_token
            and self.user_id
        )
        if not self.device_info and not cloud_available:
            raise UpdateFailed("No Lovense device connection is available")
        errors: list[Exception] = []
        for url, ssl in self._local_candidates():
            try:
                return await self._post_command(url, payload, ssl)
            except LovenseCommandError as err:
                if str(err.code) == "402" and toy_id:
                    self._mark_toy_disconnected(toy_id)
                raise
            except (aiohttp.ClientError, TimeoutError, UpdateFailed, ValueError) as err:
                errors.append(err)
        if cloud_available:
            cloud_payload = {
                "token": self.developer_token,
                "uid": self.user_id,
                **payload,
            }
            async with self.session.post(
                API_COMMAND_SERVER,
                json=cloud_payload,
                timeout=aiohttp.ClientTimeout(total=self.options.get("request_timeout", REQUEST_TIMEOUT)),
            ) as response:
                response.raise_for_status()
                result = await response.json()
            if not isinstance(result, dict) or not _is_success_code(result.get("code")):
                code = result.get("code", "unknown") if isinstance(result, dict) else "invalid"
                if str(code) == "402" and toy_id:
                    self._mark_toy_disconnected(toy_id)
                raise LovenseCommandError(code, result.get("message") if isinstance(result, dict) else None)
            return result
        raise UpdateFailed(
            "No Lovense command endpoint responded: "
            + "; ".join(str(error) for error in errors)
        )

    @callback
    def update_device_info(self, device_info: dict[str, Any]) -> None:
        """Apply a Lovense callback, then confirm its toy state with GetToys.

        Callbacks can be delivered late, so their toy state is provisional: it is
        shown immediately and then replaced by a GetToys read that starts after
        the most recent callback.
        """
        info = dict(device_info)
        toys = info.pop("toys", None)
        info.pop("utoken", None)
        self.device_info = info
        if stored := connection_info_from_callback(info):
            self._store.async_delay_save(lambda: stored, 1)
        if toys is not None:
            self._publish_toys(merge_callback_toys(self.toy_data, normalize_toys(toys)))
        else:
            self.async_set_updated_data({"status": "connected", "toys": self.toy_data})
        self._schedule_toy_reconcile()

    @callback
    def _publish_toys(self, toys: dict[str, dict[str, Any]]) -> None:
        """Publish toy state learned outside GetToys, superseding in-flight reads."""
        self._toy_revision += 1
        self.toy_data = toys
        self.async_set_updated_data({"status": "connected", "toys": toys})

    @callback
    def _schedule_toy_reconcile(self) -> None:
        self._reconcile_requested = True
        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = self.hass.async_create_background_task(
                self._async_reconcile_toys(), f"{DOMAIN} callback toy reconcile"
            )

    async def _async_reconcile_toys(self) -> None:
        """Re-read toys until a read has started after the latest callback."""
        while self._reconcile_requested:
            self._reconcile_requested = False
            revision = self._toy_revision
            try:
                toys = await self._async_fetch_toys()
            except (aiohttp.ClientError, TimeoutError, UpdateFailed, ValueError) as err:
                _LOGGER.debug("Could not confirm Lovense callback toy state: %s", err)
            else:
                if revision == self._toy_revision:
                    self.toy_data = toys
                    self.async_set_updated_data({"status": "connected", "toys": toys})
            if self._reconcile_requested:
                await asyncio.sleep(CALLBACK_RECONCILE_COOLDOWN)

    def _mark_toy_disconnected(self, toy_id: str) -> None:
        """Publish the disconnected state when Lovense returns error 402."""
        if toy_id not in self.toy_data:
            return
        toys = dict(self.toy_data)
        toys[toy_id] = {**toys[toy_id], "status": 0, "connected": False}
        self._publish_toys(toys)

    def _track_actuator_state(
        self, toy_id: str | None, command: str, payload: dict[str, Any]
    ) -> None:
        """Record which actuator levels keep running after a successful command."""
        for target in [toy_id] if toy_id else list(self.toy_settings):
            state = next_actuator_state(
                self.toy_settings.get(target, {}), command, payload
            )
            if state:
                self.toy_settings[target] = state
            else:
                self.toy_settings.pop(target, None)

    def _forget_actuator_state(self, toy_id: str | None) -> None:
        if toy_id:
            self.toy_settings.pop(toy_id, None)
        else:
            self.toy_settings.clear()

    async def send_unified_command(self, toy_id: str, **settings: Any) -> None:
        """Send combined actuator state without stopping unrelated actuators.

        ``None`` leaves an actuator unchanged, except ``stroke_range=None`` which
        clears the range. ``position`` sends a one-shot Position command.
        """
        unknown = set(settings) - _UNIFIED_SETTINGS
        if unknown:
            raise ValueError(
                f"Unsupported Lovense actuator settings: {', '.join(sorted(unknown))}"
            )
        position = settings.pop("position", None)
        changes = {
            key: value
            for key, value in settings.items()
            if value is not None or key == "stroke_range"
        }
        if position is not None:
            if changes:
                raise ValueError("Position cannot be combined with other actuator settings")
            await self.send_command_local(
                CMD_POSITION,
                value=str(int(position)),
                toy=toy_id,
            )
            return
        if not changes:
            raise ValueError("At least one Lovense actuator setting is required")

        action = build_function_action(
            {**self.toy_settings.get(toy_id, {}), **changes}, changes
        )
        if action:
            await self.send_command_local(
                CMD_FUNCTION,
                action=action,
                timeSec=0,
                toy=toy_id,
                stopPrevious=0,
            )
        # Commit only after Lovense accepted the command; also records clears
        # such as stroke_range=None that the action string cannot express.
        self.toy_settings.setdefault(toy_id, {}).update(changes)