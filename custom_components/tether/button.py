"""Immediate stop button for each Lovense toy."""
from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_FUNCTION
from .coordinator import LovenseCoordinator, TetherConfigEntry
from .entity import EntityFactories, TetherEntity, async_setup_toy_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TetherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a stop button for every discovered toy."""
    coordinator = entry.runtime_data

    def build(toy_id: str, toy_info: dict[str, Any]) -> EntityFactories:
        return {"stop": lambda: TetherStopButton(coordinator, toy_id, toy_info)}

    async_setup_toy_entities(entry, async_add_entities, build)


class TetherStopButton(TetherEntity, ButtonEntity):
    """Stop every active function on the toy."""

    def __init__(
        self, coordinator: LovenseCoordinator, toy_id: str, toy_info: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, toy_id, toy_info, "stop")

    async def async_press(self) -> None:
        await self.coordinator.send_command_local(
            CMD_FUNCTION, action="Stop", timeSec=0, toy=self.toy_id, apiVer=1
        )
