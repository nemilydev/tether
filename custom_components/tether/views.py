"""Lovense Remote callback endpoint, authenticated by the pairing credentials."""
from __future__ import annotations

import hmac
import json
import logging
import re
from typing import Any

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import (
    CALLBACK_PATH,
    CALLBACK_VIEW_REGISTERED,
    CONF_CONNECTION_TYPE,
    CONF_USER_ID,
    CONF_UTOKEN,
    CONNECTION_STANDARD,
    DOMAIN,
    PENDING_PAIRINGS,
)
from .coordinator import LovenseCoordinator, active_coordinators, connection_store
from .utils import connection_info_from_callback, normalize_toys

_LOGGER = logging.getLogger(__name__)
_MAX_CALLBACK_BYTES = 1024 * 1024
_LOVENSE_DOMAIN = re.compile(r"^(?:[a-z0-9-]+\.)+lovense\.club$", re.IGNORECASE)
_PORT_KEYS = ("httpsPort", "httpPort", "wsPort", "wssPort")


def _secure_equal(left: str, right: str) -> bool:
    # compare_digest rejects non-ASCII str, so compare encoded bytes.
    return hmac.compare_digest(left.encode(), right.encode())


def _valid_port(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return 1 <= int(value) <= 65535
    except (TypeError, ValueError):
        return False


class LovenseCallbackView(HomeAssistantView):
    """Receive connection and toy updates from Lovense Remote.

    Lovense cannot send Home Assistant credentials, so the view is public and
    instead requires the user ID plus the secret ``utoken`` generated for the
    matching config entry or in-progress pairing flow.
    """

    url = CALLBACK_PATH
    name = "api:tether:callback"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        """Handle a Lovense Remote callback."""
        if (request.content_length or 0) > _MAX_CALLBACK_BYTES:
            return web.Response(text="Payload too large", status=413)
        body = bytearray()
        try:
            # read(n) returns at most n bytes, so keep reading until EOF or the limit.
            while len(body) <= _MAX_CALLBACK_BYTES:
                chunk = await request.content.read(_MAX_CALLBACK_BYTES + 1 - len(body))
                if not chunk:
                    break
                body.extend(chunk)
        except aiohttp.ClientPayloadError:
            return web.Response(text="Invalid request body", status=400)
        if len(body) > _MAX_CALLBACK_BYTES:
            return web.Response(text="Payload too large", status=413)
        try:
            data: Any = json.loads(bytes(body))
        except (UnicodeDecodeError, ValueError):
            return web.Response(text="Invalid JSON", status=400)
        if not isinstance(data, dict):
            return web.Response(text="Expected a JSON object", status=400)

        user_id = data.get("uid")
        utoken = data.get("utoken")
        if not isinstance(user_id, str) or not isinstance(utoken, str):
            return web.Response(text="Missing pairing credentials", status=400)

        coordinator = self._find_coordinator(user_id, utoken)
        retry_entry = None if coordinator else self._find_retry_entry(user_id, utoken)
        pending = (
            None
            if coordinator or retry_entry
            else self._find_pending_pairing(user_id, utoken)
        )
        if coordinator is None and retry_entry is None and pending is None:
            _LOGGER.warning("Rejected Lovense callback with unknown credentials")
            return web.Response(text="Unauthorized callback", status=401)

        domain = data.get("domain")
        if domain is not None and (
            not isinstance(domain, str) or not _LOVENSE_DOMAIN.fullmatch(domain)
        ):
            return web.Response(text="Invalid Lovense domain", status=400)
        if any(
            data.get(key) is not None and not _valid_port(data[key]) for key in _PORT_KEYS
        ):
            return web.Response(text="Invalid port", status=400)

        update = dict(data)
        if "toys" in data:
            raw_toys = data["toys"]
            if not isinstance(raw_toys, (dict, str)):
                return web.Response(text="Invalid toy data", status=400)
            toys = normalize_toys(raw_toys)
            if raw_toys and not toys:
                return web.Response(text="Invalid toy data", status=400)
            update["toys"] = toys

        if retry_entry is not None:
            # Retrying entries have no active coordinator; save only the address
            # for the next setup attempt without starting polling or commands.
            connection_info = connection_info_from_callback(update)
            if connection_info.get("domain"):
                await connection_store(self.hass, retry_entry.entry_id).async_save(
                    connection_info
                )
        elif coordinator is None:
            pending["connection_info"] = connection_info_from_callback(update)
            pending["event"].set()
        else:
            coordinator.update_device_info(update)
        return web.Response(text="OK", status=200)

    def _find_pending_pairing(self, user_id: str, utoken: str) -> dict[str, Any] | None:
        """Match a callback to a config flow that is showing its pairing QR code."""
        pending = self.hass.data.get(DOMAIN, {}).get(PENDING_PAIRINGS, {}).get(user_id)
        if pending and _secure_equal(utoken, str(pending["utoken"])):
            return pending
        return None

    def _find_retry_entry(self, user_id: str, utoken: str) -> ConfigEntry | None:
        """Authenticate retrying Standard entries, excluding unloaded entries."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            expected_uid = str(entry.data.get(CONF_USER_ID, ""))
            expected_token = str(entry.data.get(CONF_UTOKEN, ""))
            if (
                entry.state is ConfigEntryState.SETUP_RETRY
                and entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_STANDARD
                and expected_uid
                and expected_token
                and _secure_equal(user_id, expected_uid)
                and _secure_equal(utoken, expected_token)
            ):
                return entry
        return None

    def _find_coordinator(self, user_id: str, utoken: str) -> LovenseCoordinator | None:
        for coordinator in active_coordinators(self.hass):
            expected_uid = str(coordinator.config.get(CONF_USER_ID, ""))
            expected_token = str(coordinator.config.get(CONF_UTOKEN, ""))
            if (
                expected_uid
                and expected_token
                and _secure_equal(user_id, expected_uid)
                and _secure_equal(utoken, expected_token)
            ):
                return coordinator
        return None


@callback
def async_setup_views(hass: HomeAssistant) -> None:
    """Register one callback view for all Tether config entries.

    Home Assistant cannot unregister HTTP views, so the view stays registered
    and simply rejects callbacks once no entry or pairing flow matches.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(CALLBACK_VIEW_REGISTERED):
        return
    hass.http.register_view(LovenseCallbackView(hass))
    domain_data[CALLBACK_VIEW_REGISTERED] = True
