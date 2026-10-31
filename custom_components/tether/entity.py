"""Shared base entity for Tether toy entities."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED, UndefinedType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LovenseCoordinator, TetherConfigEntry
from .utils import (
    format_device_name,
    get_device_version,
    is_device_connected,
    normalize_toys,
)

type EntityFactories = Mapping[str, Callable[[], Entity]]


def toy_device_identifier(entry_id: str, toy_id: str) -> str:
    """Return the device registry identifier of a toy within one config entry."""
    return f"{entry_id}_{toy_id}"


@callback
def async_setup_toy_entities(
    entry: TetherConfigEntry,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[str, dict[str, Any]], EntityFactories],
) -> None:
    """Add entities for each toy now and whenever Lovense reports new toys.

    ``build`` returns entity factories keyed by a per-toy key; each key is only
    created once, so later coordinator updates add just the new capabilities.
    """
    coordinator = entry.runtime_data
    tracked: set[tuple[str, str]] = set()

    @callback
    def add_entities() -> None:
        toys = normalize_toys((coordinator.data or {}).get("toys", {}))
        new_entities: list[Entity] = []
        for toy_id, toy_info in toys.items():
            for key, factory in build(toy_id, toy_info).items():
                if (toy_id, key) not in tracked:
                    tracked.add((toy_id, key))
                    new_entities.append(factory())
        if new_entities:
            async_add_entities(new_entities)

    add_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_entities))


class TetherEntity(CoordinatorEntity[LovenseCoordinator]):
    """An entity belonging to one Lovense toy of one Tether config entry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LovenseCoordinator,
        toy_id: str,
        toy_info: dict[str, Any],
        key: str,
        translation_key: str | None | UndefinedType = UNDEFINED,
    ) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self.toy_id = toy_id
        self._attr_unique_id = f"{entry_id}_{toy_id}_{key}"
        if translation_key is UNDEFINED:
            translation_key = key
        if translation_key is not None:
            self._attr_translation_key = translation_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, toy_device_identifier(entry_id, toy_id))},
            via_device=(DOMAIN, entry_id),
            name=format_device_name(toy_info),
            manufacturer="Lovense",
            model=str(toy_info.get("name") or "Unknown"),
            sw_version=get_device_version(toy_info),
        )

    @property
    def toy(self) -> dict[str, Any]:
        """Return the latest reported state of this toy."""
        toys = normalize_toys((self.coordinator.data or {}).get("toys", {}))
        return toys.get(self.toy_id, {})

    @property
    def available(self) -> bool:
        """Return whether the toy is listed and connected to Lovense Remote."""
        toy = self.toy
        return super().available and bool(toy) and is_device_connected(toy)
