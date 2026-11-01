"""Precise number controls for each advertised toy function."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CMD_FUNCTION,
    CMD_POSITION,
    FUNCTION_LIMITS,
    STROKE_CONTROL_BOTH,
    STROKE_CONTROL_NUMBERS,
)
from .coordinator import LovenseCoordinator, TetherConfigEntry
from .entity import EntityFactories, TetherEntity, async_setup_toy_entities
from .utils import parse_toy_functions, stroke_control_type, supports_position_control

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TetherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create number entities for each advertised toy capability."""
    coordinator = entry.runtime_data
    stroke_numbers = stroke_control_type(entry) in {
        STROKE_CONTROL_NUMBERS,
        STROKE_CONTROL_BOTH,
    }

    def build(toy_id: str, toy_info: dict[str, Any]) -> EntityFactories:
        factories: dict[str, Any] = {}
        for function in sorted(set(parse_toy_functions(toy_info)) & FUNCTION_LIMITS.keys()):
            if function == "Stroke" and not stroke_numbers:
                continue
            factories[function.lower()] = (
                lambda function=function: TetherFunctionNumber(
                    coordinator, toy_id, toy_info, function
                )
            )
        if supports_position_control(toy_info):
            factories["position"] = lambda: TetherPositionNumber(coordinator, toy_id, toy_info)
        return factories

    async_setup_toy_entities(entry, async_add_entities, build)


class TetherNumber(TetherEntity, NumberEntity):
    """A slider that sends its value straight to the toy."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_step = 1
    _attr_native_value = 0


class TetherFunctionNumber(TetherNumber):
    """Level control for one Lovense function such as Vibrate1 or Rotate."""

    def __init__(
        self,
        coordinator: LovenseCoordinator,
        toy_id: str,
        toy_info: dict[str, Any],
        function: str,
    ) -> None:
        super().__init__(coordinator, toy_id, toy_info, function.lower())
        self.function = function
        self._attr_native_max_value = FUNCTION_LIMITS[function]

    async def async_set_native_value(self, value: float) -> None:
        level = int(value)
        if self.function == "Stroke":
            # Lovense requires a thrust speed alongside the stroke range.
            thrusting = max(
                1, int(self.coordinator.toy_settings.get(self.toy_id, {}).get("thrusting", 1))
            )
            action = f"Stroke:{level},Thrusting:{thrusting}"
        else:
            action = f"{self.function}:{level}"
        await self.coordinator.send_command_local(
            CMD_FUNCTION, action=action, timeSec=0, stopPrevious=0, toy=self.toy_id
        )
        self._attr_native_value = level
        self.async_write_ha_state()


class TetherPositionNumber(TetherNumber):
    """Absolute stroke position (0-100) for toys that support Position."""

    _attr_native_max_value = 100

    def __init__(
        self, coordinator: LovenseCoordinator, toy_id: str, toy_info: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, toy_id, toy_info, "position")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.send_command_local(
            CMD_POSITION, value=str(int(value)), toy=self.toy_id
        )
        self._attr_native_value = int(value)
        self.async_write_ha_state()
