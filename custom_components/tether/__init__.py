"""Lovense control for Home Assistant over the local LAN or the Standard API."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import LovenseCoordinator, TetherConfigEntry, connection_store
from .services import async_setup_services
from .views import async_setup_views

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services and the callback view once per Home Assistant instance."""
    async_setup_services(hass)
    async_setup_views(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TetherConfigEntry) -> bool:
    """Set up a Tether connection from a config entry."""
    coordinator = LovenseCoordinator(hass, entry)
    await coordinator.async_load_connection_info()
    entry.runtime_data = coordinator

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Lovense",
        model="Lovense Remote",
        name=entry.title,
    )

    # Raises ConfigEntryNotReady when Lovense Remote cannot be reached.
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TetherConfigEntry) -> bool:
    """Unload a config entry; the coordinator shuts itself down with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: TetherConfigEntry) -> None:
    """Reload the entry after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: TetherConfigEntry) -> None:
    """Delete the stored Lovense Remote address with its config entry."""
    await connection_store(hass, entry.entry_id).async_remove()
