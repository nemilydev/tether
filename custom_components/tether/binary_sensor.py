"""Connectivity binary sensors for Lovense toys."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import LovenseCoordinator, TetherConfigEntry
from .entity import EntityFactories, TetherEntity, async_setup_toy_entities
from .utils import is_device_connected

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TetherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a connectivity sensor for every discovered toy."""
    coordinator = entry.runtime_data

    def build(toy_id: str, toy_info: dict[str, Any]) -> EntityFactories:
        return {
            "connectivity": lambda: TetherConnectivitySensor(coordinator, toy_id, toy_info)
        }

    async_setup_toy_entities(entry, async_add_entities, build)


class TetherConnectivitySensor(TetherEntity, BinarySensorEntity):
    """Whether the toy is currently connected to Lovense Remote."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: LovenseCoordinator, toy_id: str, toy_info: dict[str, Any]
    ) -> None:
        # No translation key: the connectivity device class supplies the name.
        super().__init__(coordinator, toy_id, toy_info, "connectivity", translation_key=None)

    @property
    def available(self) -> bool:
        """Stay available while the toy is listed so it can report disconnected."""
        return self.coordinator.last_update_success and bool(self.toy)

    @property
    def is_on(self) -> bool:
        return is_device_connected(self.toy)
