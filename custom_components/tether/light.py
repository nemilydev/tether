"""Vibration, pump and stroke intensity exposed as dimmable lights."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CMD_PRESET,
    PRESETS,
    PUMP_MAX,
    STROKE_CONTROL_BOTH,
    STROKE_CONTROL_LIGHTS,
    VIBRATE_MAX,
)
from .coordinator import LovenseCoordinator, TetherConfigEntry
from .entity import EntityFactories, TetherEntity, async_setup_toy_entities
from .utils import parse_toy_functions, stroke_control_type

STROKE_MAX = 20

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TetherConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create light controls as toys and their capabilities appear."""
    coordinator = entry.runtime_data
    stroke_lights = stroke_control_type(entry) in {STROKE_CONTROL_LIGHTS, STROKE_CONTROL_BOTH}

    def build(toy_id: str, toy_info: dict[str, Any]) -> EntityFactories:
        # Toys that do not report capabilities are treated as plain vibrators.
        functions = parse_toy_functions(toy_info) or ["Vibrate"]
        factories: dict[str, Any] = {}
        if "Vibrate" in functions:
            factories["vibration"] = lambda: TetherVibrationLight(coordinator, toy_id, toy_info)
        if "Pump" in functions:
            factories["pump"] = lambda: TetherLevelLight(
                coordinator, toy_id, toy_info, "pump", PUMP_MAX
            )
        if "Stroke" in functions and stroke_lights:
            factories["stroke"] = lambda: TetherLevelLight(
                coordinator, toy_id, toy_info, "stroke", STROKE_MAX
            )
        return factories

    async_setup_toy_entities(entry, async_add_entities, build)


class TetherLight(TetherEntity, LightEntity):
    """A toy capability whose level is mapped onto light brightness."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_is_on = False
    _attr_brightness = 0

    def _set_state(self, brightness: int, effect: str | None = None) -> None:
        self._attr_is_on = brightness > 0
        self._attr_brightness = brightness
        self._attr_effect = effect
        self.async_write_ha_state()


class TetherVibrationLight(TetherLight):
    """Vibration intensity (0-20) with Lovense presets as light effects."""

    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = PRESETS

    def __init__(
        self, coordinator: LovenseCoordinator, toy_id: str, toy_info: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, toy_id, toy_info, "vibration")

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness or 64)
        effect = kwargs.get(ATTR_EFFECT)
        if effect in PRESETS:
            await self.coordinator.send_command_local(
                CMD_PRESET, name=effect, timeSec=0, toy=self.toy_id
            )
        else:
            effect = None
            intensity = max(1, round(brightness * VIBRATE_MAX / 255))
            await self.coordinator.send_unified_command(self.toy_id, vibration=intensity)
        self._set_state(max(1, brightness), effect)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.send_unified_command(self.toy_id, vibration=0)
        self._set_state(0)


class TetherLevelLight(TetherLight):
    """Pump (0-3) or stroke (0-20) level mapped onto brightness."""

    _COMMAND_FIELDS = {"pump": "pump", "stroke": "thrusting"}

    def __init__(
        self,
        coordinator: LovenseCoordinator,
        toy_id: str,
        toy_info: dict[str, Any],
        key: str,
        maximum: int,
    ) -> None:
        super().__init__(coordinator, toy_id, toy_info, key)
        self._field = self._COMMAND_FIELDS[key]
        self._maximum = maximum

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness or 128)
        level = max(1, round(brightness * self._maximum / 255))
        await self.coordinator.send_unified_command(self.toy_id, **{self._field: level})
        self._set_state(max(1, brightness))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.send_unified_command(self.toy_id, **{self._field: 0})
        self._set_state(0)
