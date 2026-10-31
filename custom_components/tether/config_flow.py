"""Config, reconfigure and options flows for Tether."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import secrets
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    API_GET_QRCODE,
    CONF_CALLBACK_URL,
    CONF_CONNECTION_INFO,
    CONF_CONNECTION_TYPE,
    CONF_DEFAULT_TIME_SEC,
    CONF_DEVELOPER_TOKEN,
    CONF_LOCAL_HOST,
    CONF_LOCAL_PORT,
    CONF_POLL_INTERVAL,
    CONF_SKIP_PAIRING_CHECK,
    CONF_STROKE_CONTROL_TYPE,
    CONF_USE_HTTPS,
    CONF_USER_ID,
    CONF_USER_NAME,
    CONF_UTOKEN,
    CONF_VERIFY_SSL,
    CONNECTION_LOCAL,
    CONNECTION_STANDARD,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
    PAIRING_WAIT_TIMEOUT,
    PENDING_PAIRINGS,
    REQUEST_TIMEOUT,
    SCAN_INTERVAL,
    STROKE_CONTROL_BOTH,
    STROKE_CONTROL_LIGHTS,
    STROKE_CONTROL_NUMBERS,
    X_PLATFORM,
)
from .coordinator import connection_store
from .utils import (
    extract_pairing_code,
    extract_qr_image_url,
    qr_markdown,
    stroke_control_type,
)
from .views import async_setup_views

_LOGGER = logging.getLogger(__name__)
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")

_CONNECTION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[CONNECTION_LOCAL, CONNECTION_STANDARD],
        mode=SelectSelectorMode.LIST,
        translation_key=CONF_CONNECTION_TYPE,
    )
)
_STROKE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[STROKE_CONTROL_LIGHTS, STROKE_CONTROL_NUMBERS, STROKE_CONTROL_BOTH],
        mode=SelectSelectorMode.DROPDOWN,
        translation_key=CONF_STROKE_CONTROL_TYPE,
    )
)
_PORT = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
_PAIR_SCHEMA = vol.Schema({vol.Optional(CONF_SKIP_PAIRING_CHECK, default=False): bool})


class TetherConnectionError(HomeAssistantError):
    """Unable to establish or validate a Lovense connection."""


def _validate_host(host: str) -> bool:
    """Validate a host without accepting a URL, path, or arbitrary request target."""
    host = host.strip().strip("[]")
    if not host or any(char in host for char in "/@?#"):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        if ":" in host:
            return False
        return bool(_HOST_PATTERN.fullmatch(host))


def _validate_callback_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(hostname)
        and not parsed.username
        and not parsed.password
        and parsed.path.startswith("/")
    )


def _local_unique_id(data: dict[str, Any]) -> str:
    return f"local:{data[CONF_LOCAL_HOST]}:{data[CONF_LOCAL_PORT]}"


def _local_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_LOCAL_HOST, default=defaults.get(CONF_LOCAL_HOST, "")): str,
            vol.Required(
                CONF_LOCAL_PORT, default=defaults.get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT)
            ): _PORT,
            vol.Required(CONF_USE_HTTPS, default=defaults.get(CONF_USE_HTTPS, False)): bool,
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, False)): bool,
        }
    )


def _standard_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_DEVELOPER_TOKEN, default=defaults.get(CONF_DEVELOPER_TOKEN, "")
            ): str,
            vol.Required(CONF_USER_ID, default=defaults.get(CONF_USER_ID, "")): str,
            vol.Required(
                CONF_USER_NAME, default=defaults.get(CONF_USER_NAME, "Home Assistant")
            ): str,
            vol.Required(CONF_CALLBACK_URL, default=defaults.get(CONF_CALLBACK_URL, "")): str,
        }
    )


class TetherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure either a direct LAN connection or the Standard API."""

    VERSION = 1

    def __init__(self) -> None:
        self._entry_data: dict[str, Any] = {}
        self._pairing_qr: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TetherOptionsFlow:
        return TetherOptionsFlow()

    # Initial setup

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_LOCAL:
                return await self.async_step_local()
            return await self.async_step_standard()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_LOCAL): _CONNECTION_SELECTOR}
            ),
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await self._async_validate_local_input(user_input)
            if data is not None:
                await self.async_set_unique_id(_local_unique_id(data))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Tether ({data[CONF_LOCAL_HOST]})", data=data
                )
        return self.async_show_form(
            step_id="local", data_schema=_local_schema(user_input or {}), errors=errors
        )

    async def async_step_standard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USER_ID].strip())
            self._abort_if_unique_id_configured()
            data, errors = await self._async_prepare_standard(user_input)
            if data is not None:
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="standard", data_schema=_standard_schema(user_input or {}), errors=errors
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            skip = bool(user_input.get(CONF_SKIP_PAIRING_CHECK))
            connection_info = await self._async_wait_for_pairing(wait=not skip)
            if connection_info is not None or skip:
                data = dict(self._entry_data)
                if connection_info:
                    data[CONF_CONNECTION_INFO] = connection_info
                return self.async_create_entry(
                    title=f"Tether ({data[CONF_USER_ID]})", data=data
                )
            errors["base"] = "pairing_not_received"
        return self.async_show_form(
            step_id="pair",
            data_schema=_PAIR_SCHEMA,
            errors=errors,
            description_placeholders=self._pair_placeholders(),
        )

    # Reconfiguration

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_LOCAL:
                return await self.async_step_reconfigure_local()
            return await self.async_step_reconfigure_standard()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_TYPE,
                        default=entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_LOCAL),
                    ): _CONNECTION_SELECTOR
                }
            ),
        )

    async def async_step_reconfigure_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await self._async_validate_local_input(user_input)
            if data is not None:
                unique_id = _local_unique_id(data)
                if self._unique_id_taken(unique_id):
                    errors["base"] = "already_configured"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=unique_id,
                        title=f"Tether ({data[CONF_LOCAL_HOST]})",
                        data=data,
                    )
        return self.async_show_form(
            step_id="reconfigure_local",
            data_schema=_local_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    async def async_step_reconfigure_standard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._unique_id_taken(user_input[CONF_USER_ID].strip()):
                errors["base"] = "already_configured"
            else:
                data, errors = await self._async_prepare_standard(user_input)
                if data is not None:
                    return await self.async_step_reconfigure_pair()
        return self.async_show_form(
            step_id="reconfigure_standard",
            data_schema=_standard_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    async def async_step_reconfigure_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            skip = bool(user_input.get(CONF_SKIP_PAIRING_CHECK))
            connection_info = await self._async_wait_for_pairing(wait=not skip)
            if connection_info is not None or skip:
                if connection_info:
                    await connection_store(self.hass, entry.entry_id).async_save(
                        connection_info
                    )
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=self._entry_data[CONF_USER_ID],
                    title=f"Tether ({self._entry_data[CONF_USER_ID]})",
                    data=self._entry_data,
                )
            errors["base"] = "pairing_not_received"
        return self.async_show_form(
            step_id="reconfigure_pair",
            data_schema=_PAIR_SCHEMA,
            errors=errors,
            description_placeholders=self._pair_placeholders(),
        )

    # Shared helpers

    def _unique_id_taken(self, unique_id: str) -> bool:
        """Return whether another entry already uses this connection identity."""
        current = self._get_reconfigure_entry().entry_id
        return any(
            entry.unique_id == unique_id and entry.entry_id != current
            for entry in self._async_current_entries(include_ignore=False)
        )

    async def _async_validate_local_input(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Normalize and test a local connection; return its data or form errors."""
        host = user_input[CONF_LOCAL_HOST].strip().strip("[]")
        if not _validate_host(host):
            return None, {"base": "invalid_host"}
        data = {CONF_CONNECTION_TYPE: CONNECTION_LOCAL, **user_input, CONF_LOCAL_HOST: host}
        try:
            await self._async_test_local(data)
        except (aiohttp.ClientError, TimeoutError, TetherConnectionError) as err:
            _LOGGER.debug("Local Lovense Remote validation failed: %s", err)
            return None, {"base": "cannot_connect"}
        return data, {}

    async def _async_prepare_standard(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Request a pairing QR code and start listening for its callback."""
        callback_url = user_input[CONF_CALLBACK_URL].strip()
        if not _validate_callback_url(callback_url):
            return None, {"base": "invalid_callback_url"}
        data = {
            CONF_CONNECTION_TYPE: CONNECTION_STANDARD,
            **user_input,
            CONF_USER_ID: user_input[CONF_USER_ID].strip(),
            CONF_CALLBACK_URL: callback_url,
            CONF_UTOKEN: secrets.token_urlsafe(32),
        }
        try:
            self._pairing_qr = await self._async_request_qr(data)
        except (aiohttp.ClientError, TimeoutError, TetherConnectionError) as err:
            _LOGGER.warning("Unable to request a Lovense pairing QR code: %s", err)
            return None, {"base": "cannot_connect"}
        self.async_remove()
        self._entry_data = data
        self._register_pending_pairing()
        return data, {}

    def _pair_placeholders(self) -> dict[str, str]:
        """Show the Lovense QR image inline, plus the code PC Remote asks for."""
        return {
            "qr_code": qr_markdown(extract_qr_image_url(self._pairing_qr)),
            "pairing_code": extract_pairing_code(self._pairing_qr) or "-",
            "callback_url": self._entry_data.get(CONF_CALLBACK_URL, ""),
        }

    @callback
    def _register_pending_pairing(self) -> None:
        """Accept the callback Lovense Remote sends when this flow's QR is scanned.

        No config entry exists yet, so the callback view matches it against this
        flow's user ID and freshly generated utoken instead.
        """
        async_setup_views(self.hass)
        self.hass.data.setdefault(DOMAIN, {}).setdefault(PENDING_PAIRINGS, {})[
            self._entry_data[CONF_USER_ID]
        ] = {
            "utoken": self._entry_data[CONF_UTOKEN],
            "connection_info": None,
            "event": asyncio.Event(),
        }

    def _pending_pairing(self) -> dict[str, Any] | None:
        pending = (
            self.hass.data.get(DOMAIN, {})
            .get(PENDING_PAIRINGS, {})
            .get(self._entry_data.get(CONF_USER_ID))
        )
        if pending and pending["utoken"] == self._entry_data.get(CONF_UTOKEN):
            return pending
        return None

    async def _async_wait_for_pairing(self, wait: bool = True) -> dict[str, Any] | None:
        """Return the pairing callback data, waiting briefly if it is still in flight."""
        pending = self._pending_pairing()
        if pending is None:
            return None
        if not pending["event"].is_set():
            if not wait:
                return None
            try:
                await asyncio.wait_for(pending["event"].wait(), PAIRING_WAIT_TIMEOUT)
            except TimeoutError:
                return None
        return pending["connection_info"]

    @callback
    def async_remove(self) -> None:
        """Stop accepting pairing callbacks once this flow finishes or is aborted."""
        if self._pending_pairing() is not None:
            self.hass.data[DOMAIN][PENDING_PAIRINGS].pop(self._entry_data[CONF_USER_ID])

    async def _async_request_qr(self, data: dict[str, Any]) -> dict[str, Any]:
        """Request the Standard API QR code using the official form payload."""
        payload = {
            "token": data[CONF_DEVELOPER_TOKEN],
            "uid": data[CONF_USER_ID],
            "uname": data[CONF_USER_NAME],
            "utoken": data[CONF_UTOKEN],
            "v": 2,
        }
        async with async_get_clientsession(self.hass).post(
            API_GET_QRCODE,
            data=payload,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            response.raise_for_status()
            try:
                result = await response.json(content_type=None)
            except ValueError as err:
                raise TetherConnectionError("Lovense returned invalid JSON") from err
        if not isinstance(result, dict) or str(result.get("code")) not in {"0", "200"}:
            message = (
                result.get("message", "Invalid Lovense response")
                if isinstance(result, dict)
                else "Invalid Lovense response"
            )
            raise TetherConnectionError(message)
        qr_data = result.get("data")
        if not isinstance(qr_data, dict) or not extract_qr_image_url(qr_data):
            raise TetherConnectionError("Lovense returned no displayable QR code")
        return qr_data

    async def _async_test_local(self, data: dict[str, Any]) -> None:
        """Send GetToys to Lovense Remote to prove the address is reachable."""
        scheme = "https" if data[CONF_USE_HTTPS] else "http"
        host = data[CONF_LOCAL_HOST]
        host_for_url = f"[{host}]" if ":" in host else host
        url = f"{scheme}://{host_for_url}:{data[CONF_LOCAL_PORT]}/command"
        async with async_get_clientsession(self.hass).post(
            url,
            json={"command": "GetToys"},
            headers={"X-platform": X_PLATFORM},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ssl=data[CONF_VERIFY_SSL] if data[CONF_USE_HTTPS] else None,
        ) as response:
            response.raise_for_status()
            try:
                result = await response.json(content_type=None)
            except ValueError as err:
                raise TetherConnectionError("Lovense returned invalid JSON") from err
        if not isinstance(result, dict) or str(result.get("code")) not in {"0", "200"}:
            raise TetherConnectionError("Lovense rejected the local API request")


class TetherOptionsFlow(OptionsFlow):
    """Configure polling, command duration, TLS and stroke entity style."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        entry = self.config_entry
        default_verify = entry.data.get(
            CONF_VERIFY_SSL, entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_LOCAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=entry.options.get(CONF_POLL_INTERVAL, SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
                    vol.Required(
                        CONF_DEFAULT_TIME_SEC,
                        default=entry.options.get(CONF_DEFAULT_TIME_SEC, 0),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                    vol.Required(
                        CONF_VERIFY_SSL,
                        default=entry.options.get(CONF_VERIFY_SSL, default_verify),
                    ): bool,
                    vol.Required(
                        CONF_STROKE_CONTROL_TYPE, default=stroke_control_type(entry)
                    ): _STROKE_SELECTOR,
                }
            ),
        )
