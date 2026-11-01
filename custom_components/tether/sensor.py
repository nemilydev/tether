"""Battery level sensors for Lovense toys."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import LovenseCoordinator, TetherConfigEntry
from .entity import EntityFactories, TetherEntity, async_setup_toy_entities
from .utils import get_battery_level

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TetherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a battery sensor once a toy reports its battery level."""
    coordinator = entry.runtime_data

    def build(toy_id: str, toy_info: dict[str, Any]) -> EntityFactories:
        if get_battery_level(toy_info) is None:
            return {}
        return {"battery": lambda: TetherBatterySensor(coordinator, toy_id, toy_info)}

    async_setup_toy_entities(entry, async_add_entities, build)


class TetherBatterySensor(TetherEntity, SensorEntity):
    """Battery percentage reported by the toy."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: LovenseCoordinator, toy_id: str, toy_info: dict[str, Any]
    ) -> None:
        # No translation key: the battery device class supplies the name.
        super().__init__(coordinator, toy_id, toy_info, "battery", translation_key=None)

    @property
    def native_value(self) -> int | None:
        return get_battery_level(self.toy)
