"""Refresh button; never reboots or changes a VPS."""
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .entity import VDSinaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([VDSinaRefreshButton(hass.data[DOMAIN][entry.entry_id])])


class VDSinaRefreshButton(VDSinaEntity, ButtonEntity):
    _attr_name = "Обновить данные"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.entry.unique_id}_refresh"

    @property
    def available(self):
        # Allow recovery by pressing refresh after a temporary network failure.
        return True

    async def async_press(self):
        await self.coordinator.async_request_refresh()
