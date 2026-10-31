"""Built-in Lovense preset selection entities."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_PRESET, PRESETS
from .coordinator import LovenseCoordinator, TetherConfigEntry
from .entity import EntityFactories, TetherEntity, async_setup_toy_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TetherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a preset selector for every discovered toy."""
    coordinator = entry.runtime_data

    def build(toy_id: str, toy_info: dict[str, Any]) -> EntityFactories:
        return {"preset": lambda: TetherPresetSelect(coordinator, toy_id, toy_info)}

    async_setup_toy_entities(entry, async_add_entities, build)


class TetherPresetSelect(TetherEntity, SelectEntity):
    """Select and run one of the built-in Lovense patterns."""

    _attr_options = PRESETS
    _attr_current_option = None

    def __init__(
        self, coordinator: LovenseCoordinator, toy_id: str, toy_info: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, toy_id, toy_info, "preset")

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.send_command_local(
            CMD_PRESET, name=option, timeSec=0, toy=self.toy_id
        )
        self._attr_current_option = option
        self.async_write_ha_state()
